"""Goluri asteptate (xG) reconstruite din zonele de sut.

De ce exista modulul: API-Football publica xG, dar planul gratuit acopera doar
sezoanele 2022-2024. LPF publica pentru sezonul curent zonele de sut (in careu,
in afara careului, pe poarta, blocate) - dar nu si xG. Diferenta se poate
acoperi: invatam din sezoanele unde avem ambele cat valoreaza un sut din
fiecare zona, apoi aplicam formula acolo unde xG lipseste.

Modelul e deliberat simplu si constrans, nu o regresie libera:

* **fara termen liber** - zero suturi inseamna zero xG;
* **coeficienti obligatoriu pozitivi** - un sut nu poate scadea xG-ul.

Fara constrangeri, regresia da suturilor din afara careului un coeficient
NEGATIV (efect de coliniaritate cu totalul suturilor), ceea ce e absurd fizic
si nu se generalizeaza. Cu ele, coeficientii se citesc direct ca "valoarea
medie a unui sut din acea zona" - adica exact ce e xG.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
from scipy.optimize import nnls

log = logging.getLogger(__name__)

#: zonele folosite, in ordinea coeficientilor
FEATURES = ["shots_inside_box", "shots_outside_box", "shots_on_target",
            "shots_blocked"]

#: minim de observatii ca sa avem incredere in estimare
MIN_OBSERVATIONS = 40


@dataclass
class ExpectedGoalsModel:
    """Cat valoreaza, in goluri, un sut din fiecare zona."""

    weights: dict[str, float] = field(default_factory=dict)
    n_observations: int = 0
    r_squared: float = float("nan")
    fitted: bool = False

    # ------------------------------------------------------------------
    def fit(self, team_stats: pd.DataFrame) -> "ExpectedGoalsModel":
        """Antreneaza pe randurile care au SI xG real, SI zonele de sut."""
        available = [f for f in FEATURES if f in team_stats.columns]
        if "xg" not in team_stats.columns or not available:
            raise ValueError("lipsesc coloanele necesare pentru xG")

        data = team_stats.dropna(subset=["xg", *available])
        if len(data) < MIN_OBSERVATIONS:
            raise ValueError(
                f"prea putine meciuri cu xG real ({len(data)}, "
                f"minim {MIN_OBSERVATIONS})")

        matrix = data[available].to_numpy(dtype=float)
        target = data["xg"].to_numpy(dtype=float)

        # NNLS = cei mai mici patrati cu solutia constransa la valori pozitive
        coefficients, _ = nnls(matrix, target)

        predicted = matrix @ coefficients
        residual = float(np.sum((target - predicted) ** 2))
        total = float(np.sum((target - target.mean()) ** 2))
        self.r_squared = 1.0 - residual / total if total > 0 else float("nan")

        self.weights = {name: float(value)
                        for name, value in zip(available, coefficients)}
        self.n_observations = len(data)
        self.fitted = True

        log.info("model xG antrenat pe %d observatii, R2=%.3f | %s",
                 self.n_observations, self.r_squared,
                 {k: round(v, 4) for k, v in self.weights.items()})
        return self

    # ------------------------------------------------------------------
    def predict(self, row: dict[str, Any] | pd.Series) -> float | None:
        """xG estimat pentru o echipa intr-un meci. None daca lipsesc datele."""
        if not self.fitted:
            return None
        total = 0.0
        used = 0
        for name, weight in self.weights.items():
            value = row.get(name)
            if value is None or (isinstance(value, float) and np.isnan(value)):
                continue
            total += weight * float(value)
            used += 1
        # avem nevoie macar de zona principala, altfel estimarea e o fictiune
        if used == 0 or row.get("shots_inside_box") in (None, np.nan):
            return None
        return round(max(0.0, total), 3)

    def predict_frame(self, frame: pd.DataFrame) -> pd.Series:
        """xG estimat pentru un tabel intreg."""
        if not self.fitted:
            return pd.Series([None] * len(frame), index=frame.index,
                             dtype="float64")
        result = pd.Series(0.0, index=frame.index, dtype="float64")
        for name, weight in self.weights.items():
            if name in frame.columns:
                result = result + weight * frame[name].fillna(0.0)
        # fara zona principala nu raportam nimic
        if "shots_inside_box" in frame.columns:
            result[frame["shots_inside_box"].isna()] = np.nan
        return result.clip(lower=0.0).round(3)

    # ------------------------------------------------------------------
    def describe(self) -> pd.DataFrame:
        """Valoarea unui sut din fiecare zona, pentru afisare."""
        labels = {
            "shots_inside_box": "Șut din careu",
            "shots_outside_box": "Șut din afara careului",
            "shots_on_target": "Șut pe poartă (bonus)",
            "shots_blocked": "Șut blocat",
        }
        rows = [
            {"zonă": labels.get(name, name), "goluri per șut": round(value, 4)}
            for name, value in self.weights.items()
        ]
        return pd.DataFrame(rows).sort_values("goluri per șut", ascending=False)

    def to_dict(self) -> dict[str, Any]:
        return {"weights": self.weights, "n_observations": self.n_observations,
                "r_squared": self.r_squared}


# ----------------------------------------------------------------------
def backfill(db, model: ExpectedGoalsModel | None = None) -> dict[str, int]:
    """Completeaza `xg_estimated` acolo unde xG real lipseste.

    xG-ul real NU se suprascrie niciodata: raman doua coloane separate, ca sa
    se stie mereu care valoare vine de la furnizor si care e estimata de noi.
    """
    stats = db.team_stats_df()
    if stats.empty:
        return {"antrenat_pe": 0, "completate": 0}

    if model is None:
        model = ExpectedGoalsModel().fit(stats)

    missing = stats[stats["xg"].isna()].copy()
    if missing.empty:
        return {"antrenat_pe": model.n_observations, "completate": 0}

    missing["xg_estimated"] = model.predict_frame(missing)
    rows = missing.dropna(subset=["xg_estimated"])

    with db.connect() as conn:
        conn.executemany(
            "UPDATE match_team_stats SET xg_estimated = ?"
            " WHERE match_id = ? AND team_id = ?",
            [(float(r.xg_estimated), int(r.match_id), int(r.team_id))
             for r in rows.itertuples()],
        )
    log.info("xG estimat completat pentru %d randuri", len(rows))
    return {"antrenat_pe": model.n_observations, "completate": len(rows)}
