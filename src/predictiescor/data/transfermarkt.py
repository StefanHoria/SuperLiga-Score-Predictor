"""Provider Transfermarkt - loturi si statistici sezoniere de jucatori.

Rol in arhitectura: API-Football (plan gratuit) nu da acces la sezonul curent,
asa ca datele despre jucatorii din SuperLiga in sezonul in curs vin de aici -
prezente, minute, goluri, pase decisive, cartonase si valoare de piata.

Se citesc doar paginile publice de statistici, cu intarziere intre cereri si
cache pe disc, ca sa nu incarcam inutil serverul.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import Any

from bs4 import BeautifulSoup

from .base import DataProvider, IngestResult

log = logging.getLogger(__name__)

BASE = "https://www.transfermarkt.com"

#: Transfermarkt afiseaza orele in fusul Germaniei (CET/CEST), nu in UTC si
#: nici in ora Romaniei. Fara conversie, un meci de la 20:00 pe site ajungea in
#: baza ca 20:00 UTC - cu doua ore mai tarziu decat se joaca in realitate.
SITE_TIMEZONE = ZoneInfo("Europe/Berlin")
COMPETITION = "RO1"  # SuperLiga

# Pozitiile td din tabelul "leistungsdaten" (vezi tests/test_transfermarkt.py).
# Celula "Player" este un tabel imbricat care ocupa td1..td4.
TD_NAME, TD_POSITION, TD_AGE = 3, 4, 5
TD_IN_SQUAD, TD_APPS, TD_GOALS, TD_ASSISTS = 7, 8, 9, 10
TD_YELLOW, TD_SECOND_YELLOW, TD_RED = 11, 12, 13
TD_SUB_ON, TD_SUB_OFF, TD_PPG, TD_MINUTES = 14, 15, 16, 17


def _num(text: Any) -> float | None:
    """'12' -> 12.0 ; '1.234' -> 1234.0 ; '-' / '' -> None ; \"270'\" -> 270.0"""
    if text is None:
        return None
    s = str(text).strip().replace("'", "").replace("’", "")
    if not s or s in {"-", "?", "N/A"}:
        return None
    s = s.replace(".", "") if re.fullmatch(r"\d{1,3}(\.\d{3})+", s) else s
    s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _market_value(text: str) -> float | None:
    """'€1.20m' -> 1200000 ; '€800k' -> 800000."""
    if not text:
        return None
    m = re.search(r"([\d.,]+)\s*(m|k|bn)?", text.replace("€", "").strip(), re.I)
    if not m:
        return None
    try:
        value = float(m.group(1).replace(",", "."))
    except ValueError:
        return None
    unit = (m.group(2) or "").lower()
    return value * {"m": 1e6, "k": 1e3, "bn": 1e9}.get(unit, 1.0)


def _parse_date(text: str) -> datetime | None:
    """'Fri 17/07/26' -> datetime(2026, 7, 17). Celula goala inseamna
    'aceeasi zi ca randul anterior', deci intoarcem None."""
    if not text:
        return None
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{2,4})", text)
    if not m:
        return None
    day, month, year = (int(g) for g in m.groups())
    if year < 100:
        year += 2000
    try:
        return datetime(year, month, day, tzinfo=timezone.utc)
    except ValueError:
        return None


def _combine(day: datetime, time_text: str) -> str:
    """Adauga ora ('8:30 PM') la o zi si intoarce ISO 8601 in UTC.

    Ora de pe site e in fusul Germaniei; o interpretam ca atare si o convertim,
    ca sa nu ajunga in baza decalata cu una sau doua ore (dupa sezon).
    """
    m = re.search(r"(\d{1,2}):(\d{2})\s*(AM|PM)?", time_text or "", re.I)
    if not m:
        return day.astimezone(timezone.utc).isoformat()

    hour, minute = int(m.group(1)), int(m.group(2))
    suffix = (m.group(3) or "").upper()
    if suffix == "PM" and hour != 12:
        hour += 12
    elif suffix == "AM" and hour == 12:
        hour = 0

    local = datetime(day.year, day.month, day.day,
                     min(hour, 23), min(minute, 59), tzinfo=SITE_TIMEZONE)
    return local.astimezone(timezone.utc).isoformat()


#: Transfermarkt lipeste pozitia in clasament de numele echipei in pagina de
#: program: "(7.) FCV Farul", "FCSB (3.)". Fara curatare am crea echipe fantoma.
_RANK_PREFIX = re.compile(r"^\s*\(\d+\.\)\s*")
_RANK_SUFFIX = re.compile(r"\s*\(\d+\.\)\s*$")


