"""Probabilitati recalculate in timpul meciului.

La 1-0 in minutul 70, sansele nu mai sunt cele de dinainte de start. Modelul de
dinaintea meciului da ritmul de golare al fiecarei echipe (lambda pe 90 de
minute); din el rezulta direct si situatia din teren:

    goluri ramase ~ Poisson(lambda * minute_ramase / 90)

Scorul final = scorul curent + golurile ramase. Restul (1X2, peste/sub, ambele
marcheaza) se deduce din aceeasi matrice, cu functiile deja existente.

O precizare de onestitate: corectia Dixon-Coles pentru scorurile mici NU se
aplica aici. Ea descrie dependenta dintre golurile unui meci INTREG; pe un
rest de meci nu are aceeasi semnificatie, iar aplicarea ei ar fi o extindere
nejustificata. Consecinta practica e mica - contează mai ales spre finalul
meciului, cand raman putine goluri de marcat oricum.

Ce NU modeleaza: eliminarile, schimbarea de tactica dupa gol, oboseala. Un
model de ritm simplu, nu unul de joc.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy.stats import poisson

from .markets import (
    both_teams_to_score, double_chance, over_under, result_1x2,
)

log = logging.getLogger(__name__)

FULL_TIME = 90

#: cate goluri in plus modelam peste scorul curent
MAX_REMAINING = 8


@dataclass
class InPlayState:
    """Situatia din teren la un moment dat."""

    home_team: str
    away_team: str
    home_goals: int
    away_goals: int
    minute: int

    @property
    def remaining_fraction(self) -> float:
        """Cat din meci a mai ramas, intre 0 si 1."""
        left = max(0, FULL_TIME - int(self.minute))
        return left / FULL_TIME

    @property
    def score(self) -> str:
        return f"{self.home_goals}-{self.away_goals}"


def remaining_matrix(lam_home: float, lam_away: float,
                     fraction: float) -> np.ndarray:
    """Distributia golurilor care se mai pot marca pana la final."""
    size = MAX_REMAINING + 1
    goals = np.arange(size)
    rate_home = max(1e-9, lam_home * fraction)
    rate_away = max(1e-9, lam_away * fraction)
    matrix = np.outer(poisson.pmf(goals, rate_home),
                      poisson.pmf(goals, rate_away))
    total = matrix.sum()
    return matrix / total if total > 0 else matrix


def final_score_matrix(pipeline, state: InPlayState) -> np.ndarray:
    """Matricea scorului FINAL, tinand cont de ce s-a intamplat deja."""
    lam_home, lam_away = pipeline.goals.expected_goals(
        state.home_team, state.away_team)
    rest = remaining_matrix(lam_home, lam_away, state.remaining_fraction)

    size = rest.shape[0] + max(state.home_goals, state.away_goals)
    final = np.zeros((size, size))
    for i in range(rest.shape[0]):
        for j in range(rest.shape[1]):
            final[state.home_goals + i, state.away_goals + j] += rest[i, j]
    return final


def live_markets(pipeline, state: InPlayState) -> dict[str, Any]:
    """Toate pietele principale, recalculate pentru situatia din teren."""
    matrix = final_score_matrix(pipeline, state)
    size = matrix.shape[0]
    index = np.arange(size)

    markets: dict[str, Any] = {
        "1X2": result_1x2(matrix),
        "sansa dubla": double_chance(matrix),
        "ambele marcheaza": both_teams_to_score(matrix),
    }
    for line in (0.5, 1.5, 2.5, 3.5, 4.5):
        markets[f"total goluri {line}"] = over_under(matrix, line)

    return {
        "matrice": matrix,
        "piete": markets,
        "goluri_asteptate_final": (
            float((matrix.sum(axis=1) * index).sum()),
            float((matrix.sum(axis=0) * index).sum()),
        ),
        "minut": state.minute,
        "scor": state.score,
    }


def compare_with_pre_match(pipeline, state: InPlayState) -> dict[str, Any]:
    """Cat s-au schimbat sansele fata de dinainte de start."""
    before = pipeline.predict(state.home_team, state.away_team).markets["1X2"]
    after = live_markets(pipeline, state)["piete"]["1X2"]
    return {
        "inainte": before,
        "acum": after,
        "schimbare": {k: after[k] - before[k] for k in before},
    }


# ----------------------------------------------------------------------
def ticket_status_live(db, state: InPlayState, match_id: int,
                       pipeline=None,
                       stats: dict[str, tuple[float, float]] | None = None,
                       ) -> list[dict[str, Any]]:
    """Cum stau ponturile de pe acest meci, chiar acum.

    Un pont poate fi deja decis inainte de fluierul final: "peste 1.5 goluri"
    la 2-0 in minutul 60 nu mai poate pica. Le impartim in trei: deja iesite,
    deja pierdute si inca in joc - iar pentru cele in joc dam sansa curenta.

    `stats` sunt cartonasele si cornerele de pana acum, ca {coloana: (gazda,
    oaspete)}. Fara ele, un pont pe cartonase ramanea „nedecidabil" tot
    meciul, desi numarul lor se stie in orice clipa.
    """
    from ..tickets.settlement import LOST, MatchFacts, WON, settle_leg

    with db.connect() as conn:
        legs = conn.execute(
            "SELECT l.*, t.label, t.status FROM ticket_legs l"
            " JOIN tickets t ON t.ticket_id = l.ticket_id"
            " WHERE l.match_id = ?", (match_id,)).fetchall()
    if not legs:
        return []

    # Situatia din teren, ca si cum meciul s-ar fi incheiat acum. Ne spune ce
    # pont e deja decis: ce e castigat la scorul curent si nu mai poate fi
    # pierdut ramane castigat doar daca golurile viitoare nu-l pot rasturna -
    # de aceea comparam si cu varianta "s-au mai marcat goluri".
    stats = stats or {}
    now = MatchFacts(
        match_id=match_id, home_goals=state.home_goals,
        away_goals=state.away_goals,
        home_stats={name: pair[0] for name, pair in stats.items()},
        away_stats={name: pair[1] for name, pair in stats.items()},
        scorers=set(), has_player_data=False,
    )

    live = live_markets(pipeline, state) if pipeline is not None else None
    rows: list[dict[str, Any]] = []
    for leg in legs:
        market, selection = str(leg["market"]), str(leg["selection"])
        outcome, _ = settle_leg(market, selection, now)

        chance = None
        if live is not None:
            book = live["piete"].get(_market_key(market, selection))
            if book:
                chance = book.get(_selection_key(market, selection))

        rows.append({
            "bilet": leg["label"],
            "piata": market,
            "pontul": leg["description"],
            "la_scorul_curent": outcome,
            "sansa_acum": chance,
            "rezultat_final": leg["result"],
        })
    return rows


def _market_key(market: str, selection: str) -> str:
    if market == "total goluri":
        import re
        m = re.search(r"([\d.]+)", selection)
        return f"total goluri {m.group(1)}" if m else market
    return {"1X2": "1X2", "șansă dublă": "sansa dubla",
            "ambele marchează": "ambele marcheaza"}.get(market, market)


def _selection_key(market: str, selection: str) -> str:
    if market == "total goluri":
        return "peste" if selection.startswith("peste") else "sub"
    if market == "ambele marchează":
        return "da" if selection == "da" else "nu"
    return selection
