"""Provider LPF.ro - site-ul oficial al Ligii Profesioniste de Fotbal.

Rezolva limitarea principala a aplicatiei: planul gratuit API-Football nu da
acces la sezonul curent, deci pana acum nu aveam cornere, cartonase si suturi
pentru 2026/27. LPF le publica oficial, pentru fiecare meci, pe pagini publice
si fara protectie anti-bot.

Structura paginii (`https://lpf.ro/statistici/{id}`):

    <td class="stats_subtitle">Cornere</td>          <- eticheta
    <tr>
      <td class="stats_val_gazda"><div class="val_gazda">10</div>...
      <td class="stats_val_oaspeti">...<div class="val_oaspeti">4</div>

ID-urile sunt secventiale de la inceputul sezonului (8 meciuri pe etapa), deci
nu avem nevoie de o pagina de listare: parcurgem intervalul si ne oprim cand
dam de meciuri nejucate.

ATENTIE - doua capcane, ambele tratate:

1. Paginile meciurilor viitoare exista deja. Unele au toate valorile zero;
   altele afiseaza cifre care arata perfect plauzibil (suturi, posesie,
   cornere), desi meciul nu s-a disputat. Nu am putut stabili ce reprezinta
   acele cifre.

2. De aceea garantia nu sta in ce citim de pe pagina, ci in baza de date:
   providerul scrie DOAR pe meciuri care au deja un scor confirmat de alta
   sursa (football-data.co.uk sau Transfermarkt). LPF nu creeaza niciodata
   meciuri si nu decide ce s-a jucat - rolul lui e strict de imbogatire.
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

BASE = "https://lpf.ro"

#: LPF publica orele in ora Romaniei (EET/EEST), nu in UTC.
SITE_TIMEZONE = ZoneInfo("Europe/Bucharest")

#: eticheta de pe site -> coloana din match_team_stats
STAT_MAP: dict[str, str] = {
    "Cartonașe galbene": "yellow_cards",
    "Cartonașe roșii": "red_cards",
    "Posesia mingii": "possession",
    "Atacuri": "attacks",
    "Șuturi": "shots",
    "Șuturi pe poartă": "shots_on_target",
    "Șuturi blocate": "shots_blocked",
    "Șuturi din afara careului": "shots_outside_box",
    "Total pase": "passes",
    "Pase precise": "passes_accurate",
    "Faulturi": "fouls",
    "Driblling": "dribbles",
    "Dribling": "dribbles",
    "Pase în careu": "passes_into_box",
    "Cornere": "corners",
    "Lovituri libere": "free_kicks",
    "Interceptări": "interceptions",
    "Deposedări": "tackles",
    "Dueluri câștigate": "duels_won",
}

#: iconita evenimentului -> tipul lui
EVENT_ICONS: dict[str, str] = {
    "gol.png": "goal",
    "autogol.png": "own_goal",
    "penalty.png": "goal",
    "cartonas_galben.png": "yellow",
    "cartonas_rosu.png": "red",
    "cartonas_galben2.png": "second_yellow",
    "schimbare.png": "substitution",
}

#: durata regulamentara; prelungirile nu sunt publicate, deci minutele sunt
#: aproximative (un jucator schimbat in minutul 90+2 apare cu 90)
FULL_TIME = 90


LUNI = {
    "ianuarie": 1, "februarie": 2, "martie": 3, "aprilie": 4, "mai": 5,
    "iunie": 6, "iulie": 7, "august": 8, "septembrie": 9, "octombrie": 10,
    "noiembrie": 11, "decembrie": 12,
}

_DATE_RE = re.compile(
    r"(\d{1,2})\s+(" + "|".join(LUNI) + r")\s+(20\d\d)(?:\s*,\s*(\d{1,2}):(\d{2}))?",
    re.IGNORECASE,
)
_TITLE_RE = re.compile(r"^(.*?)\s+-\s+(.*?)\s+-\s+Etapa\s+(\d+)", re.IGNORECASE)


def _shirt_number(node) -> str | None:
    """'18.ALMEIDA PINHO' -> '18'. Nodurile de schimbare poarta si o sageata."""
    if node is None:
        return None
    m = re.match(r"\s*(\d+)\s*\.", node.get_text(" ", strip=True))
    return m.group(1) if m else None


def normalize_player_name(raw: str) -> str:
    """'Mihaita Alexandru CHIPCIU' -> 'Mihaita Alexandru Chipciu'.

    LPF scrie numele de familie cu majuscule. Il aducem la forma obisnuita ca
    sa se potriveasca vizual cu numele de la Transfermarkt.
    """
    words = []
    for word in str(raw).split():
        words.append(word.capitalize() if word.isupper() and len(word) > 1
                     else word)
    return " ".join(words)


def parse_date(text: str) -> datetime | None:
    """'28 august 2026, 21:00' -> datetime aware."""
    m = _DATE_RE.search(text or "")
    if not m:
        return None
    day, month_name, year = int(m.group(1)), m.group(2).lower(), int(m.group(3))
    hour = int(m.group(4)) if m.group(4) else 0
    minute = int(m.group(5)) if m.group(5) else 0
    try:
        local = datetime(year, LUNI[month_name], day, hour, minute,
                         tzinfo=SITE_TIMEZONE)
    except (KeyError, ValueError):
        return None
    return local.astimezone(timezone.utc)


class LpfProvider(DataProvider):
    name = "lpf"
    provides = (
        "suturi", "cornere", "cartonase", "posesie", "pase", "faulturi",
        "atacuri", "dueluri", "interceptari", "driblinguri",
        "formatii", "marcatori", "minute jucatori",
    )

    def __init__(self, *args: Any, max_pages: int = 60, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.max_pages = max_pages
        self.first_id = int(self.cfg.league.get("lpf_first_match_id", 3592))
        self.matches_per_round = int(
            self.cfg.league.get("lpf_matches_per_round", 8))
        # site romanesc, fara anti-bot: nu e nevoie de pauze mari
        self.http.delay = min(self.http.delay, 1.5)
        self._last_player_rows = 0

    @property
    def available(self) -> bool:
        return self.cfg.secrets.lpf_enabled

    # ------------------------------------------------------------------
    def fetch_match(self, match_page_id: int) -> dict[str, Any] | None:
        """Citeste o pagina de statistici. None daca meciul nu s-a jucat inca."""
        url = f"{BASE}/statistici/{match_page_id}"
        try:
            html = self.http.get_text(url, ttl_hours=6)
        except Exception as exc:  # noqa: BLE001
            log.debug("[%s] %s indisponibil: %s", self.name, url, exc)
            return None

        soup = BeautifulSoup(html, "lxml")
        title = soup.title.get_text(strip=True) if soup.title else ""
        header = _TITLE_RE.match(title)
        if not header:
            return None

        home, away, round_no = (header.group(1).strip(),
                                header.group(2).strip(),
                                header.group(3))
        stats = self._parse_stats(soup)
        if not stats:
            return None

        # Meci neînceput: pagina exista, dar toate valorile sunt zero.
        if not self._has_real_values(stats):
            log.debug("[%s] meciul %s nu s-a jucat inca", self.name,
                      match_page_id)
            return None

        kickoff = self._parse_kickoff(soup)
        if kickoff is None:
            return None

        events = self._parse_events(soup)

        return {
            "page_id": match_page_id,
            "home": home,
            "away": away,
            "round": f"Etapa {round_no}",
            "kickoff": kickoff,
            "stats": stats,
            "lineups": self._parse_lineups(soup, events),
            "events": events,
        }

    # ------------------------------------------------------------------
    @staticmethod
    def _parse_stats(soup: BeautifulSoup) -> dict[str, tuple[str, str]]:
        """Eticheta -> (valoare gazda, valoare oaspete), brute, ca text."""
        out: dict[str, tuple[str, str]] = {}
        for cell in soup.select("td.stats_subtitle"):
            label = cell.get_text(" ", strip=True)
            row = cell.find_parent("tr")
            values_row = row.find_next_sibling("tr") if row else None
            if values_row is None:
                continue
            home = values_row.select_one(".val_gazda")
            away = values_row.select_one(".val_oaspeti")
            if home is None or away is None:
                continue
            out[label] = (home.get_text(strip=True), away.get_text(strip=True))
        return out

    @staticmethod
    def _has_real_values(stats: dict[str, tuple[str, str]]) -> bool:
        """True daca macar o statistica e diferita de zero."""
        for home, away in stats.values():
            for raw in (home, away):
                digits = re.sub(r"\D", "", raw or "")
                if digits and digits != "0" * len(digits):
                    return True
        return False

    @staticmethod
    def _parse_kickoff(soup: BeautifulSoup) -> datetime | None:
        for node in soup.find_all(string=_DATE_RE):
            parsed = parse_date(str(node))
            if parsed is not None:
                return parsed
        return None

    # ------------------------------------------------------------------
    # Formatii si evenimente (goluri, cartonase, schimbari)
    # ------------------------------------------------------------------
    @staticmethod
    def _parse_lineups(soup: BeautifulSoup,
                       events: list[dict[str, Any]] | None = None
                       ) -> dict[str, list[dict[str, Any]]]:
        """Formatiile celor doua echipe: numar de tricou, nume, titular/rezerva.

        Pagina are patru blocuri de cate 11 randuri - titulari si rezerve
        pentru fiecare echipa - dar nimic din marcaj nu spune carei echipe ii
        apartine un bloc. Le identificam in doi pasi:

        1. **Titularii** se recunosc dupa formatiile desenate pe teren
           (`.teren_jucatori`, gazda apoi oaspete): blocul se suprapune aproape
           complet peste numerele de acolo.
        2. **Rezervele** nu se suprapun cu nimic, deci le legam prin schimbari:
           jucatorul intrat intr-o schimbare a gazdei e rezerva gazdelor.
           Daca meciul nu a avut schimbari, cadem pe ordinea din pagina, in
           care blocurile alterneaza gazda/oaspete.
        """
        pitches = soup.select(".teren_jucatori")
        if len(pitches) < 2:
            return {}

        def numbers_on_pitch(block) -> set[str]:
            found = set()
            for node in block.select(".nume_jucator"):
                m = re.match(r"\s*(\d+)\s*\.", node.get_text(" ", strip=True))
                if m:
                    found.add(m.group(1))
            return found

        starters = {"home": numbers_on_pitch(pitches[0]),
                    "away": numbers_on_pitch(pitches[1])}

        # numerele intrate pe teren, pe echipe - identifica blocurile de rezerve
        entered: dict[str, set[str]] = {"home": set(), "away": set()}
        for event in events or []:
            if event.get("kind") == "substitution" and event.get("in"):
                entered[event["side"]].add(event["in"])

        blocks: list[list[dict[str, Any]]] = []
        for container in soup.select(".lista_titulari"):
            rows = container.select(".titular-row")
            if not rows:
                continue
            players: list[dict[str, Any]] = []
            for row in rows:
                number_node = row.select_one(".nrt")
                if number_node is None:
                    continue
                number = number_node.get_text(strip=True)
                name = row.get_text(" ", strip=True)[len(number):].strip()
                # marcajul de capitan nu face parte din nume
                name = re.sub(r"\s*\(C\)\s*$", "", name).strip()
                if number and name:
                    players.append({"number": number, "name": name})
            if players:
                blocks.append(players)

        # --- clasificam blocurile ---------------------------------
        # Cele doua echipe folosesc adesea aceleasi numere de tricou (#1, #7,
        # #10), deci simpla suprapunere cu formatia de pe teren nu le poate
        # separa. Cerem egalitate exacta de multimi, iar cand tot e ambiguu
        # cadem pe ordinea din pagina: titulari gazda, titulari oaspete,
        # rezerve gazda, rezerve oaspete.
        starter_blocks: list[tuple[list[dict[str, Any]], str | None]] = []
        bench_blocks: list[tuple[list[dict[str, Any]], str | None]] = []

        for players in blocks:
            numbers = {p["number"] for p in players}
            matches_home = numbers == starters["home"]
            matches_away = numbers == starters["away"]

            if matches_home or matches_away:
                hint = None if matches_home and matches_away else (
                    "home" if matches_home else "away")
                starter_blocks.append((players, hint))
                continue

            home_bench = len(numbers & entered["home"])
            away_bench = len(numbers & entered["away"])
            hint = None
            if home_bench != away_bench:
                hint = "home" if home_bench > away_bench else "away"
            bench_blocks.append((players, hint))

        lineups: dict[str, list[dict[str, Any]]] = {"home": [], "away": []}

        def assign(group: list[tuple[list[dict[str, Any]], str | None]],
                   is_starter: bool) -> None:
            free = ["home", "away"]
            # intai blocurile cu indiciu clar, ca sa nu le fure ordinea
            ordered = sorted(range(len(group)), key=lambda i: group[i][1] is None)
            resolved: dict[int, str] = {}
            for index in ordered:
                players, hint = group[index]
                if hint in free:
                    resolved[index] = hint
                    free.remove(hint)
            for index in range(len(group)):
                if index in resolved:
                    continue
                if not free:
                    break
                resolved[index] = free.pop(0)

            for index, side in sorted(resolved.items()):
                known = {p["number"] for p in lineups[side]}
                for player in group[index][0]:
                    if player["number"] in known:
                        continue
                    player["is_starter"] = is_starter
                    lineups[side].append(player)

        assign(starter_blocks, True)
        assign(bench_blocks, False)
        return lineups

    @staticmethod
    def _parse_events(soup: BeautifulSoup) -> list[dict[str, Any]]:
        """Cronologia meciului: goluri, cartonase si schimbari."""
        events: list[dict[str, Any]] = []
        for node in soup.select(".event_gazda, .event_oaspeti"):
            classes = node.get("class") or []
            side = "home" if "event_gazda" in classes else "away"

            icon = node.select_one(".icon img")
            source = (icon.get("src") or "").rsplit("/", 1)[-1] if icon else ""
            kind = EVENT_ICONS.get(source)
            if kind is None:
                continue

            minute_node = node.select_one(".minut")
            minute = None
            if minute_node is not None:
                m = re.search(r"(\d+)", minute_node.get_text(strip=True))
                if m:
                    minute = int(m.group(1))

            if kind == "substitution":
                out_node = node.select_one(".jucator-out")
                in_node = node.select_one(".jucator-in")
                events.append({
                    "side": side, "kind": kind, "minute": minute,
                    "out": _shirt_number(out_node),
                    "in": _shirt_number(in_node),
                })
                continue

            # De obicei "17.LUKIC", dar uneori numarul lipseste ("MATEI").
            # Pastram si numele, ca sa putem lega evenimentul si asa.
            text = node.get_text(" ", strip=True)
            if minute_node is not None:
                text = text.replace(minute_node.get_text(strip=True), " ")
            text = " ".join(text.split())
            m = re.match(r"\s*(\d+)\s*\.\s*(.*)$", text)
            events.append({
                "side": side, "kind": kind, "minute": minute,
                "number": m.group(1) if m else None,
                "name": (m.group(2) if m else text).strip(),
            })
        return events

    @classmethod
    def _player_stats(cls, lineups: dict[str, list[dict[str, Any]]],
                      events: list[dict[str, Any]]
                      ) -> dict[str, dict[str, dict[str, Any]]]:
        """Combina formatiile cu evenimentele in statistici per jucator.

        Minutele sunt reconstruite din schimbari: titularii joaca pana cand
        sunt inlocuiti, rezervele din minutul intrarii pana la final. Fara
        prelungiri, pentru ca LPF nu le publica.
        """
        out: dict[str, dict[str, dict[str, Any]]] = {"home": {}, "away": {}}
        for side, players in lineups.items():
            for player in players:
                out[side][player["number"]] = {
                    "name": player["name"],
                    "is_starter": 1 if player["is_starter"] else 0,
                    "minutes": float(FULL_TIME) if player["is_starter"] else 0.0,
                    "goals": 0.0, "assists": 0.0,
                    "yellow_cards": 0.0, "red_cards": 0.0,
                }

        for event in events:
            side = event["side"]
            squad = out.get(side, {})
            minute = event.get("minute")

            if event["kind"] == "substitution":
                going_out, coming_in = event.get("out"), event.get("in")
                if going_out in squad and minute is not None:
                    squad[going_out]["minutes"] = float(min(minute, FULL_TIME))
                if coming_in in squad and minute is not None:
                    squad[coming_in]["minutes"] = float(
                        max(0, FULL_TIME - minute))
                    squad[coming_in]["is_starter"] = 0
                continue

            number = cls._match_event_player(squad, event)
            if number is None:
                log.debug("eveniment nelegat de niciun jucator: %s", event)
                continue
            if event["kind"] == "goal":
                squad[number]["goals"] += 1.0
            elif event["kind"] == "own_goal":
                pass  # autogolul nu se trece in contul marcatorului
            elif event["kind"] == "yellow":
                squad[number]["yellow_cards"] += 1.0
            elif event["kind"] in ("red", "second_yellow"):
                squad[number]["red_cards"] += 1.0
                if minute is not None:
                    squad[number]["minutes"] = float(min(minute, FULL_TIME))
        return out

    @staticmethod
    def _match_event_player(squad: dict[str, dict[str, Any]],
                            event: dict[str, Any]) -> str | None:
        """Numarul de tricou al jucatorului dintr-un eveniment.

        Numarul e de obicei prezent; cand lipseste, cautam dupa numele de
        familie in lotul echipei respective. Daca doi jucatori au acelasi nume
        de familie, nu ghicim.
        """
        from ..utils.teamnames import strip_diacritics

        number = event.get("number")
        if number and number in squad:
            return number

        raw = (event.get("name") or "").strip()
        if not raw:
            return None
        surname = strip_diacritics(raw).lower().split()[-1]

        hits = [
            num for num, player in squad.items()
            if strip_diacritics(player["name"]).lower().split()[-1] == surname
        ]
        return hits[0] if len(hits) == 1 else None

    # ------------------------------------------------------------------
    def _columns(self, side: int, stats: dict[str, tuple[str, str]]
                 ) -> dict[str, float | None]:
        """Traduce etichetele in coloanele bazei, pentru o echipa."""
        out: dict[str, float | None] = {}
        for label, values in stats.items():
            column = STAT_MAP.get(label)
            if column is None:
                continue
            out[column] = self.to_float(values[side])

        # campuri derivate, ca modelele sa aiba acelasi set ca de la alte surse
        shots, outside = out.get("shots"), out.get("shots_outside_box")
        if shots is not None and outside is not None:
            out["shots_inside_box"] = max(0.0, shots - outside)
        on_target, blocked = out.get("shots_on_target"), out.get("shots_blocked")
        if shots is not None and on_target is not None:
            off = shots - on_target - (blocked or 0.0)
            out["shots_off_target"] = max(0.0, off)
        passes, accurate = out.get("passes"), out.get("passes_accurate")
        if passes and accurate is not None:
            out["pass_accuracy"] = round(100.0 * accurate / passes, 1)
        return out

    # ------------------------------------------------------------------
    def ingest_match(self, payload: dict[str, Any], season: int) -> int:
        """Adauga statisticile la un meci despre care STIM ca s-a jucat.

        LPF publica pagini de statistici si pentru meciuri care inca nu s-au
        disputat, cu valori care arata plauzibil (suturi, posesie, cornere).
        Nu am putut stabili ce reprezinta - previzualizare, medii sau altceva -
        asa ca nu ne bazam pe ele: providerul scrie DOAR pe meciuri care au deja
        un scor in baza, adus de football-data.co.uk sau Transfermarkt.

        Consecinta: LPF nu creeaza niciodata meciuri si nu decide ce s-a jucat.
        Rolul lui este strict de imbogatire. Intoarce nr. de echipe scrise.
        """
        match_id = self._finished_match_id(season, payload)
        if match_id is None:
            log.debug("[%s] sar peste %s - %s: meciul nu are scor confirmat",
                      self.name, payload["home"], payload["away"])
            return 0

        with self.db.connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO match_external_ids"
                "(match_id, source, ext_id) VALUES (?,?,?)",
                (match_id, self.name, str(payload["page_id"])),
            )

        players = self._player_stats(payload.get("lineups") or {},
                                     payload.get("events") or [])

        self._last_player_rows = 0
        written = 0
        for side, (team_name, is_home) in enumerate(
                ((payload["home"], True), (payload["away"], False))):
            columns = self._columns(side, payload["stats"])
            if not columns:
                continue
            team_id = self.db.upsert_team(team_name)
            self.db.upsert_team_stats(match_id, team_id, is_home, columns,
                                      source=self.name)
            written += 1

            key = "home" if is_home else "away"
            self._last_player_rows += self._write_players(
                match_id, team_id, players.get(key, {}))
        return written

    # ------------------------------------------------------------------
    def _write_players(self, match_id: int, team_id: int,
                       squad: dict[str, dict[str, Any]]) -> int:
        """Scrie statisticile pe jucator pentru o echipa dintr-un meci."""
        written = 0
        used: set[int] = set()
        for stats in squad.values():
            if not stats.get("minutes"):
                continue  # rezerva neintrata: nu are ce raporta
            name = normalize_player_name(stats["name"])
            player_id = self._resolve_player(name, team_id, used)
            used.add(player_id)
            self.db.upsert_player_stats(
                match_id, player_id, team_id,
                {
                    "minutes": stats["minutes"],
                    "is_starter": stats["is_starter"],
                    "goals": stats["goals"],
                    "yellow_cards": stats["yellow_cards"],
                    "red_cards": stats["red_cards"],
                },
                source=self.name,
            )
            written += 1
        return written

    def _resolve_player(self, name: str, team_id: int,
                        used: set[int] | None = None) -> int:
        """Gaseste jucatorul existent al echipei sau il creeaza.

        LPF scrie numele complet ("Mihaita Alexandru Chipciu"), Transfermarkt
        forma scurta ("Mihai Chipciu"). Fara potrivire nu am avea legatura
        intre aparitiile reale si predictii.

        Numele de familie SINGUR nu e suficient: un lot poate avea doi "Lopes"
        sau doi "Fernandes", iar contopirea lor ar face ca statisticile unuia
        sa le suprascrie pe ale celuilalt. Cerem, pe langa numele de familie,
        si o potrivire pe prenume; daca raman mai multi candidati sau candidatul
        e deja folosit in acest meci, cream o inregistrare distincta.
        """
        from ..utils.teamnames import strip_diacritics

        def tokens(value: str) -> list[str]:
            return strip_diacritics(str(value)).lower().replace("-", " ").split()

        wanted = tokens(name)
        if not wanted:
            return self.db.upsert_player(name, team_id)

        surname, given = wanted[-1], set(wanted[:-1])
        used = used or set()

        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT player_id, name FROM players WHERE team_id = ?",
                (team_id,),
            ).fetchall()

        candidates: list[tuple[int, int]] = []
        for row in rows:
            existing = tokens(row["name"])
            if not existing or existing[-1] != surname:
                continue
            player_id = int(row["player_id"])
            if player_id in used:
                continue
            shared = given & set(existing[:-1])
            initials = {t[0] for t in given} & {t[0] for t in existing[:-1]}
            score = 2 * len(shared) + len(initials)
            candidates.append((score, player_id))

        if candidates:
            candidates.sort(reverse=True)
            best_score = candidates[0][0]
            tied = [pid for score, pid in candidates if score == best_score]
            # un singur candidat clar, si numai daca prenumele confirma
            if len(tied) == 1 and (best_score > 0 or len(candidates) == 1):
                return tied[0]

        # Numele portugheze/braziliene scapa regulii de mai sus: "Joao Paulo"
        # (Transfermarkt) si "Joao Paulo Moreira Fernandes" (LPF) au nume de
        # familie diferite, desi sunt acelasi jucator. Cerem in schimb doua
        # cuvinte comune - suficient de strict cat sa nu uneasca oameni diferiti.
        wanted_set = set(wanted)
        overlaps: list[tuple[int, int]] = []
        for row in rows:
            player_id = int(row["player_id"])
            if player_id in used:
                continue
            shared = wanted_set & set(tokens(row["name"]))
            if len(shared) >= 2:
                overlaps.append((len(shared), player_id))
        if len(overlaps) == 1:
            return overlaps[0][1]

        # Ultima incercare: acelasi nume scris usor diferit ("Marinos Tzioni"
        # vs "Marinos Tzionis"). Pragul e ridicat intentionat - la 0.92 doua
        # nume apropiate se unesc, dar doi jucatori diferiti nu.
        from difflib import SequenceMatcher

        target = " ".join(wanted)
        close: list[tuple[float, int]] = []
        for row in rows:
            player_id = int(row["player_id"])
            if player_id in used:
                continue
            ratio = SequenceMatcher(
                None, target, " ".join(tokens(row["name"]))).ratio()
            if ratio >= 0.92:
                close.append((ratio, player_id))
        if len(close) == 1:
            return close[0][1]

        # Inainte de a crea un rand nou: exista jucatorul la ALTA echipa?
        #
        # Un transfer in timpul sezonului arata exact asa - LPF il vede in
        # formatia clubului nou, dar el e inregistrat la cel vechi, deci nu se
        # potriveste cu nimeni. Fara verificarea asta ieseau doi Korenica, unul
        # la CFR Cluj si unul la FCSB, cu istoricul rupt in doua.
        mutat = self.db.player_at_other_team(name, team_id)
        if mutat is not None:
            log.info("jucator transferat: %s -> echipa %s", name, team_id)
            self.db.move_player(mutat, team_id)
            return mutat

        return self.db.upsert_player(name, team_id)

    # ------------------------------------------------------------------
    def fetch_season(self, season: int) -> IngestResult:
        """Parcurge paginile sezonului curent si aduce statisticile.

        LPF publica doar sezonul in curs pe acest interval de ID-uri, deci
        pentru alte sezoane nu facem nimic.
        """
        result = IngestResult(source=self.name)
        if season != self.cfg.current_season:
            log.debug("[%s] sezonul %s nu e acoperit", self.name, season)
            return result

        known = self._known_page_ids()
        misses = skipped = 0
        for offset in range(self.max_pages * self.matches_per_round):
            page_id = self.first_id + offset
            if page_id in known:
                continue
            payload = self.fetch_match(page_id)
            if payload is None:
                misses += 1
                # o etapa intreaga fara meciuri jucate => am ajuns in viitor
                if misses >= self.matches_per_round * 2:
                    break
                continue
            misses = 0
            try:
                written = self.ingest_match(payload, season)
            except Exception as exc:  # noqa: BLE001
                result.errors.append(f"meci {page_id}: {exc}")
                continue
            if written:
                result.team_stats += written
                result.matches += 1
                result.player_stats += self._last_player_rows
            else:
                # pagina exista, dar meciul nu are inca scor confirmat
                skipped += 1

        log.info(
            "[%s] sezon %s: %d meciuri imbogatite, %d randuri de statistici"
            " (%d pagini sarite - meciuri fara scor confirmat)",
            self.name, season, result.matches, result.team_stats, skipped,
        )
        self.skipped_without_score = skipped
        return result

    def _finished_match_id(self, season: int,
                           payload: dict[str, Any]) -> int | None:
        """Gaseste meciul JUCAT care corespunde paginii LPF.

        Cautam dupa (sezon, echipe, zi +/- 3), la fel ca `upsert_match`, dar
        fara sa inseram nimic: daca meciul nu exista sau nu are scor, LPF nu
        are ce imbogati.
        """
        from ..utils.teamnames import canonical_team

        home_id = self.db.team_id_by_name(canonical_team(payload["home"]))
        away_id = self.db.team_id_by_name(canonical_team(payload["away"]))
        if home_id is None or away_id is None:
            return None

        day = payload["kickoff"].date().isoformat()
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT match_id FROM matches"
                " WHERE season = ? AND home_team_id = ? AND away_team_id = ?"
                "   AND home_goals IS NOT NULL"
                "   AND abs(julianday(substr(utc_date, 1, 10))"
                "         - julianday(?)) <= 3",
                (season, home_id, away_id, day),
            ).fetchone()
        return int(row["match_id"]) if row else None

    def _known_page_ids(self) -> set[int]:
        """Paginile complet procesate, ca sa nu le recitim la fiecare rulare.

        Cerem si statistici de echipa, si aparitii de jucatori: daca adaugam
        ulterior un tip nou de date, meciurile vechi sunt reprocesate automat.
        """
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT e.ext_id FROM match_external_ids e"
                " WHERE e.source = ?"
                "   AND EXISTS (SELECT 1 FROM match_team_stats s"
                "               WHERE s.match_id = e.match_id)"
                "   AND EXISTS (SELECT 1 FROM player_match_stats p"
                "               WHERE p.match_id = e.match_id)",
                (self.name,),
            ).fetchall()
        out: set[int] = set()
        for row in rows:
            try:
                out.add(int(row["ext_id"]))
            except (TypeError, ValueError):
                continue
        return out

    def fetch_upcoming(self) -> IngestResult:
        """LPF nu aduce program - calendarul vine de la Transfermarkt."""
        return IngestResult(source=self.name)
