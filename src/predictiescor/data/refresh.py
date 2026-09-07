"""Actualizarea datelor - logica folosita si de `scripts/update.py`, si de buton.

Modulul exista ca sa nu avem doua implementari care se despart in timp: si
linia de comanda, si butonul din dashboard apeleaza `run_refresh`.

Fiecare pas raporteaza separat daca a mers sau nu. Un pas cazut nu opreste
restul: daca Transfermarkt e indisponibil, rezultatele de la football-data tot
intra in baza.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Any, Callable

from ..config import Config, get_config
from ..storage.db import Database
from .apifootball import ApiFootballProvider, RateLimitedError
from .flashscore import FlashscoreProvider
from .footballdata import FootballDataProvider
from .lpf import LpfProvider
from .news import NewsProvider
from .transfermarkt import TransfermarktProvider

log = logging.getLogger(__name__)

#: semnatura functiei de progres: (indice pas, total pasi, eticheta)
ProgressFn = Callable[[int, int, str], None]

OK, FAILED, SKIPPED = "ok", "eșuat", "sărit"

#: Cat timp ramane proaspat un pas scump, in ore.
#:
#: Pasii ieftini (rezultate, scor live, calendar, stiri) ruleaza mereu - dureaza
#: secunde si aduc exact lucrurile care se schimba des. Cei de mai jos costa
#: minute intregi, iar reluarea lor la fiecare apasare n-ar aduce nimic:
#: loturile nu se schimba de doua ori pe zi, iar cotele nu se misca in cinci
#: minute cat sa merite inca sase minute de cereri.
FRESH_HOURS: dict[str, float] = {
    "Cote pre-meci (Flashscore)": 3.0,
    "Loturi și jucători": 12.0,
    "Statistici detaliate pe meci": 6.0,
}

#: cheia sub care tinem minte cand a mers ultima oara fiecare pas
_LAST_RUN_KEY = "refresh_last_ok"


def _last_success(db: Database) -> dict[str, str]:
    with db.connect() as conn:
        row = conn.execute("SELECT value FROM meta WHERE key = ?",
                           (_LAST_RUN_KEY,)).fetchone()
    if not row or not row["value"]:
        return {}
    import json
    try:
        return dict(json.loads(str(row["value"])))
    except (ValueError, TypeError):
        return {}


def _mark_success(db: Database, step: str) -> None:
    import json
    
    done = _last_success(db)
    done[step] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO meta(key, value) VALUES (?,?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (_LAST_RUN_KEY, json.dumps(done)))


@dataclass
class RefreshStep:
    name: str
    status: str = OK
    detail: str = ""
    seconds: float = 0.0

    @property
    def icon(self) -> str:
        return {OK: "✅", FAILED: "⚠️", SKIPPED: "➖"}.get(self.status, "•")


@dataclass
class RefreshReport:
    steps: list[RefreshStep] = field(default_factory=list)
    summary: dict[str, int] = field(default_factory=dict)
    seconds: float = 0.0

    @property
    def ok(self) -> bool:
        return all(step.status != FAILED for step in self.steps)

    @property
    def failures(self) -> list[RefreshStep]:
        return [s for s in self.steps if s.status == FAILED]

    def as_rows(self) -> list[dict[str, Any]]:
        return [
            {"pas": s.name, "stare": f"{s.icon} {s.status}",
             "detalii": s.detail, "durată (s)": round(s.seconds, 1)}
            for s in self.steps
        ]

    def describe(self) -> str:
        return " | ".join(f"{s.icon} {s.name}: {s.detail}" for s in self.steps)


def run_refresh(
    db: Database | None = None,
    cfg: Config | None = None,
    *,
    quick: bool = False,
    with_players: bool = True,
    api_budget: int | None = None,
    with_tickets: bool = False,
    force_all: bool = False,
    progress: ProgressFn | None = None,
) -> RefreshReport:
    """Aduce datele noi din toate sursele active si intoarce un raport.

    `quick=True` face doar pasii ieftini (rezultate + calendar, ~5 secunde).
    Restul pasilor pot dura minute intregi, din cauza limitelor API-Football.
    """
    cfg = cfg or get_config()
    db = db or Database()
    report = RefreshReport()
    started = time.monotonic()
    season = cfg.current_season

    steps: list[str] = ["Rezultate și cote", "Scor live (Flashscore)",
                        "Calendar și rezultate live",
                        "Statistici oficiale LPF",
                        "Cornere și cartonașe (Flashscore)", "Știri"]
    if not quick:
        steps.append("Cote pre-meci (Flashscore)")
    if not quick:
        if with_players:
            steps.append("Loturi și jucători")
        steps.append("Statistici detaliate pe meci")
    steps.extend(["Curățare", "Predicții înregistrate", "Bilete"])
    total = len(steps)
    index = 0

    def announce(label: str) -> None:
        nonlocal index
        index += 1
        if progress:
            progress(index, total, label)

    class Skip(Exception):
        """Pasul nu are ce face - nu e o eroare."""

    done_at = _last_success(db)

    def _is_fresh(name: str) -> str | None:
        """Cat de recent a mers pasul, daca e prea recent ca sa merite reluat."""
        window = FRESH_HOURS.get(name)
        if not window or force_all:
            return None
        stamp = done_at.get(name)
        if not stamp:
            return None
        try:
            when = datetime.fromisoformat(stamp)
        except ValueError:
            return None
        age = (datetime.now(timezone.utc) - when).total_seconds() / 3600
        if age >= window:
            return None
        minutes = int(age * 60)
        return (f"proaspăt (acum {minutes} min)" if minutes < 90
                else f"proaspăt (acum {age:.1f} ore)")

    def record(name: str, fn: Callable[[], str]) -> None:
        announce(name)
        step = RefreshStep(name)
        clock = time.monotonic()
        fresh = _is_fresh(name)
        if fresh:
            step.status = SKIPPED
            step.detail = fresh
            report.steps.append(step)
            return
        try:
            step.detail = fn()
            if name in FRESH_HOURS:
                _mark_success(db, name)
        except Skip as exc:
            step.status = SKIPPED
            step.detail = str(exc)
        except Exception as exc:  # noqa: BLE001
            step.status = FAILED
            step.detail = str(exc)[:300]
            log.exception("pasul '%s' a eșuat", name)
        step.seconds = time.monotonic() - clock
        report.steps.append(step)

    # ------------------------------------------------------------------
    def football_data() -> str:
        provider = FootballDataProvider(db=db, cfg=cfg)
        # fortam redescarcarea: e sursa cea mai proaspata de rezultate
        provider.load_csv(refresh=True)
        result = provider.fetch_season(season)
        return f"{result.matches} meciuri, {result.odds} cote"

    record("Rezultate și cote", football_data)

    # ------------------------------------------------------------------
    def flashscore() -> str:
        provider = FlashscoreProvider(db=db, cfg=cfg)
        if not provider.available:
            raise Skip("oprit din .env (FLASHSCORE_ENABLED)")
        feed = provider.fetch_feed()
        result = provider.ingest(feed, season)
        corrected = provider.correct_kickoff_times()
        live = sum(1 for m in feed if m.is_live)
        parts = [f"{result.matches} rezultate"]
        if live:
            parts.append(f"{live} meciuri ÎN DESFĂȘURARE")
        if corrected:
            parts.append(f"{corrected} ore de start corectate")
        return ", ".join(parts)

    record("Scor live (Flashscore)", flashscore)

    # ------------------------------------------------------------------
    def schedule() -> str:
        provider = TransfermarktProvider(db=db, cfg=cfg)
        result = provider.fetch_schedule(season, ttl_hours=1.0)
        if result.errors:
            raise RuntimeError(result.errors[0])
        return f"{result.matches} meciuri în calendar"

    record("Calendar și rezultate live", schedule)

    # ------------------------------------------------------------------
    def lpf_stats() -> str:
        provider = LpfProvider(db=db, cfg=cfg)
        if not provider.available:
            raise Skip("oprit din .env (LPF_ENABLED)")
        result = provider.fetch_season(season)
        skipped = getattr(provider, "skipped_without_score", 0)
        if result.matches == 0:
            return ("nimic nou de adus"
                    + (f" ({skipped} meciuri încă fără scor)" if skipped else ""))
        return (f"{result.matches} meciuri îmbogățite, "
                f"{result.team_stats} rânduri de statistici"
                + (f"; {skipped} încă fără scor" if skipped else ""))

    record("Statistici oficiale LPF", lpf_stats)

    # ------------------------------------------------------------------
    def flashscore_stats() -> str:
        provider = FlashscoreProvider(db=db, cfg=cfg)
        if not provider.available:
            raise Skip("oprit din .env (FLASHSCORE_ENABLED)")
        result = provider.ingest_stats(season)
        if not result.matches:
            return "nimic de completat"
        return (f"{result.matches} meciuri, cornere și cartonașe"
                + (f"; {len(result.errors)} erori" if result.errors else ""))

    record("Cornere și cartonașe (Flashscore)", flashscore_stats)

    # ------------------------------------------------------------------
    def news() -> str:
        provider = NewsProvider(db=db, cfg=cfg)
        if not provider.available:
            raise Skip("oprit din .env (NEWS_ENABLED)")
        result = provider.refresh()
        if not result.matches:
            return "nimic nou de citit"
        summary = db.news_summary()
        sure = summary["pe_incredere"].get("confirmat", 0)
        return (f"{result.matches} articole, {result.player_stats} fapte "
                f"({sure} confirmate în total)")

    record("Știri", news)

    # ------------------------------------------------------------------
    if not quick:
        def prematch_odds() -> str:
            """Cotele meciurilor care urmeaza - singura sursa pre-meci.

            Nu ruleaza in modul rapid: un meci costa `case x piete` cereri,
            deci o rulare completa dureaza minute intregi.
            """
            provider = FlashscoreProvider(db=db, cfg=cfg)
            if not provider.odds_available:
                raise Skip("oprit din .env (FLASHSCORE_ODDS)")
            result = provider.fetch_odds()
            if result.errors:
                raise RuntimeError(result.errors[0])
            if not result.matches:
                return "niciun meci nou cu cote publicate"
            return (f"{result.matches} meciuri, {result.odds} cote "
                    f"(case + medie + cel mai bun preț)")

        record("Cote pre-meci (Flashscore)", prematch_odds)

    # ------------------------------------------------------------------
    if not quick and with_players:
        def players() -> str:
            provider = TransfermarktProvider(db=db, cfg=cfg)
            clubs = provider.fetch_clubs(season)
            written = 0
            absent = 0
            transferuri = 0
            for name, club_id, slug in clubs:
                team_id = db.upsert_team(name, source=provider.name,
                                         ext_id=club_id)
                written += provider.fetch_club_players(
                    club_id, slug, season, team_id, fresh=True)
                try:
                    transferuri += provider.fetch_club_transfers(
                        club_id, slug, season, team_id, fresh=True)
                except Exception as exc:  # noqa: BLE001
                    log.debug("transferuri %s: %s", name, exc)
                # Absentele nu se aduceau deloc din refresh - se actualizau doar
                # la ingestia completa a sezonului, deci ramaneau vechi.
                try:
                    absent += provider.fetch_absences(club_id, slug, team_id)
                except Exception as exc:  # noqa: BLE001
                    log.debug("absente %s: %s", name, exc)

            # Transfermarkt publica doar starea de acum. Fotografia de aici e
            # singurul mod in care vom putea vreodata masura daca absentele
            # ajuta modelul: peste cateva luni avem istoric, acum nu avem.
            db.snapshot_availability(source=provider.name)
            # Jucatorii pe care Transfermarkt nu-i cunoaste (adusi de LPF, cu
            # numele legal complet) isi iau clubul din ultima aparitie.
            mutati = db.sync_teams_from_appearances(source=provider.name)
            return (f"{len(clubs)} cluburi, {written} jucători, "
                    f"{absent} indisponibili (fotografiați), "
                    f"{transferuri} transferuri"
                    + (f", {mutati} cluburi corectate după apariții"
                       if mutati else ""))

        record("Loturi și jucători", players)

    # ------------------------------------------------------------------
    if not quick:
        def match_stats() -> str:
            budget = api_budget if api_budget is not None else int(
                cfg.league.get("apifootball_daily_budget", 90))
            if budget <= 0:
                raise Skip("buget de cereri pus pe zero")
            api = ApiFootballProvider(db=db, cfg=cfg, max_requests=budget)
            if not api.available:
                raise Skip("APIFOOTBALL_KEY lipsește din .env")

            targets: list[tuple[str, int]] = []
            for candidate in cfg.seasons:
                if api.supports_season(candidate):
                    targets.extend(api._fixtures_missing_stats(candidate))
            if not targets:
                return "toate meciurile accesibile au deja statistici"

            done = 0
            limit_hit = ""
            for ext_id, match_id in targets:
                if api.budget_left < 1:
                    break
                try:
                    api.fetch_match_statistics(ext_id, match_id)
                    done += 1
                except RateLimitedError as exc:
                    limit_hit = f" (limită atinsă: {exc})"
                    break
                except Exception as exc:  # noqa: BLE001
                    log.debug("meci %s: %s", ext_id, exc)
                    if "buget" in str(exc):
                        break
            remaining = len(targets) - done
            return (f"{done} meciuri completate cu {api.requests_used} cereri; "
                    f"{remaining} rămân pentru rulările viitoare{limit_hit}")

        record("Statistici detaliate pe meci", match_stats)

    # ------------------------------------------------------------------
    def cleanup() -> str:
        result = db.deduplicate()
        return (f"{result['echipe_unite']} echipe unite, "
                f"{result['meciuri_unite']} meciuri unite")

    record("Curățare", cleanup)

    # ------------------------------------------------------------------
    def record_forecasts() -> str:
        """Scrie in jurnal ce prezice modelul, INAINTE de meciuri."""
        from ..modeling.pipeline import PredictionPipeline
        from ..modeling.tracking import record_predictions

        pipeline = PredictionPipeline(db=db, cfg=cfg).train()
        if not pipeline.ready:
            raise Skip("modelul nu s-a putut antrena")
        saved = record_predictions(pipeline, db)
        if not saved:
            return "nimic nou de înregistrat"
        return f"{saved} predicții salvate pentru meciurile care urmează"

    record("Predicții înregistrate", record_forecasts)

    # ------------------------------------------------------------------
    def tickets() -> str:
        from ..tickets import (
            learn_calibration, settle_tickets, update_strategy_weights,
        )
        settled = settle_tickets(db)
        parts = [f"{settled['decontate']} decontate",
                 f"{settled['in_asteptare']} în așteptare"]
        if settled["decontate"]:
            learn_calibration(db)
            update_strategy_weights(db)
            parts.append("calibrare actualizată")

        if with_tickets:
            parts.append(_generate_next_tickets(db, cfg))
        return ", ".join(parts)

    record("Bilete", tickets)

    # ------------------------------------------------------------------
    report.summary = db.summary()
    report.seconds = time.monotonic() - started
    return report


def _generate_next_tickets(db: Database, cfg: Config) -> str:
    from ..modeling.pipeline import PredictionPipeline
    from ..tickets import (
        TicketGenerator, bettable, load_calibration, matches_on,
        next_match_days,
    )

    # Prima zi care mai are meciuri care INCA NU AU INCEPUT. Ziua de azi apare
    # in lista chiar daca meciurile ei s-au disputat deja sau sunt in
    # desfasurare; un "pont" pe un meci intrat in minutul 65 nu e o predictie.
    
    day, matches = None, None
    for candidate in next_match_days(db, 5):
        upcoming = bettable(matches_on(db, candidate))
        if len(upcoming) >= 1:
            day, matches = candidate, upcoming
            break
    if day is None:
        return "niciun meci viitor pentru bilete"

    pipeline = PredictionPipeline(db=db, cfg=cfg).train()
    if not pipeline.ready:
        return "modelul nu s-a putut antrena pentru bilete"

    generator = TicketGenerator(pipeline, db, calibrator=load_calibration(db))
    tickets = generator.build(matches, count=8, match_day=day)
    ids = generator.save(tickets)
    if not ids:
        return (f"niciun bilet pentru {day} "
                f"({len(matches)} meciuri viitoare - prea puține)")
    return f"{len(ids)} bilete generate pentru {day} ({len(matches)} meciuri)"
