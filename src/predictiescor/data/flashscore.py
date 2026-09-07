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

#: Feed-ul incorporat in pagina NU contine meciul care se joaca chiar acum:
#: pagina il aduce separat, din endpoint-ul de mai jos, cu JavaScript. De aceea
#: „Niciun meci in desfasurare" aparea si cand era unul - sursa citita de noi
#: n-avea de unde sa stie. `{key}` e perechea turneu_sezon (ex. 157_GILi6JC9),
#: citita din pagina, ca sa nu ramana infipta in cod cand se schimba sezonul.
LIVE_FEED_URL = "https://9.flashscore.ninja/9/x/feed/t_1_{key}_3_ro_1"

#: Fara antetul asta endpoint-ul raspunde 401. E o valoare fixa a site-ului,
#: nu un token de utilizator - aceeasi pentru oricine deschide pagina.
LIVE_FEED_HEADERS = {"x-fsign": "SW9D1eZo"}

#: perechea turneu_sezon, asa cum o scrie feed-ul in antetul lui
_FEED_KEY_RE = re.compile(r"ZH÷(\w+_\w+)")

#: Statisticile unui meci, live sau incheiat. Aceleasi cifre pe care le arata
#: site-ul la „Statistici meci".
MATCH_STATS_URL = "https://9.flashscore.ninja/9/x/feed/df_st_1_{match}"

#: Coloanele pe care Flashscore le scrie in baza, cu ultimul cuvant. Restul
#: statisticilor raman ale LPF, sursa oficiala a ligii; aici e vorba doar de
#: cele doua piete pe care se pariaza, unde LPF s-a dovedit nesigura: la
#: Dinamo - FCSB publica 2-2 cornere la un meci cu 32 de suturi, iar
#: Flashscore da 14.
INGESTED_STATS = ("corners", "yellow_cards", "red_cards")

#: Cate zile dupa meci se mai cer statisticile. Dupa atata, nu se mai schimba.
STATS_RECHECK_DAYS = 7

#: Arhiva unui sezon trecut. Pagina da `seasonId`-ul intern, iar feed-ul de
#: rezultate se pagineaza cu el. Tiparul l-am luat din cererile pe care le face
#: pagina la apasarea pe „Arata mai multe meciuri".
ARCHIVE_URL = "https://www.flashscore.ro/fotbal/romania/superliga-{a}-{b}/rezultate/"
ARCHIVE_FEED = ("https://9.flashscore.ninja/9/x/feed/"
                "tr_1_{key}_{season_id}_{page}_3_ro_1")

#: cate pagini incercam inainte sa ne oprim (o pagina are ~100 de meciuri)
ARCHIVE_MAX_PAGES = 8

_SEASON_ID_RE = re.compile(r"seasonId:\s*(\d+)")

#: `SD` e un id numeric de statistica, stabil intre limbi - de aceea se
#: potriveste dupa el, nu dupa numele romanesc, care se poate schimba.
LIVE_STAT_IDS = {
    "12": "possession",
    "13": "shots_on_target",
    "14": "shots_off_target",
    "15": "free_kicks",
    "16": "corners",
    "17": "offsides",
    "19": "saves",
    "21": "fouls",
    "22": "red_cards",
    "23": "yellow_cards",
    "34": "shots",
    "158": "shots_blocked",
    "432": "xg",
    "461": "shots_inside_box",
    "463": "shots_outside_box",
}

#: starile pe care le-am putut confirma direct din feed
STATUS_SCHEDULED = "1"
STATUS_LIVE = "2"
STATUS_FINISHED = "3"

