"""Simulare Monte Carlo, ca sa putem combina ponturi din ACELASI meci.

Problema: doua selectii din acelasi meci sunt corelate. "Gazda castiga" si
"peste 2.5 goluri" nu sunt evenimente independente - daca gazda domina, cresc
amandoua. Inmultind probabilitatile, un bilet cu ambele ar parea mai putin
probabil decat este in realitate, iar cota totala ar fi gresita.

Solutia: simulam meciul de multe ori (scor din matricea Dixon-Coles, cornere si
cartonase din distributiile lor) si numaram in cate simulari ies TOATE
ponturile. Corelatia iese de la sine, fara sa o modelam explicit.

Detaliul care tine totul coerent: fiecare simulare e transformata intr-un
`MatchFacts` - exact structura folosita la decontarea reala - si trecuta prin
`settle_leg`. Asa, un pont e evaluat la pret cu acelasi cod cu care va fi
decontat dupa meci; nu pot aparea diferente intre cele doua.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

import numpy as np

from ..tickets.settlement import WON, MatchFacts, settle_leg

log = logging.getLogger(__name__)

DEFAULT_DRAWS = 20_000

#: samanta de baza; fiecare meci primeste una derivata din `match_id`, ca
#: simularile a doua meciuri diferite sa NU fie identice
SEED_BASE = 12345

#: statisticile simulate, cu coloana din care se decontează
SIMULATED_STATS = {"corners": "corners", "yellow_cards": "yellow_cards"}


@dataclass
class MatchSimulation:
    """Rezultatele simulate ale unui meci."""

    match_id: int
    home_team: str
    away_team: str
    home_goals: np.ndarray
    away_goals: np.ndarray
    stats: dict[str, tuple[np.ndarray, np.ndarray]]
    scorers: list[set[str]] | None = None

    def __len__(self) -> int:
        return len(self.home_goals)

    def facts_at(self, index: int) -> MatchFacts:
        """O singura simulare, in forma folosita de decontare."""
        home_stats = {col: float(values[0][index])
                      for col, values in self.stats.items()}
        away_stats = {col: float(values[1][index])
                      for col, values in self.stats.items()}
        return MatchFacts(
            match_id=self.match_id,
            home_goals=int(self.home_goals[index]),
            away_goals=int(self.away_goals[index]),
            home_stats=home_stats,
            away_stats=away_stats,
            scorers=self.scorers[index] if self.scorers else set(),
            has_player_data=bool(self.scorers),
        )


def simulate_match(pipeline, home_team: str, away_team: str, *,
                   match_id: int = 0, draws: int = DEFAULT_DRAWS,
                   rng: np.random.Generator | None = None) -> MatchSimulation:
    """Trage `draws` desfasurari posibile ale unui meci.

    Samanta depinde de `match_id`: cu o samanta comuna, doua meciuri diferite
    ar primi exact aceleasi trageri, iar ponturi din meciuri DIFERITE ar iesi
    perfect corelate - eroare mult mai grava decat cea pe care o rezolvam aici.
    Rezultatele raman totusi reproductibile de la o rulare la alta.
    """
    rng = rng or np.random.default_rng(SEED_BASE + int(match_id))

    matrix = pipeline.goals.score_matrix(home_team, away_team)
    size = matrix.shape[0]
    flat = matrix.reshape(-1)
    flat = flat / flat.sum()
    picks = rng.choice(len(flat), size=draws, p=flat)
    home_goals = (picks // size).astype(int)
    away_goals = (picks % size).astype(int)

    stats: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    secondary = getattr(pipeline, "secondary", None)
    if secondary is not None:
        for stat, column in SIMULATED_STATS.items():
            model = secondary.models.get(stat)
            if model is None:
                continue
            home_mean, away_mean = model.expected_match(home_team, away_team)
            stats[column] = (_draw_counts(model, home_mean, draws, rng),
                             _draw_counts(model, away_mean, draws, rng))

    return MatchSimulation(match_id=match_id, home_team=home_team,
                           away_team=away_team, home_goals=home_goals,
                           away_goals=away_goals, stats=stats)


def _draw_counts(model, mean: float, draws: int,
                 rng: np.random.Generator) -> np.ndarray:
    """Esantioneaza dintr-o statistica de numarare, dupa distributia modelului."""
    pmf = model.distribution(mean)
    total = pmf.sum()
    if total <= 0:
        return np.zeros(draws, dtype=int)
    return rng.choice(len(pmf), size=draws, p=pmf / total).astype(int)


# ----------------------------------------------------------------------
def joint_probability(legs: Sequence[Any],
                      simulations: dict[int, MatchSimulation]) -> float:
    """Sansa ca TOATE ponturile sa iasa, tinand cont de corelatii.

    Ponturile din meciuri diferite raman independente (se inmultesc implicit,
    pentru ca simularile sunt trase separat), dar cele din acelasi meci sunt
    evaluate pe aceeasi desfasurare, deci corelatia intra corect in calcul.
    """
    if not legs:
        return 0.0

    draws = min(len(sim) for sim in simulations.values()) if simulations else 0
    if draws == 0:
        return 0.0

    surviving = np.ones(draws, dtype=bool)
    for leg in legs:
        simulation = simulations.get(leg.match_id)
        if simulation is None:
            # fara simulare cadem pe probabilitatea marginala
            surviving = surviving & (
                np.random.default_rng(0).random(draws) < leg.probability)
            continue
        outcomes = np.fromiter(
            (settle_leg(leg.market, leg.selection,
                        simulation.facts_at(i))[0] == WON
             for i in range(draws)),
            dtype=bool, count=draws,
        )
        surviving = surviving & outcomes
        if not surviving.any():
            return 0.0
    return float(surviving.mean())


def leg_probability(leg: Any, simulation: MatchSimulation) -> float:
    """Probabilitatea unui singur pont, dupa simulare - util pentru control."""
    draws = len(simulation)
    wins = sum(
        settle_leg(leg.market, leg.selection, simulation.facts_at(i))[0] == WON
        for i in range(draws)
    )
    return wins / draws if draws else 0.0


def correlation_gain(legs: Sequence[Any],
                     simulations: dict[int, MatchSimulation]) -> dict[str, float]:
    """Cat de mult minte inmultirea simpla fata de simulare."""
    independent = 1.0
    for leg in legs:
        independent *= leg.probability
    joint = joint_probability(legs, simulations)
    return {
        "independent": independent,
        "real": joint,
        "diferenta": joint - independent,
        "raport": (joint / independent) if independent > 0 else float("nan"),
    }
