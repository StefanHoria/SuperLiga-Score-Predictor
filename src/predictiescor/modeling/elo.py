"""Rating Elo adaptat pentru fotbal.

Elo nu inlocuieste Dixon-Coles - il completeaza. E o masura simpla, usor de
citit si de urmarit in timp a fortei unei echipe, folosita in dashboard pentru
grafice de evolutie si ca reper independent fata de modelul de goluri.

Diferenta fata de Elo-ul din sah: actualizarea e amplificata de diferenta de
goluri, ca o victorie cu 4-0 sa conteze mai mult decat una cu 1-0.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd


@dataclass
class EloModel:
    k_factor: float = 20.0
    home_advantage: float = 65.0
    initial_rating: float = 1500.0
    goal_diff_scaling: bool = True

    ratings: dict[str, float] = field(default_factory=dict, init=False)
    history: list[dict] = field(default_factory=list, init=False)

    @classmethod
    def from_config(cls, cfg: dict) -> "EloModel":
        elo = cfg.get("elo", {})
        return cls(
            k_factor=float(elo.get("k_factor", 20)),
            home_advantage=float(elo.get("home_advantage", 65)),
            initial_rating=float(elo.get("initial_rating", 1500)),
            goal_diff_scaling=bool(elo.get("goal_diff_scaling", True)),
        )

    # ------------------------------------------------------------------
    def rating(self, team: str) -> float:
        return self.ratings.get(team, self.initial_rating)

    def expected_score(self, home_team: str, away_team: str) -> float:
        """Probabilitatea (Elo) ca gazda sa castige, egalul contand jumatate."""
        diff = (self.rating(home_team) + self.home_advantage
                - self.rating(away_team))
        return 1.0 / (1.0 + 10 ** (-diff / 400.0))

    def win_probabilities(self, home_team: str, away_team: str,
                          draw_factor: float = 0.28) -> dict[str, float]:
        """Descompune scorul asteptat in 1 / X / 2.

        `draw_factor` calibreaza cat de des se termina egal: probabilitatea de
        egal e maxima cand echipele sunt egale si scade cu diferenta de valoare.
        """
        expected = self.expected_score(home_team, away_team)
        draw = draw_factor * (1.0 - 2.0 * abs(expected - 0.5))
        draw = max(0.05, min(0.40, draw))
        home = expected - draw / 2.0
        away = 1.0 - home - draw
        total = home + draw + away
        return {"gazda": home / total, "egal": draw / total, "oaspete": away / total}

    # ------------------------------------------------------------------
    def _multiplier(self, goal_diff: int) -> float:
        if not self.goal_diff_scaling or goal_diff <= 1:
            return 1.0
        # formula folosita de FiveThirtyEight pentru fotbal
        return math.sqrt(goal_diff) if goal_diff <= 4 else 2.0 + (goal_diff - 4) * 0.1

    def update(self, home_team: str, away_team: str,
               home_goals: int, away_goals: int) -> tuple[float, float]:
        """Actualizeaza ratingurile dupa un meci. Intoarce noile valori."""
        expected = self.expected_score(home_team, away_team)
        if home_goals > away_goals:
            actual = 1.0
        elif home_goals < away_goals:
            actual = 0.0
        else:
            actual = 0.5

        change = (self.k_factor * self._multiplier(abs(home_goals - away_goals))
                  * (actual - expected))
        new_home = self.rating(home_team) + change
        new_away = self.rating(away_team) - change
        self.ratings[home_team] = new_home
        self.ratings[away_team] = new_away
        return new_home, new_away

    # ------------------------------------------------------------------
    def fit(self, matches: pd.DataFrame) -> "EloModel":
        """Parcurge meciurile in ordine cronologica si construieste istoricul."""
        data = matches.dropna(subset=["home_goals", "away_goals"]).copy()
        data = data.sort_values("date")
        self.ratings.clear()
        self.history.clear()

        for row in data.itertuples():
            before_home = self.rating(row.home_team)
            before_away = self.rating(row.away_team)
            after_home, after_away = self.update(
                row.home_team, row.away_team,
                int(row.home_goals), int(row.away_goals),
            )
            self.history.append({
                "date": row.date, "match_id": getattr(row, "match_id", None),
                "home_team": row.home_team, "away_team": row.away_team,
                "elo_home_before": before_home, "elo_away_before": before_away,
                "elo_home_after": after_home, "elo_away_after": after_away,
            })
        return self

    def ratings_frame(self) -> pd.DataFrame:
        df = pd.DataFrame(
            [{"echipa": t, "elo": r} for t, r in self.ratings.items()])
        if df.empty:
            return df
        return df.sort_values("elo", ascending=False).reset_index(drop=True)

    def history_frame(self) -> pd.DataFrame:
        """Evolutia ratingului fiecarei echipe, format lung (pentru grafice)."""
        if not self.history:
            return pd.DataFrame(columns=["date", "echipa", "elo"])
        rows = []
        for h in self.history:
            rows.append({"date": h["date"], "echipa": h["home_team"],
                         "elo": h["elo_home_after"]})
            rows.append({"date": h["date"], "echipa": h["away_team"],
                         "elo": h["elo_away_after"]})
        return pd.DataFrame(rows).sort_values("date")