def _clean_team(text: str) -> str:
    """'(7.) FCV Farul' / 'FCSB (3.)' -> numele echipei, fara pozitie."""
    name = _RANK_PREFIX.sub("", str(text or ""))
    name = _RANK_SUFFIX.sub("", name)
    return " ".join(name.split())


def _parse_score(text: str) -> tuple[int | None, int | None]:
    """'2:1' -> (2, 1) ; '-:-' (meci nejucat) -> (None, None)."""
    m = re.match(r"^\s*(\d+)\s*:\s*(\d+)\s*$", text or "")
    if not m:
        return None, None
    return int(m.group(1)), int(m.group(2))


class TransfermarktProvider(DataProvider):
    name = "transfermarkt"
    provides = (
        "loturi", "minute jucatori", "goluri/assist-uri pe sezon",
        "cartonase pe sezon", "valoare de piata", "accidentari", "suspendari",
    )

    #: cate absente a gasit ultima rulare (raportat de scriptul de update)
    absences_found: int = 0

    @property
    def available(self) -> bool:
        # Nu are nevoie de cheie; poate fi oprit din .env daca site-ul blocheaza.
        return self.cfg.secrets.transfermarkt_enabled

    # ------------------------------------------------------------------
    def fetch_clubs(self, season: int) -> list[tuple[str, str, str]]:
        """Cluburile din SuperLiga intr-un sezon: (nume, id_club, slug)."""
        url = f"{BASE}/superliga/startseite/wettbewerb/{COMPETITION}"
        html = self.http.get_text(url, {"saison_id": season}, ttl_hours=72)
        soup = BeautifulSoup(html, "lxml")

        clubs: list[tuple[str, str, str]] = []
        seen: set[str] = set()
        for a in soup.select("table.items td.hauptlink a[href*='/startseite/verein/']"):
            href = a.get("href") or ""
            m = re.search(r"/([^/]+)/startseite/verein/(\d+)", href)
            if not m:
                continue
            slug, club_id = m.group(1), m.group(2)
            if club_id in seen:
                continue
            seen.add(club_id)
            name = (a.get("title") or a.get_text(strip=True)).strip()
            if name:
                clubs.append((name, club_id, slug))
        return clubs

    # ------------------------------------------------------------------
    def fetch_club_players(self, club_id: str, slug: str, season: int,
                           team_id: int) -> int:
        """Statisticile sezoniere ale jucatorilor unui club. Intoarce nr. de randuri."""
        # `reldata` filtreaza la competitia interna (fara cupe / Europa).
        # Valoarea contine un "&" codificat ca %26; il punem direct in URL,
        # altfel `requests` ar re-encoda semnul procent in %2526.
        url = (f"{BASE}/{slug}/leistungsdaten/verein/{club_id}/plus/1"
               f"?reldata={COMPETITION}%26{season}")
        html = self.http.get_text(url, ttl_hours=24)
        soup = BeautifulSoup(html, "lxml")
        table = soup.select_one("table.items")
        if table is None:
            log.warning("[%s] tabel lipsa pentru clubul %s", self.name, slug)
            return 0

        written = 0
        for tr in table.select("tbody > tr"):
            tds = tr.select("td")
            if len(tds) <= TD_MINUTES:
                continue

            # Celula de nume concateneaza numele complet cu forma scurta
            # ("Stefan Tarnovanu S. Tarnovanu"). Link-ul catre profil poarta
            # numele curat in atributul `title`, deci il preferam.
            name = self._player_name(tr, tds[TD_NAME])
            if not name:
                continue

            position = tds[TD_POSITION].get_text(" ", strip=True)
            age = _num(tds[TD_AGE].get_text(strip=True))
            minutes = _num(tds[TD_MINUTES].get_text(strip=True))
            apps = _num(tds[TD_APPS].get_text(strip=True))
            if not apps and not minutes:
                continue  # jucator fara nicio aparitie in sezon

            ext_id = self._player_ext_id(tr)
            player_id = self.db.upsert_player(
                name, team_id, source=self.name, ext_id=ext_id,
                position=position or None,
            )
            self._upsert_season(
                season, player_id, team_id,
                appearances=apps,
                minutes=minutes,
                goals=_num(tds[TD_GOALS].get_text(strip=True)) or 0.0,
                assists=_num(tds[TD_ASSISTS].get_text(strip=True)) or 0.0,
                yellow_cards=_num(tds[TD_YELLOW].get_text(strip=True)) or 0.0,
                second_yellow=_num(tds[TD_SECOND_YELLOW].get_text(strip=True)) or 0.0,
                red_cards=_num(tds[TD_RED].get_text(strip=True)) or 0.0,
                subbed_on=_num(tds[TD_SUB_ON].get_text(strip=True)),
                subbed_off=_num(tds[TD_SUB_OFF].get_text(strip=True)),
                points_per_game=_num(tds[TD_PPG].get_text(strip=True)),
                age=age,
            )
            written += 1
        return written

    @classmethod
    def _player_name(cls, row, name_cell) -> str:
        """Numele curat al jucatorului dintr-un rand de tabel."""
        link = row.select_one("a[href*='/profil/spieler/']")
        if link is not None:
            candidate = (link.get("title") or link.get_text(" ", strip=True) or "")
            candidate = " ".join(candidate.split())
            if candidate:
                return candidate
        return cls._clean_name(name_cell.get_text(" ", strip=True))

    @staticmethod
    def _clean_name(raw: str) -> str:
        """Rezerva, cand link-ul de profil lipseste.

        'Stefan Tarnovanu S. Tarnovanu' -> 'Stefan Tarnovanu'
        'Joao Paulo Joao Paulo'         -> 'Joao Paulo'
        """
        text = " ".join(raw.split())
        # cazul in care forma scurta e identica cu numele complet, lipita
        words = text.split()
        half = len(words) // 2
        if half and words[:half] == words[half:half * 2]:
            return " ".join(words[:half])
        # forma scurta abreviata ('S. Tarnovanu') lipita dupa numele complet
        m = re.search(r"^(.*?)\s+([A-ZȘȚĂÂÎ]\.\s.*)$",
                      text)
        if m:
            return m.group(1).strip()
        return text

    @staticmethod
    def _player_ext_id(tr) -> str | None:
        a = tr.select_one("a[href*='/profil/spieler/']")
        if not a:
            return None
        m = re.search(r"/profil/spieler/(\d+)", a.get("href") or "")
        return m.group(1) if m else None

    def _upsert_season(self, season: int, player_id: int, team_id: int,
                       *, age: float | None = None, **stats: Any) -> None:
        cols = ["season", "player_id", "team_id", "competition", "appearances",
                "minutes", "goals", "assists", "yellow_cards", "second_yellow",
                "red_cards", "subbed_on", "subbed_off", "points_per_game",
                "market_value_eur", "source"]
        vals = [season, player_id, team_id, "liga1",
                stats.get("appearances"), stats.get("minutes"), stats.get("goals"),
                stats.get("assists"), stats.get("yellow_cards"),
                stats.get("second_yellow"), stats.get("red_cards"),
                stats.get("subbed_on"), stats.get("subbed_off"),
                stats.get("points_per_game"), stats.get("market_value_eur"),
                self.name]
        updates = ", ".join(f"{c} = excluded.{c}" for c in cols[4:])
        placeholders = ", ".join(["?"] * len(cols))
        with self.db.connect() as conn:
            conn.execute(
                f"INSERT INTO player_season_stats({', '.join(cols)})"
                f" VALUES ({placeholders})"
                f" ON CONFLICT(season, player_id, competition) DO UPDATE SET {updates}",
                vals,
            )

    # ------------------------------------------------------------------
    # Absente: accidentari si suspendari
    # ------------------------------------------------------------------
    def fetch_absences(self, club_id: str, slug: str, team_id: int) -> int:
        """Jucatorii indisponibili ai unui club. Intoarce cati a gasit.

        Pagina grupeaza absentele pe sectiuni ("Injuries", "Suspensions"),
        marcate prin randuri fara date de jucator. Le urmarim ca sa stim daca
        e vorba de accidentare sau suspendare.
        """
        url = f"{BASE}/{slug}/sperrenundverletzungen/verein/{club_id}"
        html = self.http.get_text(url, ttl_hours=6)
        soup = BeautifulSoup(html, "lxml")
        table = soup.select_one("table.items")
        if table is None:
            return 0

        kind = "accidentare"
        rows: list[dict[str, Any]] = []
        for tr in table.select("tbody > tr"):
            cells = [td.get_text(" ", strip=True) for td in tr.select("td")]
            if not cells:
                continue

            # rand de sectiune: o singura celula, fara link de jucator
            link = tr.select_one("a[href*='/profil/spieler/']")
            if link is None:
                header = " ".join(cells).lower()
                if "suspend" in header or "ban" in header or "sperr" in header:
                    kind = "suspendare"
                elif "injur" in header or "verletz" in header:
                    kind = "accidentare"
                continue

            name = " ".join((link.get("title")
                             or link.get_text(" ", strip=True)).split())
            if not name:
                continue

            # ultimele celule: motiv, de cand, revenire estimata, meciuri ratate
            tail = cells[-4:] if len(cells) >= 4 else cells
            reason = tail[0] if len(tail) > 0 else None
            since = tail[1] if len(tail) > 1 else None
            expected = tail[2] if len(tail) > 2 else None
            missed = _num(tail[3]) if len(tail) > 3 else None

            player_id = self.db.upsert_player(
                name, team_id, source=self.name,
                ext_id=self._player_ext_id(tr))
            rows.append({
                "player_id": player_id, "kind": kind, "reason": reason or None,
                "since": since or None,
                "expected_return": (expected or None) if expected != "-" else None,
                "missed_matches": int(missed) if missed is not None else None,
            })

        return self.db.replace_availability(team_id, rows, source=self.name)

    # ------------------------------------------------------------------
    # Program si rezultate (sursa LIVE pentru sezonul curent)
    # ------------------------------------------------------------------
    def fetch_schedule(self, season: int, *, ttl_hours: float = 3.0) -> IngestResult:
        """Programul complet al sezonului: meciuri jucate SI viitoare.

        Este singura sursa gratuita care da si calendarul viitor, deci sta la
        baza paginii de bilete si a predictiilor pentru etapa urmatoare.
        """
        result = IngestResult(source=self.name)
        url = (f"{BASE}/superliga/gesamtspielplan/wettbewerb/{COMPETITION}"
               f"/saison_id/{season}")
        html = self.http.get_text(url, ttl_hours=ttl_hours)
        soup = BeautifulSoup(html, "lxml")

        for box in soup.select("div.box"):
            headline = box.select_one("div.content-box-headline, h2")
            round_name = headline.get_text(strip=True) if headline else None
            table = box.select_one("table")
            if table is None:
                continue

            current_date: datetime | None = None
            for tr in table.select("tr"):
                cells = [td.get_text(" ", strip=True) for td in tr.select("td")]
                if len(cells) < 7:
                    continue

                date_text, time_text = cells[0], cells[1]
                home = _clean_team(cells[2])
                score = cells[4]
                away = _clean_team(cells[6])
                if not home or not away:
                    continue

                parsed = _parse_date(date_text)
                if parsed is not None:
                    current_date = parsed
                if current_date is None:
                    continue

                utc_date = _combine(current_date, time_text)
                home_goals, away_goals = _parse_score(score)
                played = home_goals is not None

                self.db.upsert_match(
                    season=season,
                    utc_date=utc_date,
                    home=home,
                    away=away,
                    home_goals=home_goals,
                    away_goals=away_goals,
                    status="finished" if played else "scheduled",
                    round_=round_name,
                    source=self.name,
                    ext_id=f"{season}|{home}|{away}",
                )
                result.matches += 1

        if result.matches == 0:
            result.errors.append(
                f"programul sezonului {season} nu a putut fi citit "
                "(structura paginii s-a schimbat sau accesul e blocat)")
        log.info("[%s] program sezon %s: %d meciuri",
                 self.name, season, result.matches)
        return result

    def fetch_upcoming(self) -> IngestResult:
        """Reimprospateaza programul sezonului curent (folosit de update.py)."""
        return self.fetch_schedule(self.cfg.current_season, ttl_hours=1.0)

    # ------------------------------------------------------------------
    def fetch_season(self, season: int) -> IngestResult:
        result = IngestResult(source=self.name)
        try:
            result.merge(self.fetch_schedule(season))
        except Exception as exc:  # noqa: BLE001
            result.errors.append(f"program {season}: {exc}")

        try:
            clubs = self.fetch_clubs(season)
        except Exception as exc:  # noqa: BLE001
            result.errors.append(f"lista cluburi {season}: {exc}")
            return result

        if not clubs:
            result.errors.append(
                f"niciun club gasit pentru sezonul {season} "
                "(structura paginii s-a schimbat sau accesul e blocat)"
            )
            return result

        for name, club_id, slug in clubs:
            try:
                team_id = self.db.upsert_team(
                    name, source=self.name, ext_id=club_id)
                result.player_stats += self.fetch_club_players(
                    club_id, slug, season, team_id)
            except Exception as exc:  # noqa: BLE001
                result.errors.append(f"{name}: {exc}")

            if season == self.cfg.current_season:
                try:
                    self.absences_found += self.fetch_absences(
                        club_id, slug, team_id)
                except Exception as exc:  # noqa: BLE001
                    result.errors.append(f"absente {name}: {exc}")
        log.info("[%s] sezon %s: %d cluburi, %d jucatori",
                 self.name, season, len(clubs), result.player_stats)
        return result
