"""Descarca si actualizeaza datele SuperLiga din toate sursele configurate.

Exemple:
    python scripts/fetch_data.py                      # toate sursele, toate sezoanele
    python scripts/fetch_data.py --sources footballdata
    python scripts/fetch_data.py --seasons 2023 2024
    python scripts/fetch_data.py --stats-only --budget 80
    python scripts/fetch_data.py --upcoming          # doar meciurile care urmeaza

Ruleaza-l periodic: API-Football are 100 de cereri pe zi, iar statisticile se
cer per meci, deci baza se completeaza in cateva zile de rulari succesive.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

if hasattr(sys.stdout, "reconfigure"):  # diacritice pe consola Windows
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from predictiescor.config import get_config  # noqa: E402
from predictiescor.data.aggregator import DataAggregator  # noqa: E402
from predictiescor.data.apifootball import (  # noqa: E402
    ApiFootballProvider, RateLimitedError,
)
from predictiescor.storage.db import Database  # noqa: E402


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Ingestie date SuperLiga")
    p.add_argument("--seasons", type=int, nargs="*", default=None,
                   help="sezoane de descarcat (implicit: cele din config.yaml)")
    p.add_argument("--sources", nargs="*", default=None,
                   help="footballdata apifootball transfermarkt sofascore fbref")
    p.add_argument("--upcoming", action="store_true",
                   help="doar meciurile programate")
    p.add_argument("--stats-only", action="store_true",
                   help="doar statistici detaliate pe meci (API-Football)")
    p.add_argument("--budget", type=int, default=None,
                   help="cereri API-Football permise in aceasta rulare")
    p.add_argument("--with-players", action="store_true",
                   help="descarca si statistici individuale (dubleaza costul)")
    p.add_argument("--verbose", "-v", action="store_true")
    return p.parse_args()


def fetch_match_stats(budget: int, with_players: bool, seasons: list[int]) -> None:
    """Completeaza statisticile lipsa, cele mai recente meciuri intai."""
    cfg = get_config()
    db = Database()
    provider = ApiFootballProvider(db=db, cfg=cfg, max_requests=budget)
    if not provider.available:
        print("API-Football nu are cheie configurata (.env: APIFOOTBALL_KEY)")
        return

    targets: list[tuple[str, int]] = []
    for season in seasons:
        if provider.supports_season(season):
            targets.extend(provider._fixtures_missing_stats(season))

    if not targets:
        print("Toate meciurile accesibile au deja statistici.")
        return

    print(f"{len(targets)} meciuri fara statistici; buget {budget} cereri.")
    done = teams = players = 0
    for ext_id, match_id in targets:
        cost = 2 if with_players else 1
        if provider.budget_left < cost:
            break
        try:
            teams += provider.fetch_match_statistics(ext_id, match_id)
            if with_players:
                players += provider.fetch_match_players(ext_id, match_id)
            done += 1
            if done % 10 == 0:
                print(f"  {done} meciuri procesate "
                      f"({provider.requests_used} cereri folosite)")
        except RateLimitedError as exc:
            print(f"  Limita de cereri atinsa, ma opresc aici: {exc}")
            break
        except Exception as exc:  # noqa: BLE001
            print(f"  meci {ext_id}: {exc}")
            if "buget" in str(exc):
                break

    db.log_ingest("apifootball", "statistici meci", teams + players,
                  "ok", f"{done} meciuri")
    print(f"\nGata: {done} meciuri, {teams} randuri statistici echipa, "
          f"{players} randuri jucatori. Cereri folosite: {provider.requests_used}.")


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    cfg = get_config()
    seasons = args.seasons or cfg.seasons
    db = Database()

    if args.stats_only:
        budget = args.budget or cfg.league.get("apifootball_daily_budget", 90)
        fetch_match_stats(budget, args.with_players, seasons)
    else:
        agg = DataAggregator(db=db, cfg=cfg)
        print("Surse configurate:")
        icons = {"activ": "OK ", "blocat": "BLOCAT", "oprit din .env": "OPRIT"}
        for row in agg.status():
            mark = icons.get(row["stare"], row["stare"])
            note = f"  ({row['observatii']})" if row.get("observatii") else ""
            print(f"  [{mark:<6}] {row['sursa']:<14} {row['ofera']}{note}")
        print()

        if args.upcoming:
            results = agg.refresh_upcoming()
        else:
            results = agg.refresh(seasons, sources=args.sources)

        print("\nRezultate:")
        for res in results.values():
            print("  " + res.describe())

    print("\nContinutul bazei de date:")
    for key, value in db.summary().items():
        print(f"  {key:<24} {value}")

    orphans = db.orphan_teams()
    if not orphans.empty:
        print("\nATENTIE - echipe fara meciuri (variante de nume nemapate):")
        print(orphans.to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