#: Reperele dupa care se decide in ce repriza suntem. `AO` e momentul in care a
#: pornit ceasul reprizei curente, nu finalul meciului - de aici si confuzia de
#: mai jos, care marca meciurile live drept incheiate.
_SECOND_HALF_AFTER = 40 * 60   # secunde de la start pana la repriza a doua
_MATCH_IS_OVER_AFTER = 100 * 60  # dupa atata, meciul s-a incheiat sigur


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

        # `AB` e autoritar cand il intelegem. Inainte se citea si `AO` ca semn
        # de meci incheiat, dar `AO` apare si la meciurile in desfasurare (e
        # startul reprizei curente), deci un meci live era dat drept terminat -
        # si disparea din pagina de Live.
        if status == STATUS_FINISHED:
            state = "incheiat"
        elif status == STATUS_LIVE:
            state = "live"
        elif status == STATUS_SCHEDULED:
            state = "programat"
        elif _looks_over(fields):
            state = "incheiat"
        elif home_goals is not None:
            # stare necunoscuta, dar are scor => se joaca
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
            minute=_live_minute(fields) if state == "live" else None,
            round=fields.get("ER") or None,
        ))
    return matches


def _looks_over(fields: dict[str, str]) -> bool:
    """Meciul pare terminat, judecand doar dupa ceas.

    Se foloseste numai cand codul de stare e unul pe care nu-l cunoastem.
    `AO` e ultima schimbare de stare: la un meci incheiat e fluierul final
    (masurat: la ~112 minute dupa start), la unul in desfasurare e pornirea
    ceasului reprizei (la ~2 minute). Peste doua ore de la ora de start, orice
    meci de fotbal s-a terminat.
    """
    started = _as_int(fields.get("AO"))
    kickoff = _as_int(fields.get("AD"))
    if started is None:
        return False
    if kickoff is None:
        return True
    return started - kickoff > _MATCH_IS_OVER_AFTER


def _live_minute(fields: dict[str, str], *, now: float | None = None
                 ) -> int | None:
    """Minutul curent, socotit din momentul in care a pornit ceasul.

    Feed-ul nu publica minutul ca numar; il calculeaza si site-ul, din `AO` -
    startul reprizei curente. Repriza se deduce din distanta fata de ora de
    start: daca ceasul a pornit la cateva minute dupa fluierul de incepere,
    suntem in prima repriza; daca a pornit mult mai tarziu, in a doua, si
    atunci se adauga cele 45 de minute.

    Prima versiune citea `AC`, care e un cod de stare, nu un minut: la Craiova
    - „U" Cluj scria 12 in minutul 24.
    """
    started = _as_int(fields.get("AO"))
    kickoff = _as_int(fields.get("AD"))
    if started is None:
        return None

    now = datetime.now(timezone.utc).timestamp() if now is None else now
    elapsed = (now - started) / 60.0
    if elapsed < 0:
        return None

    if kickoff is not None and started - kickoff >= _SECOND_HALF_AFTER:
        elapsed += 45
    minute = int(elapsed) + 1
    return minute if 0 < minute <= 130 else None


def _stat_number(text: str) -> float | None:
    """Cifra dintr-o valoare de statistica.

    Vin in trei forme: „3", „61%" si „87% (183/211)". Peste tot conteaza
    primul numar; procentul si detaliul din paranteza se ignora.
    """
    found = re.search(r"-?\d+(?:\.\d+)?", str(text or ""))
    return float(found.group(0)) if found else None


