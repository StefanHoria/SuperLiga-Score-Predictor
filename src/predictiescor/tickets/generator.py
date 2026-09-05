"""Generarea biletelor de pariuri pentru meciurile unei zile.

Un bilet arata ca pe site-urile de pariuri: mai multe "picioare" (selectii),
fiecare cu piata, selectia si cota; cota totala e produsul cotelor, iar
castigul potential e miza inmultita cu cota totala.

Reguli respectate la construire:

* **Un singur picior per meci.** Doua selectii din acelasi meci sunt puternic
  corelate (daca gazda castiga, e mai probabil sa fie si peste 1.5 goluri), iar
  inmultirea cotelor ar minti despre sansa reala a biletului. Fiecare strategie
  ia cel mult o selectie per meci.
* **Probabilitati recalibrate.** Inainte de a alege, probabilitatile trec prin
  corectia invatata din biletele decontate anterior (vezi `learning.py`).
* **Strategii diferite, nu variatii ale aceleiasi idei.** De la "sigur" pana la
  "cota mare", fiecare are alt profil de risc.
"""
from __future__ import annotations

import itertools
import logging
import math
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Callable, Iterable

import pandas as pd

from ..utils.localtime import local_time

log = logging.getLogger(__name__)


@dataclass
class Leg:
    """Un picior de bilet: o singura selectie dintr-un meci."""

    match_id: int
    home_team: str
    away_team: str
    kickoff: str
    market: str
    selection: str
    description: str
    probability: float
    offered_odds: float | None = None

    @property
    def fair_odds(self) -> float:
        return float("inf") if self.probability <= 0 else 1.0 / self.probability

    @property
    def odds(self) -> float:
        """Cota folosita la calcul: cea a casei daca o stim, altfel cea corecta."""
        return self.offered_odds if self.offered_odds else self.fair_odds

    @property
    def match_label(self) -> str:
        return f"{self.home_team} — {self.away_team}"


@dataclass
class Ticket:
    """Un bilet complet, gata de afisat sau de salvat."""

    strategy: str
    label: str
    match_day: str
    legs: list[Leg] = field(default_factory=list)
    stake: float = 10.0
    #: sansa reala, calculata prin simulare, cand biletul are ponturi corelate
    correlated_probability: float | None = None

    @property
    def has_same_match_legs(self) -> bool:
        seen = [leg.match_id for leg in self.legs]
        return len(seen) != len(set(seen))

    @property
    def total_odds(self) -> float:
        """Cota totala corecta.

        Pentru ponturi din meciuri diferite e produsul cotelor. Pentru ponturi
        din acelasi meci produsul ar fi gresit - evenimentele sunt corelate -
        deci folosim inversul probabilitatii comune obtinute prin simulare.
        """
        if self.correlated_probability:
            return 1.0 / self.correlated_probability
        odds = 1.0
        for leg in self.legs:
            odds *= leg.odds
        return odds

    @property
    def probability(self) -> float:
        """Sansa ca TOATE ponturile sa iasa."""
        if self.correlated_probability is not None:
            return self.correlated_probability
        p = 1.0
        for leg in self.legs:
            p *= leg.probability
        return p

    @property
    def potential_payout(self) -> float:
        return self.stake * self.total_odds

    @property
    def expected_value(self) -> float:
        """Cat se asteapta sa castigi (sau sa pierzi) in medie, la miza data."""
        return self.probability * self.potential_payout - self.stake

    def to_frame(self) -> pd.DataFrame:
        return pd.DataFrame([
            {
                "meci": leg.match_label,
                "ora": local_time(leg.kickoff, "%H:%M"),
                "piață": leg.market,
                "pontul": leg.description,
                "șansă": leg.probability,
                "cotă": round(leg.odds, 2),
            }
            for leg in self.legs
        ])


# ----------------------------------------------------------------------
# Strategii
# ----------------------------------------------------------------------
@dataclass
class Strategy:
    """Profilul unei strategii de bilet."""

    name: str
    label: str
    description: str
    #: intervalul de probabilitate acceptat pentru un picior
    prob_range: tuple[float, float]
    #: cate picioare are biletul
    legs: tuple[int, int]
    #: piete permise (None = toate)
    markets: tuple[str, ...] | None = None
    #: cere avantaj fata de cota casei
    require_value: bool = False
    #: permite mai multe ponturi din acelasi meci (probabilitate prin simulare)
    allow_same_match: bool = False


