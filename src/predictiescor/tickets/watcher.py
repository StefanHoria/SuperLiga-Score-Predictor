"""Verificarea automata a biletelor dupa fiecare meci.

Ideea: nu are rost sa interogam sursele la fiecare cateva minute "orbeste".
Stim programul, deci stim si cand are sens sa verificam - la circa doua ore
dupa fluierul de start, cand meciul s-a incheiat si sursele au apucat sa
publice rezultatul.

`due_matches` raspunde la intrebarea "ce meci ar trebui sa fie gata, dar noi
inca nu avem rezultatul (sau mai avem bilete deschise pe el)?". Daca lista e
goala, ciclul nu face nicio cerere de retea si se termina instant.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd

from ..config import Config, get_config
from ..storage.db import Database

log = logging.getLogger(__name__)

#: cat dureaza un meci, cu pauza si prelungiri, plus marja pentru publicare
#
# Marja era de 2h15 pe vremea cand rezultatul venea de la football-data, care
# publica de doua ori pe saptamana. Flashscore are scorul final in secunda in
# care se fluiera, deci putem verifica mai devreme: 45+15+45 de minute plus
# prelungirile inseamna ca un meci nu se poate incheia mai repede de ~1h50.
MATCH_DURATION = timedelta(hours=1, minutes=50)

#: nu reinterogam sursele mai des de atat pentru acelasi meci neincheiat
RETRY_AFTER = timedelta(minutes=20)

#: cheia sub care tinem minte ultima verificare
LAST_CHECK_KEY = "watch_last_check"

#: Stirile au ritmul lor, independent de meciuri.
#:
#: Ciclul de verificare a biletelor nu ajunge la fluxuri decat cand lipseste un
#: rezultat - si nici atunci intotdeauna, fiindca Flashscore il aduce de obicei
#: pe calea rapida. In practica insemna ca o accidentare anuntata la pranz, in
#: ziua meciului de seara, nu intra in baza decat daca apasai butonul.
NEWS_KEY = "watch_last_news"
NEWS_EVERY = timedelta(hours=1)


@dataclass
class WatchResult:
    """Ce a facut un ciclu de verificare."""

    checked_at: datetime
    due: list[dict[str, Any]] = field(default_factory=list)
    refreshed: bool = False
    settled: int = 0
    still_open: int = 0
    newly_finished: int = 0
    news: int = 0
    message: str = ""

    @property
    def did_work(self) -> bool:
        return self.refreshed or self.settled > 0 or self.news > 0

    def describe(self) -> str:
        if not self.due:
            return "nimic de verificat"
        matches = ", ".join(m["label"] for m in self.due[:3])
        if len(self.due) > 3:
            matches += f" (+{len(self.due) - 3})"
        parts = [f"{len(self.due)} meciuri de verificat: {matches}"]
        if self.newly_finished:
            parts.append(f"{self.newly_finished} rezultate noi")
        if self.settled:
            parts.append(f"{self.settled} bilete decontate")
        if self.still_open:
            parts.append(f"{self.still_open} raman deschise")
        if self.news:
            parts.append(f"{self.news} fapte noi din presa")
        return " | ".join(parts)


def due_matches(db: Database, *, now: datetime | None = None,
                grace: timedelta = MATCH_DURATION) -> list[dict[str, Any]]:
    """Meciurile care ar trebui sa fie incheiate, dar nu sunt inca decontate.

    Un meci intra in lista daca a inceput cu cel putin `grace` in urma si:
      * nu are inca rezultat in baza, SAU
      * mai exista bilete deschise cu ponturi pe el.
    """
    now = now or datetime.now(timezone.utc)
    cutoff = (now - grace).isoformat()

    # Conditia se uita la starea PONTULUI, nu a biletului: un bilet poate
    # ramane deschis din cauza altui meci, iar meciul asta sa fie deja
    # decontat - nu are rost sa-l reverificam.
    sql = """
        SELECT m.match_id, m.utc_date, ht.name AS home, at.name AS away,
               m.home_goals, m.status,
               (SELECT COUNT(*) FROM ticket_legs l
                 WHERE l.match_id = m.match_id AND l.result = 'deschis'
               ) AS open_legs
        FROM matches m
        JOIN teams ht ON ht.team_id = m.home_team_id
        JOIN teams at ON at.team_id = m.away_team_id
        WHERE m.utc_date <= ?
          AND (m.home_goals IS NULL OR EXISTS (
                SELECT 1 FROM ticket_legs l
                 WHERE l.match_id = m.match_id AND l.result = 'deschis'))
        ORDER BY m.utc_date DESC
        LIMIT 40
    """
    with db.connect() as conn:
        rows = conn.execute(sql, (cutoff,)).fetchall()

    out: list[dict[str, Any]] = []
    for row in rows:
        # meciurile foarte vechi fara rezultat nu mai au sanse sa apara
        kickoff = pd.to_datetime(row["utc_date"], utc=True, errors="coerce")
        if kickoff is pd.NaT:
            continue
        if row["home_goals"] is None and (now - kickoff.to_pydatetime()
                                          ) > timedelta(days=7):
            continue
        out.append({
            "match_id": int(row["match_id"]),
            "label": f"{row['home']} - {row['away']}",
            "kickoff": row["utc_date"],
            "has_result": row["home_goals"] is not None,
            "open_legs": int(row["open_legs"] or 0),
        })
    return out


def _read_meta(db: Database, key: str) -> str | None:
    with db.connect() as conn:
        row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return str(row["value"]) if row else None


def _write_meta(db: Database, key: str, value: str) -> None:
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO meta(key, value) VALUES (?,?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value))


def run_cycle(db: Database | None = None, cfg: Config | None = None,
              *, force: bool = False,
              now: datetime | None = None) -> WatchResult:
    """Un singur ciclu de verificare. Ieftin cand nu e nimic de facut."""
    cfg = cfg or get_config()
    db = db or Database()
    now = now or datetime.now(timezone.utc)
    result = WatchResult(checked_at=now)

    # Stirile intai: sunt ieftine (patru cereri) si nu depind de meciuri.
    result.news = _refresh_news(db, cfg, now=now, force=force)

    result.due = due_matches(db, now=now)
    if not result.due:
        _write_meta(db, LAST_CHECK_KEY, now.isoformat(timespec="seconds"))
        result.message = ("niciun meci de verificat"
                          + (f"; {result.news} fapte noi din presă"
                             if result.news else ""))
        return result

    # Daca toate meciurile au deja rezultat, e de ajuns sa decontam: nu mai
    # interogam sursele degeaba.
    missing_results = [m for m in result.due if not m["has_result"]]

    if missing_results and not force:
        last = _read_meta(db, LAST_CHECK_KEY)
        if last:
            try:
                previous = datetime.fromisoformat(last)
                if now - previous < RETRY_AFTER:
                    result.message = (
                        f"am verificat acum {int((now - previous).total_seconds() // 60)}"
                        f" minute; astept")
                    return result
            except ValueError:
                pass

    if missing_results or force:
        before = _finished_count(db)

        # Intai incercam ieftin: o singura cerere la Flashscore. De cele mai
        # multe ori e de ajuns, si atunci sarim peste refresh-ul complet - care
        # descarca un CSV intreg si mai interogheaza doua site-uri.
        try:
            _flashscore_pass(db, cfg)
            result.refreshed = True
        except Exception as exc:  # noqa: BLE001
            log.warning("Flashscore nu a raspuns: %s", exc)

        still_missing = _without_results(
            db, [m["match_id"] for m in missing_results])

        if still_missing or force:
            from ..data.refresh import run_refresh

            report = run_refresh(db, cfg, quick=True)
            result.refreshed = True
            if not report.ok:
                result.message = "; ".join(
                    s.detail for s in report.failures)[:200]

        result.newly_finished = max(0, _finished_count(db) - before)

    from .learning import learn_calibration, update_strategy_weights
    from .settlement import settle_tickets

    outcome = settle_tickets(db)
    result.settled = int(outcome["decontate"])
    result.still_open = int(outcome["in_asteptare"])

    if result.settled:
        learn_calibration(db)
        update_strategy_weights(db)

    _write_meta(db, LAST_CHECK_KEY, now.isoformat(timespec="seconds"))
    return result


def _refresh_news(db: Database, cfg: Config, *, now: datetime,
                  force: bool = False) -> int:
    """Citeste fluxurile de stiri, cel mult o data pe ora.

    Ruleaza independent de meciuri: o accidentare se anunta cand se anunta, nu
    dupa fluierul final. Patru cereri HTTP, cateva secunde.
    """
    if not force:
        last = _read_meta(db, NEWS_KEY)
        if last:
            try:
                if now - datetime.fromisoformat(last) < NEWS_EVERY:
                    return 0
            except ValueError:
                pass

    from ..data.news import NewsProvider

    provider = NewsProvider(db=db, cfg=cfg)
    if not provider.available:
        return 0
    try:
        result = provider.refresh()
    except Exception as exc:  # noqa: BLE001
        log.warning("stirile nu s-au putut citi: %s", exc)
        return 0
    _write_meta(db, NEWS_KEY, now.isoformat(timespec="seconds"))
    return int(result.player_stats)


def _flashscore_pass(db: Database, cfg: Config) -> int:
    """Aduce rezultatele incheiate de la Flashscore. O cerere, raspuns instant."""
    from ..data.flashscore import FlashscoreProvider

    provider = FlashscoreProvider(db=db, cfg=cfg)
    if not provider.available:
        return 0
    written = provider.ingest(provider.fetch_feed(), cfg.current_season).matches
    log.info("Flashscore: %d rezultate scrise", written)
    return written


def _without_results(db: Database, match_ids: list[int]) -> list[int]:
    """Care dintre meciuri tot nu au scor in baza."""
    if not match_ids:
        return []
    marks = ",".join("?" * len(match_ids))
    with db.connect() as conn:
        rows = conn.execute(
            f"SELECT match_id FROM matches WHERE match_id IN ({marks})"
            "   AND home_goals IS NULL", match_ids).fetchall()
    return [int(r["match_id"]) for r in rows]


def _finished_count(db: Database) -> int:
    with db.connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) c FROM matches WHERE home_goals IS NOT NULL"
        ).fetchone()
    return int(row["c"])


def next_check_at(db: Database, *, now: datetime | None = None,
                  grace: timedelta = MATCH_DURATION) -> datetime | None:
    """Cand merita urmatoarea verificare: la finalul urmatorului meci."""
    now = now or datetime.now(timezone.utc)
    with db.connect() as conn:
        row = conn.execute(
            "SELECT MIN(utc_date) AS next FROM matches"
            " WHERE home_goals IS NULL AND utc_date > ?",
            ((now - grace).isoformat(),),
        ).fetchone()
    if not row or not row["next"]:
        return None
    kickoff = pd.to_datetime(row["next"], utc=True, errors="coerce")
    if kickoff is pd.NaT:
        return None
    return kickoff.to_pydatetime() + grace


def status(db: Database | None = None) -> dict[str, Any]:
    """Rezumat pentru pagina de bilete: ce se asteapta si cand."""
    db = db or Database()
    now = datetime.now(timezone.utc)
    with db.connect() as conn:
        open_tickets = int(conn.execute(
            "SELECT COUNT(*) c FROM tickets WHERE status='deschis'"
        ).fetchone()["c"])
    return {
        "bilete_deschise": open_tickets,
        "de_verificat": due_matches(db, now=now),
        "ultima_verificare": _read_meta(db, LAST_CHECK_KEY),
        "urmatoarea_verificare": next_check_at(db, now=now),
    }