def parse_match_stats(raw: str) -> dict[str, tuple[float, float]]:
    """Statisticile meciului, ca {coloana: (gazda, oaspete)}.

    Feed-ul repeta aceleasi statistici pe sectiuni: intai „Meci" (tot meciul),
    apoi „Repriza 1", „Repriza 2". Se citeste **doar prima**; altfel valorile
    de pe reprize le-ar suprascrie pe cele totale si un meci cu doua cartonase
    in prima repriza si trei in a doua ar raporta trei.
    """
    out: dict[str, tuple[float, float]] = {}
    sections = 0
    for chunk in raw.split("~"):
        fields = _parse_fields(chunk)
        if "SE" in fields:
            sections += 1
            if sections > 1:
                break
            continue
        stat_id = fields.get("SD")
        if not stat_id or stat_id not in LIVE_STAT_IDS:
            continue
        home = _stat_number(fields.get("SH"))
        away = _stat_number(fields.get("SI"))
        if home is None or away is None:
            continue
        out[LIVE_STAT_IDS[stat_id]] = (home, away)
    return out


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

    def fetch_live_feed(self) -> list[LiveMatch]:
        """Meciurile din feed-ul de live - singurul care le contine pe cele

        in desfasurare. Se cere cheia turneului din pagina, deci o citire in
        plus; e ieftina si tine URL-ul valabil cand se schimba sezonul.
        """
        page = self.http.get_text(FEED_URL, ttl_hours=0, use_cache=False)
        found = _FEED_KEY_RE.search(page)
        if not found:
            raise RuntimeError(
                "nu am gasit cheia turneului in pagina Flashscore "
                "(formatul s-a schimbat?)")
        raw = self.http.get_text(
            LIVE_FEED_URL.format(key=found.group(1)),
            ttl_hours=0, use_cache=False, extra_headers=LIVE_FEED_HEADERS)
        return _deduplicate(parse_feed(raw))

    def fetch_match_stats(self, external_id: str
                          ) -> dict[str, tuple[float, float]]:
        """Statisticile unui meci, mergand si in timpul lui.

        De aici vine numarul de cartonase de pana acum: feed-ul de scor da
        doar golurile, iar fara statistici un pont pe cartonase ramanea
        „nedecidabil" cat timp se juca meciul.
        """
        raw = self.http.get_text(
            MATCH_STATS_URL.format(match=external_id),
            ttl_hours=0, use_cache=False, extra_headers=LIVE_FEED_HEADERS)
        return parse_match_stats(raw)

    def fetch_archive(self, season: int) -> list[LiveMatch]:
        """Meciurile unui sezon trecut, cu id-urile lor.

        Feed-ul obisnuit acopera doar sezonul curent, deci fara asta nu putem
        cere statisticile meciurilor vechi - iar 2025 nu are absolut nicio
        statistica in baza.
        """
        page_url = ARCHIVE_URL.format(a=season, b=season + 1)
        page = self.http.get_text(page_url, ttl_hours=24.0, use_cache=True)
        season_id = _SEASON_ID_RE.search(page)
        key = _FEED_KEY_RE.search(page)
        if not season_id or not key:
            raise RuntimeError(f"nu am gasit sezonul {season} in arhiva")

        out: list[LiveMatch] = []
        for index in range(ARCHIVE_MAX_PAGES):
            raw = self.http.get_text(
                ARCHIVE_FEED.format(key=key.group(1),
                                    season_id=season_id.group(1), page=index),
                ttl_hours=24.0, use_cache=True,
                extra_headers=LIVE_FEED_HEADERS)
            rows = parse_feed(raw)
            if not rows:
                break
            out.extend(rows)
        return _deduplicate(out)

    def link_archive(self, season: int) -> int:
        """Leaga meciurile din arhiva de cele din baza, dupa zi si echipe.

        Potrivirea e pe ziua meciului plus perechea de echipe normalizate -
        nu pe ora, fiindca sursele difera cu minute, si nu pe scor, fiindca
        exact ce vrem sa verificam mai tarziu n-are voie sa decida potrivirea.
        """
        from ..utils.teamnames import canonical_team

        with self.db.connect() as conn:
            ours = {
                (str(r["utc_date"])[:10], r["home"], r["away"]): int(r["match_id"])
                for r in conn.execute(
                    "SELECT m.match_id, m.utc_date, h.name AS home,"
                    "       a.name AS away"
                    " FROM matches m"
                    " JOIN teams h ON h.team_id = m.home_team_id"
                    " JOIN teams a ON a.team_id = m.away_team_id"
                    " WHERE m.season = ? AND m.status = 'finished'", (season,))
            }

        linked = 0
        with self.db.connect() as conn:
            for match in self.fetch_archive(season):
                if match.kickoff is None:
                    continue
                cheie = (match.kickoff.date().isoformat(),
                         canonical_team(match.home), canonical_team(match.away))
                match_id = ours.get(cheie)
                if match_id is None:
                    continue
                conn.execute(
                    "INSERT OR IGNORE INTO match_external_ids"
                    "(match_id, source, ext_id) VALUES (?,?,?)",
                    (match_id, self.name, match.external_id))
                linked += 1
        log.info("[%s] sezon %s: %d meciuri legate de arhiva",
                 self.name, season, linked)
        return linked

    def ingest_stats(self, season: int, *, full: bool = False) -> IngestResult:
        """Scrie cornerele si cartonasele meciurilor jucate.

        Se cer doar meciurile la care raspunsul se mai poate schimba - cele
        din ultimele `STATS_RECHECK_DAYS` zile - plus cele carora le lipsesc
        cornerele cu totul. `full=True` reia tot sezonul, pentru o reparare.

        O cerere per meci, deci lista trebuie sa ramana scurta: intr-o rulare
        obisnuita sunt cele opt meciuri ale etapei.
        """
        result = IngestResult(source=self.name)
        for match_id, ext_id, home_id, away_id in self._stats_todo(season, full):
            try:
                stats = self.fetch_match_stats(ext_id)
            except Exception as exc:  # noqa: BLE001
                result.errors.append(f"meci {ext_id}: {exc}")
                continue
            columns = {name: stats[name] for name in INGESTED_STATS
                       if name in stats}
            if not columns:
                continue
            for team_id, side in ((home_id, 0), (away_id, 1)):
                self.db.upsert_team_stats(
                    match_id, team_id, side == 0,
                    {name: pair[side] for name, pair in columns.items()},
                    source=self.name)
                result.team_stats += 1
            result.matches += 1
        log.info("[%s] statistici: %d meciuri, %d randuri",
                 self.name, result.matches, result.team_stats)
        return result

    def _stats_todo(self, season: int, full: bool) -> list[tuple]:
        """Meciurile carora le cerem statisticile in rularea asta."""
        where = "" if full else (
            f" AND (m.utc_date >= datetime('now', '-{STATS_RECHECK_DAYS} days')"
            "      OR NOT EXISTS (SELECT 1 FROM match_team_stats s"
            "                     WHERE s.match_id = m.match_id"
            "                       AND s.corners IS NOT NULL))")
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT m.match_id, e.ext_id, m.home_team_id, m.away_team_id"
                " FROM matches m"
                " JOIN match_external_ids e ON e.match_id = m.match_id"
                "  AND e.source = ?"
                " WHERE m.season = ? AND m.status = 'finished'" + where
                + " ORDER BY m.utc_date DESC",
                (self.name, season),
            ).fetchall()
        return [(int(r["match_id"]), str(r["ext_id"]),
                 int(r["home_team_id"]), int(r["away_team_id"])) for r in rows]

    def fetch_feed_with_live(self) -> list[LiveMatch]:
        """Programul intreg, plus meciurile care se joaca chiar acum.

        Feed-ul din pagina are tot sezonul, dar nu si meciul in desfasurare;
        cel de live are meciul, dar nu si restul programului. Amandoua, deci.
        Randurile care se repeta trec prin aceeasi alegere ca de obicei: se
        pastreaza cel cu mai multe informatii.
        """
        season = self.fetch_feed()
        try:
            live = self.fetch_live_feed()
        except Exception as exc:  # noqa: BLE001
            log.warning("feed-ul de live n-a raspuns (%s); "
                        "raman doar meciurile din pagina", exc)
            return season
        return _deduplicate(season + live)

    def live_matches(self) -> list[LiveMatch]:
        """Doar meciurile in desfasurare acum.

        Se incearca intai feed-ul de live; daca nu raspunde, se cade pe cel
        incorporat in pagina, care nu are meciurile in curs, dar macar nu lasa
        aplicatia fara nimic.
        """
        try:
            return [m for m in self.fetch_live_feed() if m.is_live]
        except Exception as exc:  # noqa: BLE001
            log.warning("feed-ul de live n-a raspuns (%s); "
                        "incerc feed-ul din pagina", exc)
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