STRATEGIES: list[Strategy] = [
    Strategy(
        "sigur", "Bilet sigur",
        "Selecții cu șansă mare, cotă mică. Puține surprize, câștig modest.",
        prob_range=(0.68, 0.97), legs=(3, 4),
    ),
    Strategy(
        "echilibrat", "Bilet echilibrat",
        "Compromis între șansă și cotă — profilul folosit cel mai des.",
        prob_range=(0.50, 0.72), legs=(3, 4),
    ),
    Strategy(
        "valoare", "Bilet de valoare",
        "Doar selecții unde modelul dă o șansă mai mare decât cota casei.",
        prob_range=(0.35, 0.85), legs=(2, 4), require_value=True,
    ),
    Strategy(
        "goluri", "Bilet pe goluri",
        "Numai piețe de goluri: peste/sub și ambele marchează.",
        prob_range=(0.52, 0.88), legs=(3, 5),
        markets=("total goluri", "ambele marchează", "echipa marchează"),
    ),
    Strategy(
        "statistici", "Bilet pe statistici",
        "Cornere și cartonașe — piețe unde modelul are date detaliate.",
        prob_range=(0.55, 0.90), legs=(2, 4),
        markets=("cornere", "cartonașe"),
    ),
    Strategy(
        "marcatori", "Bilet cu marcatori",
        "Jucători care marchează sau dau pasă decisivă.",
        prob_range=(0.30, 0.75), legs=(2, 3), markets=("marcatori",),
    ),
    Strategy(
        "cota_mare", "Bilet cu cotă mare",
        "Selecții puțin probabile, cotă totală mare. Iese rar, plătește mult.",
        prob_range=(0.18, 0.42), legs=(3, 5),
    ),
    Strategy(
        "rezultate", "Bilet pe rezultate",
        "Numai 1X2 și șansă dublă, câte un meci fiecare.",
        prob_range=(0.45, 0.90), legs=(3, 5),
        markets=("1X2", "șansă dublă"),
    ),
    Strategy(
        "combinat", "Bilet combinat pe un meci",
        "Mai multe ponturi din același meci, cu șansa reală calculată prin "
        "simulare — nu prin înmulțire, care ar minți din cauza corelației.",
        prob_range=(0.35, 0.90), legs=(2, 3),
        markets=("1X2", "șansă dublă", "total goluri", "ambele marchează",
                 "echipa marchează", "cornere", "cartonașe"),
        allow_same_match=True,
    ),
]

STRATEGY_BY_NAME = {s.name: s for s in STRATEGIES}


