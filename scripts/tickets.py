"""Bilete: generare, decontare, invatare si istoric.

Exemple:
    python scripts/tickets.py --next                 # bilete pentru ziua urmatoare
    python scripts/tickets.py --day 2026-08-30       # bilete pentru o zi anume
    python scripts/tickets.py --settle               # verifica biletele deschise
    python scripts/tickets.py --learn                # recalibreaza din rezultate
    python scripts/tickets.py --backfill 20          # istoric onest, fara scurgeri
    python scripts/tickets.py --report               # acuratete si profit

Fluxul normal, zilnic:
    python scripts/update.py            # aduce rezultatele noi
    python scripts/tickets.py --settle --learn
    python scripts/tickets.py --next
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pandas as pd  # noqa: E402

from predictiescor.modeling.pipeline import PredictionPipeline  # noqa: E402
from predictiescor.storage.db import Database  # noqa: E402
from predictiescor.tickets import (  # noqa: E402
    TicketGenerator, accuracy_report, learn_calibration, load_calibration,
    matches_on, next_match_days, overall_summary, recent_match_days,
    settle_tickets, strategy_performance, surprising_legs, tickets_frame,
    update_strategy_weights,
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Bilete SuperLiga")
    p.add_argument("--day", help="ziua pentru care generam (YYYY-MM-DD)")
    p.add_argument("--next", action="store_true",
                   help="genereaza pentru urmatoarea zi cu meciuri")
    p.add_argument("--count", type=int, default=8,
                   help="cate bilete (5-10 recomandat)")
    p.add_argument("--stake", type=float, default=10.0, help="miza per bilet")
    p.add_argument("--settle", action="store_true",
                   help="deconteaza biletele ale caror meciuri s-au jucat")
    p.add_argument("--learn", action="store_true",
                   help="recalculeaza calibrarea si greutatile strategiilor")
    p.add_argument("--backfill", type=int, metavar="N",
                   help="genereaza si deconteaza bilete pentru ultimele N zile "
                        "cu meciuri, antrenand modelul DOAR pe date anterioare")
    p.add_argument("--report", action="store_true",
                   help="afiseaza acuratetea si profitul")
    p.add_argument("--clear", action="store_true",
                   help="sterge toate biletele (reporneste invatarea)")
    p.add_argument("--verbose", "-v", action="store_true")
    return p.parse_args()


def print_ticket(index: int, ticket) -> None:
    print(f"\n  ┌─ Bilet {index}: {ticket.label}")
    for leg in ticket.legs:
        hour = leg.kickoff[11:16] if len(leg.kickoff) > 15 else "  :  "
        print(f"  │  {hour}  {leg.match_label}")
        print(f"  │         {leg.description:<46} "
              f"{leg.probability:>6.1%}   cotă {leg.odds:.2f}")
    print(f"  └─ cotă totală {ticket.total_odds:.2f} · șansă {ticket.probability:.1%} "
          f"· miză {ticket.stake:.0f} → câștig potențial "
          f"{ticket.potential_payout:.2f}")


def generate(db, pipeline, day: str, count: int, stake: float) -> int:
    matches = matches_on(db, day)
    if matches.empty:
        print(f"Nu sunt meciuri pe {day}.")
        return 0

    print(f"\n=== Bilete pentru {day} ({len(matches)} meciuri) ===")
    for row in matches.itertuples():
        hour = str(row.utc_date)[11:16]
        print(f"  {hour}  {row.home_team} — {row.away_team}")

    generator = TicketGenerator(pipeline, db, calibrator=load_calibration(db),
                                stake=stake)
    tickets = generator.build(matches, count=count, match_day=day)
    if not tickets:
        print("Nu am putut construi bilete (prea puține selecții eligibile).")
        return 0

    for i, ticket in enumerate(tickets, 1):
        print_ticket(i, ticket)

    ids = generator.save(tickets)
    print(f"\n{len(ids)} bilete salvate în baza de date.")
    return len(ids)


def backfill(db, days: int, count: int, stake: float) -> None:
    """Reface istoricul biletelor fara sa se uite in viitor.

    Pentru fiecare zi din trecut, modelul este reantrenat folosind exclusiv
    meciurile jucate INAINTE de acea zi. Doar asa acuratetea raportata este
    reala; daca am antrena pe tot istoricul, modelul ar fi "vazut" chiar
    meciurile pe care le pariaza.
    """
    match_days = sorted(recent_match_days(db, days * 2))[-days:]
    if not match_days:
        print("Nu există zile cu meciuri jucate.")
        return

    all_matches = db.matches_df(finished_only=True)
    print(f"Backfill onest pe {len(match_days)} zile "
          f"({match_days[0]} → {match_days[-1]}).")

    created = 0
    for day in match_days:
        cutoff = pd.Timestamp(day, tz="UTC")
        history = all_matches[all_matches["date"] < cutoff]
        if len(history) < 150:
            print(f"  {day}: sar peste (doar {len(history)} meciuri anterioare)")
            continue

        pipeline = PredictionPipeline(db=db)
        pipeline.matches = history
        try:
            from predictiescor.modeling.dixon_coles import DixonColesModel
            pipeline.goals = DixonColesModel.from_config(
                pipeline.cfg.model).fit(history)
        except Exception as exc:  # noqa: BLE001
            print(f"  {day}: antrenare eșuată ({exc})")
            continue

        # statisticile secundare si jucatorii se antreneaza pe ce e disponibil
        _train_side_models(pipeline, cutoff)

        matches = matches_on(db, day)
        if matches.empty:
            continue
        generator = TicketGenerator(pipeline, db,
                                    calibrator=load_calibration(db), stake=stake)
        tickets = generator.build(matches, count=count, match_day=day)
        ids = generator.save(tickets)
        created += len(ids)

        settle_tickets(db, match_day=day)
        learn_calibration(db)
        update_strategy_weights(db)
        print(f"  {day}: {len(ids)} bilete, model antrenat pe "
              f"{len(history)} meciuri anterioare")

    print(f"\nTotal: {created} bilete generate și decontate.")


def _train_side_models(pipeline, cutoff: pd.Timestamp) -> None:
    """Antreneaza modelele secundare doar pe date de dinaintea `cutoff`."""
    from predictiescor.modeling.players import PlayerModel
    from predictiescor.modeling.secondary import SecondaryStatsModel

    stats = pipeline.db.team_stats_df()
    if not stats.empty:
        stats = stats[pd.to_datetime(stats["date"], utc=True) < cutoff]
        if len(stats) >= 40:
            try:
                pipeline.secondary = SecondaryStatsModel.from_config(
                    pipeline.cfg.model).fit(stats)
            except Exception:  # noqa: BLE001
                pipeline.secondary = None

    season_stats = pipeline.db.player_season_stats_df()
    if not season_stats.empty:
        try:
            pipeline.players = PlayerModel.from_config(
                pipeline.cfg.model).fit_from_season_stats(season_stats)
        except Exception:  # noqa: BLE001
            pipeline.players = None


def report(db) -> None:
    summary = overall_summary(db)
    print("\n=== Rezultatul biletelor ===")
    if summary["bilete"] == 0:
        print("Niciun bilet decontat încă.")
        return
    print(f"  bilete decontate : {summary['bilete']}")
    print(f"  câștigate        : {summary['castigate']} "
          f"({summary['rata %']:.1f}%)")
    print(f"  profit           : {summary['profit']:+.2f}")
    print(f"  ROI              : {summary['ROI %']:+.1f}%")

    performance = strategy_performance(db)
    if not performance.empty:
        print("\n=== Pe strategii ===")
        columns = ["strategy", "tickets", "won", "rată bilete %",
                   "rată picioare %", "ROI %", "weight"]
        print(performance[columns].to_string(index=False))

    accuracy = accuracy_report(db)
    if "pe_piata" in accuracy:
        print("\n=== Acuratețe pe piețe ===")
        print(accuracy["pe_piata"].to_string(index=False))
    if "anulate" in accuracy:
        print("\n=== Picioare anulate (date lipsă) ===")
        print(accuracy["anulate"].to_string(index=False))

    surprises = surprising_legs(db, limit=10)
    if not surprises.empty:
        print("\n=== Cele mai mari surprize ===")
        print(surprises.to_string(index=False))


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s")
    db = Database()

    if args.clear:
        with db.connect() as conn:
            conn.execute("DELETE FROM ticket_legs")
            conn.execute("DELETE FROM tickets")
            conn.execute("DELETE FROM market_calibration")
            conn.execute("DELETE FROM strategy_performance")
        print("Toate biletele și ce s-a învățat din ele au fost șterse.")

    if args.settle:
        print("Decontare:", settle_tickets(db))

    if args.backfill:
        backfill(db, args.backfill, args.count, args.stake)

    if args.day or args.next:
        pipeline = PredictionPipeline(db=db).train()
        if not pipeline.ready:
            print("Modelele nu sunt antrenate:")
            for warning in pipeline.warnings:
                print("  -", warning)
            return 1
        day = args.day
        if not day:
            upcoming = next_match_days(db, 1)
            if not upcoming:
                print("Nu există meciuri programate în baza de date. "
                      "Rulează: python scripts/update.py")
                return 1
            day = upcoming[0]
        generate(db, pipeline, day, args.count, args.stake)

    if args.learn:
        calibrator = learn_calibration(db)
        weights = update_strategy_weights(db)
        print(f"\nCalibrare actualizată: {len(calibrator.table)} combinații "
              f"piață × interval.")
        if not weights.empty:
            print("Greutăți strategii:")
            print(weights[["strategy", "tickets", "roi", "weight"]]
                  .to_string(index=False))

    if args.report:
        report(db)

    if not any([args.day, args.next, args.settle, args.learn, args.backfill,
                args.report, args.clear]):
        print(__doc__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
