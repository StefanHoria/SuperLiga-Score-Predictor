"""Provider Flashscore - scor live pentru SuperLiga.

Pagina competitiei include feed-ul intern al site-ului direct in HTML, in
format delimitat, fara token si fara JavaScript:

    ~AA÷0Sgejp7D¬AD÷1788112800¬CX÷FCSB¬AF÷UTA Arad¬AG÷1¬AH÷3¬AB÷3¬ER÷Runda 7

Delimitatorii: `~AA÷` incepe un meci nou, `¬` separa campurile, `÷` separa
cheia de valoare.

Campurile care ne intereseaza:

    AD   momentul startului (timestamp)      AG/AH  scorul gazda/oaspete
    CX   echipa gazda                        BC/BD  scorul la pauza
    AF   echipa oaspete                      ER     etapa
    AB   starea (1 programat, 3 incheiat)    AO     momentul incheierii

ATENTIE la stare: am observat direct doar codurile 1 (programat) si 3
(incheiat). Codul pentru "in desfasurare" nu a putut fi confirmat, neexistand
meci live la momentul scrierii. De aceea providerul NU se bazeaza pe o valoare
anume: trateaza drept live orice meci care nu e nici programat, nici incheiat,
dar are deja scor. Asa, un cod necunoscut nu strica nimic.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from .base import DataProvider, IngestResult
from .flashscoreodds import (
    BET_TYPES, DEFAULT_BOOKMAKERS, OddsFormatChanged, OddsRow, aggregate,
    decode, menu_query, odds_query, parse_bookmakers, parse_odds,
)

log = logging.getLogger(__name__)

FEED_URL = "https://www.flashscore.ro/fotbal/romania/superliga/"

#: starile pe care le-am putut confirma direct din feed
STATUS_SCHEDULED = "1"
STATUS_FINISHED = "3"

#: campurile din care incercam sa citim minutul curent, in ordine
MINUTE_FIELDS = ("AC", "AM", "BA", "AL")


@dataclass
class LiveMatch:
    """Un meci din feed, in forma folosita de aplicatie."""

    external_id: str
    kickoff: datetime | None
    home: str
    away: str
    home_goals: int | None
    away_goals: int | None
    ht_home_goals: int | None
    ht_away_goals: int | None
    state: str            # programat | live | incheiat
    minute: int | None
    round: str | None

    @property
    def is_live(self) -> bool:
        return self.state == "live"

    @property
    def is_finished(self) -> bool:
        return self.state == "incheiat"

    @property
    def label(self) -> str:
        return f"{self.home} — {self.away}"

    @property
    def score(self) -> str:
        if self.home_goals is None or self.away_goals is None:
            return "—"
        return f"{self.home_goals}-{self.away_goals}"


def _parse_fields(block: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in block.split("¬"):
        if "÷" in part:
            key, _, value = part.partition("÷")
            out.setdefault(key.strip(), value)
    return out


def _as_int(value: Any) -> int | None:
    text = str(value or "").strip()
    if not text or not text.lstrip("-").isdigit():
        return None
    return int(text)


def parse_feed(html: str) -> list[LiveMatch]:
    """Extrage meciurile din feed-ul incorporat in pagina."""
    matches: list[LiveMatch] = []
    for block in html.split("~AA÷")[1:]:
        fields = _parse_fields(block)
        home, away = fields.get("CX"), fields.get("AF")
        if not home or not away:
            continue

        external_id = block.split("¬", 1)[0].strip()
        timestamp = _as_int(fields.get("AD"))
        kickoff = (datetime.fromtimestamp(timestamp, tz=timezone.utc)
                   if timestamp else None)

        home_goals = _as_int(fields.get("AG"))
        away_goals = _as_int(fields.get("AH"))
        status = str(fields.get("AB") or "").strip()

        if status == STATUS_FINISHED or fields.get("AO"):
            state = "incheiat"
        elif status == STATUS_SCHEDULED and home_goals is None:
            state = "programat"
        elif home_goals is not None:
            # nici programat, nici incheiat, dar are scor => se joaca
            state = "live"
        else:
            state = "programat"

        matches.append(LiveMatch(
            external_id=external_id,
            kickoff=kickoff,
            home=home,
            away=away,
            home_goals=home_goals,
            away_goals=away_goals,
            ht_home_goals=_as_int(fields.get("BC")),
            ht_away_goals=_as_int(fields.get("BD")),
            state=state,
            minute=_extract_minute(fields) if state == "live" else None,
            round=fields.get("ER") or None,
        ))
    return matches


def _extract_minute(fields: dict[str, str]) -> int | None:
    """Minutul curent, daca feed-ul il publica intr-unul din campurile stiute."""
    for key in MINUTE_FIELDS:
        raw = str(fields.get(key) or "")
        match = re.search(r"\b(\d{1,3})\b", raw)
        if match:
            minute = int(match.group(1))
            if 0 < minute <= 130:
                return minute
    return None


def _information(match: LiveMatch) -> int:
    """Cate campuri utile are un rand - pentru a alege intre duplicate."""
    return sum(1 for value in (match.home_goals, match.away_goals,
                               match.ht_home_goals, match.ht_away_goals,
                               match.minute, match.round)
               if value is not None)


def _deduplicate(matches: list[LiveMatch]) -> list[LiveMatch]:
    """Feed-ul listeaza acelasi meci de mai multe ori (rezultate + program).

    Pastram varianta cu cele mai multe campuri completate: aceeasi partida
    apare si intr-o sectiune scurta (doar scorul), si intr-una completa (cu
    scorul la pauza si etapa). Alegand dupa scor am fi pastrat-o uneori pe cea
    saraca si am fi pierdut restul.
    """
    seen: dict[tuple[str, str, str], LiveMatch] = {}
    for match in matches:
        day = match.kickoff.date().isoformat() if match.kickoff else "?"
        key = (match.home, match.away, day)
        current = seen.get(key)
        if current is None or _information(match) > _information(current):
            seen[key] = match
    return list(seen.values())


class FlashscoreProvider(DataProvider):
    name = "flashscore"
    provides = ("scor live", "rezultate", "scor la pauza", "etapa",
                "cote pre-meci (1X2, peste/sub, ambele marcheaza, sansa dubla)")

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # feed public, dar il interogam politicos
        self.http.delay = max(self.http.delay, 2.0)

    @property
    def available(self) -> bool:
        return self.cfg.secrets.flashscore_enabled

    # ------------------------------------------------------------------
    def fetch_feed(self, *, ttl_hours: float = 0.0) -> list[LiveMatch]:
        """Toate meciurile din feed. `ttl_hours=0` inseamna mereu proaspat."""
        html = self.http.get_text(FEED_URL, ttl_hours=ttl_hours,
                                  use_cache=ttl_hours > 0)
        matches = parse_feed(html)
        if not matches:
            raise RuntimeError(
                "feed-ul Flashscore nu a putut fi interpretat "
                "(formatul paginii s-a schimbat?)")
        return _deduplicate(matches)

    def live_matches(self) -> list[LiveMatch]:
        """Doar meciurile in desfasurare acum."""
        return [m for m in self.fetch_feed() if m.is_live]

    # ------------------------------------------------------------------
    def ingest(self, matches: Iterable[LiveMatch], season: int) -> IngestResult:
        """Scrie rezultatele incheiate in baza de date."""
        result = IngestResult(source=self.name)
        for match in matches:
            if not match.is_finished or match.kickoff is None:
                continue
            if match.home_goals is None or match.away_goals is None:
                continue
            self.db.upsert_match(
                season=season,
                utc_date=match.kickoff.isoformat(),
                home=match.home,
                away=match.away,
                home_goals=match.home_goals,
                away_goals=match.away_goals,
                ht_home_goals=match.ht_home_goals,
                ht_away_goals=match.ht_away_goals,
                status="finished",
                round_=match.round,
                source=self.name,
                ext_id=match.external_id,
            )
            result.matches += 1
        return result

    def fetch_season(self, season: int) -> IngestResult:
        """Feed-ul acopera sezonul curent; pentru altele nu avem ce aduce."""
        if season != self.cfg.current_season:
            return IngestResult(source=self.name)
        try:
            return self.ingest(self.fetch_feed(ttl_hours=1.0), season)
        except Exception as exc:  # noqa: BLE001
            return IngestResult(source=self.name, errors=[str(exc)])

    def fetch_upcoming(self) -> IngestResult:
        """Programul vine de la Transfermarkt; aici doar rezultatele."""
        return IngestResult(source=self.name)

    # ------------------------------------------------------------------
    # Cote pre-meci
    # ------------------------------------------------------------------
    @property
    def odds_available(self) -> bool:
        return self.available and self.cfg.secrets.flashscore_odds

    def bookmakers_for(self, event_id: str) -> list[tuple[int, str]]:
        """Casele listate pentru un meci. Cade elegant pe lista implicita."""
        try:
            payload = decode(self.http.get_text(menu_query(event_id),
                                                ttl_hours=24.0))
            found = parse_bookmakers(payload)
        except Exception as exc:  # noqa: BLE001
            log.debug("lista de case nu s-a putut citi: %s", exc)
            found = []
        return found or list(DEFAULT_BOOKMAKERS)

    def match_odds(self, event_id: str, bookmakers: list[tuple[int, str]],
                   bet_types: tuple[str, ...]) -> dict[str, list[OddsRow]]:
        """Cotele unui meci, pe casa. Un meci fara cote intoarce dictionar gol."""
        out: dict[str, list[OddsRow]] = {}
        for book_id, book_name in bookmakers:
            rows: list[OddsRow] = []
            for bet_type in bet_types:
                url = odds_query(event_id, book_id, bet_type)
                try:
                    rows.extend(parse_odds(decode(
                        self.http.get_text(url, ttl_hours=self.odds_ttl_hours)),
                        bet_type))
                except OddsFormatChanged:
                    raise
                except Exception as exc:  # noqa: BLE001
                    log.debug("%s / %s: %s", event_id, bet_type, exc)
            if rows:
                out[book_name] = rows
        return out

    #: cat timp raman valide cotele descarcate; se misca in timpul zilei, dar
    #: nu atat de repede incat sa merite reinterogate la fiecare rulare
    odds_ttl_hours: float = 3.0

    #: cate raspunsuri stricate LA RAND inseamna ca s-a schimbat formatul
    FORMAT_FAILURE_LIMIT: int = 3

    def fetch_odds(self, *, days_ahead: int = 10, max_requests: int = 120,
                   bookmaker_limit: int = 3,
                   bet_types: tuple[str, ...] = ("HOME_DRAW_AWAY",
                                                 "OVER_UNDER",
                                                 "BOTH_TEAMS_TO_SCORE",
                                                 "DOUBLE_CHANCE"),
                   include_finished: bool = False) -> IngestResult:
        """Aduce cotele meciurilor care urmeaza, in ordinea startului.

        Bugetul de cereri exista din acelasi motiv ca la API-Football: un meci
        costa `case x tipuri de pariu` cereri, deci fara limita o rulare ar
        insemna sute. Meciurile cele mai apropiate se servesc primele.
        """
        result = IngestResult(source=self.name)
        if not self.odds_available:
            return result

        now = datetime.now(timezone.utc)
        horizon = now + timedelta(days=days_ahead)
        feed = self.fetch_feed(ttl_hours=1.0)

        wanted = [m for m in feed
                  if m.kickoff and (include_finished or not m.is_finished)
                  and (include_finished or m.kickoff >= now - timedelta(hours=3))
                  and m.kickoff <= horizon]
        wanted.sort(key=lambda m: m.kickoff)
        if not wanted:
            return result

        books = self.bookmakers_for(wanted[0].external_id)[:bookmaker_limit]
        per_match = max(1, len(books) * len(bet_types))
        spent = 0
        consecutive_failures = 0
        tolerated: list[str] = []

        for match in wanted:
            if spent + per_match > max_requests:
                break
            match_id = self._resolve_match(match)
            if match_id is None:
                continue
            try:
                by_book = self.match_odds(match.external_id, books, bet_types)
                consecutive_failures = 0
            except OddsFormatChanged as exc:
                # Un raspuns stricat izolat e o pana de moment, nu o schimbare
                # de format: pe 1 septembrie 2026 un singur raspuns gol a oprit
                # tot pasul si a marcat actualizarea zilnica drept esuata, desi
                # endpointul mergea perfect. O schimbare reala de format cade
                # de fiecare data, nu o data.
                consecutive_failures += 1
                tolerated.append(str(exc))
                if consecutive_failures >= self.FORMAT_FAILURE_LIMIT:
                    result.errors.append(
                        f"cotele au esuat de {consecutive_failures} ori la rand "
                        f"({exc}); probabil s-a schimbat formatul")
                    break
                spent += per_match
                continue
            spent += per_match
            if not by_book:
                continue
            result.odds += self._write_odds(match_id, by_book)
            result.matches += 1

        if tolerated and not result.errors:
            log.info("cote: %d meciuri sarite din cauza unor raspunsuri "
                     "stricate izolate", len(tolerated))
        return result

    def _resolve_match(self, match: LiveMatch) -> int | None:
        """Meciul nostru care corespunde unuia din feed.

        Legatura prin `external_id` exista doar pentru meciurile incheiate -
        acolo le scrie `ingest`. Pentru cele care urmeaza, exact cele care ne
        intereseaza la cote, cautam dupa echipe si data, iar cand gasim
        inregistram si legatura, ca data viitoare sa fie imediata.
        """
        known = self.db.match_id_by_external(self.name, match.external_id)
        if known is not None:
            return known
        if match.kickoff is None:
            return None

        from ..utils.teamnames import canonical_team

        home, away = canonical_team(match.home), canonical_team(match.away)
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT m.match_id FROM matches m"
                " JOIN teams ht ON ht.team_id = m.home_team_id"
                " JOIN teams at ON at.team_id = m.away_team_id"
                " WHERE ht.name = ? AND at.name = ?"
                "   AND ABS(julianday(m.utc_date) - julianday(?)) < 0.5"
                " ORDER BY ABS(julianday(m.utc_date) - julianday(?)) LIMIT 1",
                (home, away, match.kickoff.isoformat(),
                 match.kickoff.isoformat()),
            ).fetchone()
            if row is None:
                return None
            match_id = int(row["match_id"])
            conn.execute(
                "INSERT OR IGNORE INTO match_external_ids(match_id, source,"
                " ext_id) VALUES (?,?,?)",
                (match_id, self.name, match.external_id))
        return match_id

    def _write_odds(self, match_id: int,
                    by_book: dict[str, list[OddsRow]]) -> int:
        """Scrie cotele pe casa, plus media si cel mai bun pret.

        Agregatele se numesc `fs_avg` / `fs_max`, nu `avg` / `max`: acelea sunt
        deja folosite de football-data pentru cotele de inchidere, iar
        backtestul se sprijina pe ele. Nu le atingem.
        """
        rows: list[tuple] = []
        for book, priced in by_book.items():
            rows.extend((match_id, book, r.market, r.selection,
                         r.price, r.opening) for r in priced)
        # Agregatele n-au deschidere proprie: media si maximul se recalculeaza
        # la fiecare rulare, deci "deschiderea" lor n-ar insemna nimic.
        rows.extend((match_id, name, r.market, r.selection, r.price, None)
                    for name, r in aggregate(by_book))
        return self.db.upsert_odds(rows)

    # ------------------------------------------------------------------
    def correct_kickoff_times(self, *, tolerance_minutes: int = 5) -> int:
        """Aliniaza orele de start din baza la cele din feed.

        Flashscore publica momentul exact in UTC. Transfermarkt afiseaza ora
        Germaniei si LPF ora Romaniei, iar amandoua erau initial stocate ca si
        cum ar fi fost UTC - meciurile ajungeau in baza cu doua ore mai tarziu
        decat se joaca. Parserele au fost corectate, dar randurile scrise
        inainte raman gresite; functia asta le repara.

        Intoarce numarul de meciuri corectate.
        """
        from ..utils.teamnames import canonical_team

        feed = {}
        for match in self.fetch_feed(ttl_hours=1.0):
            if match.kickoff is None:
                continue
            key = (canonical_team(match.home), canonical_team(match.away))
            feed.setdefault(key, []).append(match.kickoff)

        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT m.match_id, m.utc_date, ht.name AS home, at.name AS away"
                " FROM matches m"
                " JOIN teams ht ON ht.team_id = m.home_team_id"
                " JOIN teams at ON at.team_id = m.away_team_id"
                " WHERE m.season = ?", (self.cfg.current_season,),
            ).fetchall()

        corrections: list[tuple[str, int]] = []
        for row in rows:
            candidates = feed.get((row["home"], row["away"]))
            if not candidates:
                continue
            stored = datetime.fromisoformat(str(row["utc_date"]))
            if stored.tzinfo is None:
                stored = stored.replace(tzinfo=timezone.utc)
            # alegem meciul din feed cel mai apropiat in timp (acelasi cuplu de
            # echipe se poate juca de doua ori intr-un sezon)
            best = min(candidates, key=lambda k: abs((k - stored).total_seconds()))
            drift = abs((best - stored).total_seconds())
            if drift > tolerance_minutes * 60 and drift < 36 * 3600:
                corrections.append((best.isoformat(), int(row["match_id"])))

        if corrections:
            with self.db.connect() as conn:
                conn.executemany(
                    "UPDATE matches SET utc_date = ? WHERE match_id = ?",
                    corrections)
        log.info("ore de start corectate: %d", len(corrections))
        return len(corrections)