# ----------------------------------------------------------------------
class TicketGenerator:
    """Construieste bilete pornind de la predictiile pipeline-ului."""

    def __init__(self, pipeline, db, *, calibrator: Callable[[str, float], float] | None = None,
                 stake: float = 10.0) -> None:
        self.pipeline = pipeline
        self.db = db
        self.stake = stake
        # `calibrator(piata, probabilitate) -> probabilitate corectata`
        self.calibrate = calibrator or (lambda market, p: p)

    # ------------------------------------------------------------------
    def candidate_legs(self, match: pd.Series) -> list[Leg]:
        """Toate selectiile rezonabile pentru un meci, cu probabilitatile lor."""
        home, away = match["home_team"], match["away_team"]
        try:
            prediction = self.pipeline.predict(home, away)
        except Exception as exc:  # noqa: BLE001
            log.warning("nu pot prezice %s - %s: %s", home, away, exc)
            return []

        match_id = int(match["match_id"])
        kickoff = str(match["utc_date"])
        offered = self._offered_odds(match_id)
        legs: list[Leg] = []

        def add(market: str, selection: str, description: str, prob: float,
                odds_key: tuple[str, str] | None = None) -> None:
            prob = self.calibrate(market, float(prob))
            if not (0.02 < prob < 0.995):
                return
            legs.append(Leg(
                match_id=match_id, home_team=home, away_team=away,
                kickoff=kickoff, market=market, selection=selection,
                description=description, probability=prob,
                offered_odds=offered.get(odds_key) if odds_key else None,
            ))

        # --- 1X2 ---
        result = prediction.markets["1X2"]
        add("1X2", "gazda", f"{home} câștigă", result["gazda"], ("1x2", "home"))
        add("1X2", "egal", "Egal", result["egal"], ("1x2", "draw"))
        add("1X2", "oaspete", f"{away} câștigă", result["oaspete"], ("1x2", "away"))

        # --- sansa dubla ---
        double = prediction.markets["sansa dubla"]
        add("șansă dublă", "1X", f"{home} sau egal", double["1X"], ("dc", "1x"))
        add("șansă dublă", "X2", f"{away} sau egal", double["X2"], ("dc", "x2"))
        add("șansă dublă", "12", "Fără egal", double["12"], ("dc", "12"))

        # --- goluri ---
        for line in (0.5, 1.5, 2.5, 3.5):
            key = f"total goluri {line}"
            if key not in prediction.markets:
                continue
            ou = prediction.markets[key]
            # Flashscore publica toate liniile, nu doar 2.5 cum facea
            # football-data; le legam pe toate.
            add("total goluri", f"peste {line}", f"Peste {line} goluri",
                ou["peste"], (f"ou_{line}", "over"))
            add("total goluri", f"sub {line}", f"Sub {line} goluri", ou["sub"],
                (f"ou_{line}", "under"))

        btts = prediction.markets["ambele marcheaza"]
        add("ambele marchează", "da", "Ambele echipe marchează", btts["da"],
            ("btts", "yes"))
        add("ambele marchează", "nu", "NU marchează ambele", btts["nu"],
            ("btts", "no"))

        scorer = prediction.markets["echipa marcheaza"]
        add("echipa marchează", "gazda", f"{home} marchează", scorer["gazda"])
        add("echipa marchează", "oaspete", f"{away} marchează", scorer["oaspete"])

        # --- statistici secundare ---
        legs.extend(self._secondary_legs(match_id, home, away, kickoff))

        # --- marcatori ---
        legs.extend(self._scorer_legs(match_id, home, away, kickoff, prediction))
        return legs

    # ------------------------------------------------------------------
    def _secondary_legs(self, match_id: int, home: str, away: str,
                        kickoff: str) -> list[Leg]:
        secondary = self.pipeline.secondary
        if secondary is None or not secondary.models:
            return []

        legs: list[Leg] = []
        specs = [
            ("corners", "cornere", "cornere", [7.5, 8.5, 9.5, 10.5]),
            ("yellow_cards", "yellow_cards", "cartonașe", [2.5, 3.5, 4.5, 5.5]),
        ]
        for stat, _code, market, lines in specs:
            model = secondary.models.get(stat)
            if model is None:
                continue
            for line in lines:
                try:
                    ou = model.match_total_over_under(home, away, line)
                except Exception:  # noqa: BLE001
                    continue
                for side in ("peste", "sub"):
                    prob = self.calibrate(market, float(ou[side]))
                    if not (0.02 < prob < 0.995):
                        continue
                    legs.append(Leg(
                        match_id=match_id, home_team=home, away_team=away,
                        kickoff=kickoff, market=market,
                        selection=f"{side} {line}",
                        description=f"{side.capitalize()} {line} {market}",
                        probability=prob,
                    ))
        return legs

    def _scorer_legs(self, match_id: int, home: str, away: str, kickoff: str,
                     prediction) -> list[Leg]:
        players = self.pipeline.players
        if players is None or not players.fitted:
            return []
        frame = self.pipeline.predict_players(home, away, prediction)
        if frame.empty:
            return []

        legs: list[Leg] = []
        for row in frame.head(8).itertuples():
            prob = self.calibrate("marcatori", float(row.marcheaza))
            if not (0.05 < prob < 0.95):
                continue
            legs.append(Leg(
                match_id=match_id, home_team=home, away_team=away,
                kickoff=kickoff, market="marcatori",
                selection=f"gol::{row.jucator}",
                description=f"{row.jucator} ({row.echipa}) marchează",
                probability=prob,
            ))
        return legs

    # ------------------------------------------------------------------
    def _offered_odds(self, match_id: int) -> dict[tuple[str, str], float]:
        """Cel mai bun pret disponibil pentru acest meci.

        `MAX`, nu `AVG`, din doua motive. Primul: un parior alege casa cu cota
        cea mai buna, deci aia e cota pe care o obtine cu adevarat. Al doilea:
        in tabel stau si case individuale, si agregate (`max`, `fs_max`), iar
        media peste ele amesteca lucruri de naturi diferite si iese un numar
        care nu e cota nimanui.
        """
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT market, selection, MAX(price) AS price FROM odds"
                " WHERE match_id = ? GROUP BY market, selection",
                (match_id,),
            ).fetchall()
        return {(r["market"], r["selection"]): float(r["price"]) for r in rows}

    # ------------------------------------------------------------------
    def build(self, matches: pd.DataFrame, *, count: int = 8,
              match_day: str | None = None,
              strategies: Iterable[str] | None = None) -> list[Ticket]:
        """Genereaza pana la `count` bilete pentru meciurile date."""
        if matches.empty:
            return []

        day = match_day or str(matches.iloc[0]["utc_date"])[:10]
        legs_by_match: dict[int, list[Leg]] = {}
        for _, match in matches.iterrows():
            candidates = self.candidate_legs(match)
            if candidates:
                legs_by_match[int(match["match_id"])] = candidates

        if not legs_by_match:
            return []

        chosen = [STRATEGY_BY_NAME[s] for s in strategies
                  if s in STRATEGY_BY_NAME] if strategies else STRATEGIES
        chosen = self._order_by_weight(chosen)

        tickets: list[Ticket] = []
        seen: set[tuple] = set()
        variants: dict[str, int] = {s.name: 0 for s in chosen}
        attempts = 0
        max_attempts = max(40, count * len(chosen) * 3)

        for strategy in itertools.cycle(chosen):
            if len(tickets) >= count or attempts >= max_attempts:
                break
            attempts += 1
            ticket = self._build_one(strategy, legs_by_match, day,
                                     variant=variants[strategy.name])
            variants[strategy.name] += 1
            if ticket is None:
                continue
            signature = tuple(sorted(
                (leg.match_id, leg.market, leg.selection) for leg in ticket.legs))
            if signature in seen:
                continue
            seen.add(signature)
            tickets.append(ticket)
        return tickets[:count]

    def _order_by_weight(self, strategies: list[Strategy]) -> list[Strategy]:
        """Strategiile care au mers mai bine sunt propuse primele."""
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT strategy, weight FROM strategy_performance").fetchall()
        weights = {r["strategy"]: float(r["weight"]) for r in rows}
        return sorted(strategies, key=lambda s: -weights.get(s.name, 1.0))

    def _build_one(self, strategy: Strategy, legs_by_match: dict[int, list[Leg]],
                   day: str, variant: int = 0) -> Ticket | None:
        if strategy.allow_same_match:
            return self._build_correlated(strategy, legs_by_match, day, variant)

        low, high = strategy.prob_range
        pool: list[Leg] = []

        for match_id, legs in legs_by_match.items():
            eligible = [
                leg for leg in legs
                if low <= leg.probability <= high
                and (strategy.markets is None or leg.market in strategy.markets)
                and (not strategy.require_value or self._has_value(leg))
            ]
            if not eligible:
                continue
            # un singur picior per meci; alegem alt candidat la fiecare varianta
            eligible.sort(key=lambda leg: -leg.probability)
            # fiecare varianta a strategiei ia alt candidat din acelasi meci,
            # asa ca doua bilete ale aceleiasi strategii nu ies identice
            pool.append(eligible[variant % len(eligible)])

        if not pool:
            return None

        min_legs, max_legs = strategy.legs
        # Intr-o zi de SuperLiga se joaca de obicei 2-3 meciuri, deci un bilet
        # de 4 picioare adesea nu incape. Coboram pragul la cate meciuri sunt,
        # dar nu sub 2: un "bilet" cu un singur pont nu e bilet.
        effective_min = max(2, min(min_legs, len(legs_by_match)))
        if len(pool) < effective_min:
            return None

        # pentru bilete sigure luam cele mai probabile; pentru cota mare, invers
        reverse = strategy.name != "cota_mare"
        pool.sort(key=lambda leg: leg.probability, reverse=reverse)
        size = min(max_legs, len(pool))
        # variem si lungimea biletului, ca sa nu iasa toate identice
        if size > effective_min:
            size -= variant % (size - effective_min + 1)
        selected = pool[:size]

        return Ticket(strategy=strategy.name, label=strategy.label,
                      match_day=day, legs=selected, stake=self.stake)

    def _build_correlated(self, strategy: Strategy,
                          legs_by_match: dict[int, list[Leg]],
                          day: str, variant: int) -> Ticket | None:
        """Bilet cu mai multe ponturi din ACELASI meci.

        Aici nu se poate inmulti: doua ponturi din acelasi meci sunt corelate.
        Simulam meciul si numaram in cate desfasurari ies amandoua.
        """
        from ..modeling.simulation import joint_probability, simulate_match

        low, high = strategy.prob_range
        match_ids = sorted(legs_by_match)
        if not match_ids:
            return None
        match_id = match_ids[variant % len(match_ids)]

        eligible = [
            leg for leg in legs_by_match[match_id]
            if low <= leg.probability <= high
            and (strategy.markets is None or leg.market in strategy.markets)
        ]
        if len(eligible) < 2:
            return None

        eligible.sort(key=lambda leg: -leg.probability)
        # evitam ponturi din aceeasi piata (ex. "peste 2.5" cu "sub 2.5")
        selected: list[Leg] = []
        used_markets: set[str] = set()
        for leg in eligible:
            if leg.market in used_markets:
                continue
            selected.append(leg)
            used_markets.add(leg.market)
            if len(selected) >= strategy.legs[1]:
                break
        if len(selected) < 2:
            return None

        first = selected[0]
        try:
            simulation = simulate_match(
                self.pipeline, first.home_team, first.away_team,
                match_id=match_id)
            joint = joint_probability(selected, {match_id: simulation})
        except Exception as exc:  # noqa: BLE001
            log.warning("simulare esuata pentru meciul %s: %s", match_id, exc)
            return None

        if joint <= 0.01:
            return None

        return Ticket(strategy=strategy.name, label=strategy.label,
                      match_day=day, legs=selected, stake=self.stake,
                      correlated_probability=joint)

    @staticmethod
    def _has_value(leg: Leg) -> bool:
        """Cota casei plateste mai mult decat merita, dupa model."""
        if not leg.offered_odds:
            return False
        return leg.offered_odds > leg.fair_odds * 1.05

    # ------------------------------------------------------------------
    def save(self, tickets: list[Ticket]) -> list[int]:
        """Salveaza biletele in baza de date. Intoarce ID-urile create."""
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        ids: list[int] = []
        with self.db.connect() as conn:
            for ticket in tickets:
                cur = conn.execute(
                    "INSERT INTO tickets(created_at, match_day, strategy, label,"
                    " stake, total_odds, probability, expected_value, status,"
                    " legs_total) VALUES (?,?,?,?,?,?,?,?,'deschis',?)",
                    (now, ticket.match_day, ticket.strategy, ticket.label,
                     ticket.stake, ticket.total_odds, ticket.probability,
                     ticket.expected_value, len(ticket.legs)),
                )
                ticket_id = int(cur.lastrowid)
                ids.append(ticket_id)
                for leg in ticket.legs:
                    conn.execute(
                        "INSERT INTO ticket_legs(ticket_id, match_id, market,"
                        " selection, description, probability, fair_odds,"
                        " offered_odds) VALUES (?,?,?,?,?,?,?,?)",
                        (ticket_id, leg.match_id, leg.market, leg.selection,
                         leg.description, leg.probability, leg.fair_odds,
                         leg.offered_odds),
                    )
        log.info("am salvat %d bilete pentru %s", len(ids),
                 tickets[0].match_day if tickets else "-")
        return ids


