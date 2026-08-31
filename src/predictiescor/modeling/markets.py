"""Din matricea de scor derivam toate pietele de pariere.

Toate functiile primesc `matrix[i, j] = P(gazda marcheaza i, oaspete marcheaza j)`
si intorc probabilitati. Nicio functie de aici nu stie nimic despre modelul care
a produs matricea - deci merge la fel cu Dixon-Coles sau cu orice alt model.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd


def probability_to_odds(prob: float) -> float:
    """Cota corecta (fara marja) pentru o probabilitate."""
    return float("inf") if prob <= 0 else round(1.0 / prob, 2)


def odds_to_probability(odds: float) -> float:
    return 0.0 if odds <= 1.0 else 1.0 / odds


def remove_margin(prices: dict[str, float]) -> dict[str, float]:
    """Elimina marja casei dintr-un set de cote (normalizare proportionala)."""
    implied = {k: odds_to_probability(v) for k, v in prices.items() if v and v > 1}
    total = sum(implied.values())
    if total <= 0:
        return {}
    return {k: v / total for k, v in implied.items()}


# ----------------------------------------------------------------------
# Piete de baza
# ----------------------------------------------------------------------
def result_1x2(matrix: np.ndarray) -> dict[str, float]:
    """Probabilitatile 1 / X / 2."""
    home = float(np.tril(matrix, -1).sum())   # i > j
    draw = float(np.trace(matrix))            # i == j
    away = float(np.triu(matrix, 1).sum())    # i < j
    return {"gazda": home, "egal": draw, "oaspete": away}


def double_chance(matrix: np.ndarray) -> dict[str, float]:
    r = result_1x2(matrix)
    return {
        "1X": r["gazda"] + r["egal"],
        "12": r["gazda"] + r["oaspete"],
        "X2": r["egal"] + r["oaspete"],
    }


def draw_no_bet(matrix: np.ndarray) -> dict[str, float]:
    r = result_1x2(matrix)
    denom = r["gazda"] + r["oaspete"]
    if denom <= 0:
        return {"gazda": 0.5, "oaspete": 0.5}
    return {"gazda": r["gazda"] / denom, "oaspete": r["oaspete"] / denom}


def total_goals_distribution(matrix: np.ndarray) -> np.ndarray:
    """P(total goluri = k) pentru k = 0..2*max_goals."""
    size = matrix.shape[0]
    totals = np.zeros(2 * size - 1)
    for i in range(size):
        for j in range(size):
            totals[i + j] += matrix[i, j]
    return totals


def over_under(matrix: np.ndarray, line: float) -> dict[str, float]:
    """Peste / sub o linie de goluri (liniile .5 nu au 'push')."""
    totals = total_goals_distribution(matrix)
    goals = np.arange(len(totals))
    over = float(totals[goals > line].sum())
    under = float(totals[goals < line].sum())
    return {"peste": over, "sub": under}


def both_teams_to_score(matrix: np.ndarray) -> dict[str, float]:
    yes = float(matrix[1:, 1:].sum())
    return {"da": yes, "nu": 1.0 - yes}


def clean_sheet(matrix: np.ndarray) -> dict[str, float]:
    return {
        "gazda": float(matrix[:, 0].sum()),   # oaspetele nu marcheaza
        "oaspete": float(matrix[0, :].sum()),  # gazda nu marcheaza
    }


def team_to_score(matrix: np.ndarray) -> dict[str, float]:
    return {
        "gazda": 1.0 - float(matrix[0, :].sum()),
        "oaspete": 1.0 - float(matrix[:, 0].sum()),
    }


def odd_even_goals(matrix: np.ndarray) -> dict[str, float]:
    totals = total_goals_distribution(matrix)
    goals = np.arange(len(totals))
    return {
        "impar": float(totals[goals % 2 == 1].sum()),
        "par": float(totals[goals % 2 == 0].sum()),
    }


def asian_handicap(matrix: np.ndarray, line: float) -> dict[str, float]:
    """Handicap asiatic pe gazda. Linia negativa = gazda ofera avantaj.

    Liniile intregi produc 'push' (miza returnata); il raportam separat.
    """
    size = matrix.shape[0]
    home_win = draw = away_win = 0.0
    for i in range(size):
        for j in range(size):
            margin = (i + line) - j
            p = float(matrix[i, j])
            if margin > 1e-9:
                home_win += p
            elif margin < -1e-9:
                away_win += p
            else:
                draw += p
    return {"gazda": home_win, "oaspete": away_win, "nul": draw}


def winning_margin(matrix: np.ndarray, max_margin: int = 4) -> dict[str, float]:
    """Distributia diferentei de goluri, din perspectiva gazdei."""
    size = matrix.shape[0]
    out: dict[str, float] = {}
    for i in range(size):
        for j in range(size):
            diff = i - j
            key = (f"gazda +{diff}" if 0 < diff <= max_margin else
                   f"gazda +{max_margin}+" if diff > max_margin else
                   f"oaspete +{-diff}" if 0 > diff >= -max_margin else
                   f"oaspete +{max_margin}+" if diff < -max_margin else
                   "egalitate")
            out[key] = out.get(key, 0.0) + float(matrix[i, j])
    return out


def correct_score(matrix: np.ndarray, top_n: int = 15) -> pd.DataFrame:
    """Cele mai probabile scoruri exacte."""
    size = matrix.shape[0]
    rows = [
        {"scor": f"{i}-{j}", "probabilitate": float(matrix[i, j]),
         "cota_corecta": probability_to_odds(float(matrix[i, j]))}
        for i in range(size) for j in range(size)
    ]
    df = pd.DataFrame(rows).sort_values("probabilitate", ascending=False)
    return df.head(top_n).reset_index(drop=True)


# ----------------------------------------------------------------------
# Raport complet
# ----------------------------------------------------------------------
@dataclass
class MatchPrediction:
    """Toate pietele pentru un meci, gata de afisat."""

    home_team: str
    away_team: str
    expected_home_goals: float
    expected_away_goals: float
    matrix: np.ndarray
    markets: dict[str, dict[str, float]]
    correct_scores: pd.DataFrame

    @property
    def most_likely_score(self) -> str:
        return str(self.correct_scores.iloc[0]["scor"])

    @property
    def expected_total_goals(self) -> float:
        return self.expected_home_goals + self.expected_away_goals

    def as_rows(self) -> list[tuple[str, str, float]]:
        """(piata, selectie, probabilitate) - pentru salvare in DB."""
        return [
            (market, selection, prob)
            for market, book in self.markets.items()
            for selection, prob in book.items()
        ]

    def to_frame(self) -> pd.DataFrame:
        rows = [
            {"piata": market, "selectie": selection,
             "probabilitate": prob, "cota_corecta": probability_to_odds(prob)}
            for market, selection, prob in self.as_rows()
        ]
        return pd.DataFrame(rows)


def build_prediction(
    model: Any,
    home_team: str,
    away_team: str,
    *,
    ou_lines: list[float] | None = None,
    handicaps: list[float] | None = None,
    top_n: int = 15,
) -> MatchPrediction:
    """Construieste toate pietele pentru un meci folosind un model cu score_matrix."""
    ou_lines = ou_lines or [0.5, 1.5, 2.5, 3.5, 4.5]
    handicaps = handicaps or [-2.0, -1.5, -1.0, -0.5, 0.0, 0.5, 1.0, 1.5, 2.0]

    matrix = model.score_matrix(home_team, away_team)
    lam, mu = model.expected_goals(home_team, away_team)

    markets: dict[str, dict[str, float]] = {
        "1X2": result_1x2(matrix),
        "sansa dubla": double_chance(matrix),
        "pariu fara egal": draw_no_bet(matrix),
        "ambele marcheaza": both_teams_to_score(matrix),
        "poarta goala": clean_sheet(matrix),
        "echipa marcheaza": team_to_score(matrix),
        "goluri par/impar": odd_even_goals(matrix),
        "diferenta de goluri": winning_margin(matrix),
    }
    for line in ou_lines:
        markets[f"total goluri {line}"] = over_under(matrix, line)
    for line in handicaps:
        markets[f"handicap asiatic {line:+g}"] = asian_handicap(matrix, line)

    return MatchPrediction(
        home_team=home_team,
        away_team=away_team,
        expected_home_goals=lam,
        expected_away_goals=mu,
        matrix=matrix,
        markets=markets,
        correct_scores=correct_score(matrix, top_n),
    )