# ----------------------------------------------------------------------
def matches_on(db, day: date | str, *, include_finished: bool = True) -> pd.DataFrame:
    """Meciurile de SuperLiga dintr-o anumita zi."""
    day_str = day if isinstance(day, str) else day.isoformat()
    sql = """
        SELECT m.match_id, m.season, m.round, m.utc_date, m.status,
               m.home_goals, m.away_goals,
               ht.name AS home_team, at.name AS away_team
        FROM matches m
        JOIN teams ht ON ht.team_id = m.home_team_id
        JOIN teams at ON at.team_id = m.away_team_id
        WHERE substr(m.utc_date, 1, 10) = ?
        ORDER BY m.utc_date
    """
    with db.connect() as conn:
        frame = pd.read_sql_query(sql, conn, params=[day_str])
    if not include_finished and not frame.empty:
        frame = frame[frame["status"] != "finished"]
    return frame.reset_index(drop=True)


def matches_in_range(db, start: date | str, end: date | str) -> pd.DataFrame:
    """Meciurile dintr-un interval de zile - folosit pentru bilete pe etapa."""
    start_str = start if isinstance(start, str) else start.isoformat()
    end_str = end if isinstance(end, str) else end.isoformat()
    sql = """
        SELECT m.match_id, m.season, m.round, m.utc_date, m.status,
               m.home_goals, m.away_goals,
               ht.name AS home_team, at.name AS away_team
        FROM matches m
        JOIN teams ht ON ht.team_id = m.home_team_id
        JOIN teams at ON at.team_id = m.away_team_id
        WHERE substr(m.utc_date, 1, 10) BETWEEN ? AND ?
        ORDER BY m.utc_date
    """
    with db.connect() as conn:
        return pd.read_sql_query(sql, conn, params=[start_str, end_str])


def matches_in_round(db, round_name: str, season: int) -> pd.DataFrame:
    """Toate meciurile unei etape."""
    sql = """
        SELECT m.match_id, m.season, m.round, m.utc_date, m.status,
               m.home_goals, m.away_goals,
               ht.name AS home_team, at.name AS away_team
        FROM matches m
        JOIN teams ht ON ht.team_id = m.home_team_id
        JOIN teams at ON at.team_id = m.away_team_id
        WHERE m.round = ? AND m.season = ?
        ORDER BY m.utc_date
    """
    with db.connect() as conn:
        return pd.read_sql_query(sql, conn, params=[round_name, season])


def next_rounds(db, limit: int = 5) -> list[tuple[str, int, int]]:
    """Etapele urmatoare: (nume etapa, sezon, numar de meciuri nejucate)."""
    sql = """
        SELECT round, season, COUNT(*) AS n, MIN(utc_date) AS first_kickoff
        FROM matches
        WHERE status != 'finished' AND home_goals IS NULL AND round IS NOT NULL
        GROUP BY round, season
        ORDER BY first_kickoff
        LIMIT ?
    """
    with db.connect() as conn:
        rows = conn.execute(sql, (limit,)).fetchall()
    return [(str(r["round"]), int(r["season"]), int(r["n"])) for r in rows]


def next_match_days(db, limit: int = 10) -> list[str]:
    """Zilele urmatoare in care se joaca, cu numarul de meciuri."""
    sql = """
        SELECT substr(utc_date, 1, 10) AS day, COUNT(*) AS n
        FROM matches
        WHERE status != 'finished' AND home_goals IS NULL
        GROUP BY day ORDER BY day LIMIT ?
    """
    with db.connect() as conn:
        return [r["day"] for r in conn.execute(sql, (limit,)).fetchall()]


def recent_match_days(db, limit: int = 20) -> list[str]:
    sql = """
        SELECT substr(utc_date, 1, 10) AS day
        FROM matches WHERE status = 'finished'
        GROUP BY day ORDER BY day DESC LIMIT ?
    """
    with db.connect() as conn:
        return [r["day"] for r in conn.execute(sql, (limit,)).fetchall()]
