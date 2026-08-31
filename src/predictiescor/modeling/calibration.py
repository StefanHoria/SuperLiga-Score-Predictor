"""Calibrarea probabilitatilor 1X2 - si de ce NU o aplicam.

REZULTAT MASURAT (nu presupunere). Am testat pe iesirea backtestului
walk-forward, antrenand pe primele 561 de meciuri si testand pe urmatoarele
374:

    metrica      necalibrat   izotonica      Platt
    RPS             0.2088      0.2121      0.2090
    log loss        1.0345      1.0933      1.0335
    Brier           0.6202      0.6278      0.6200

Regresia izotonica *inrautateste* toate metricile - e prea flexibila si invata
zgomotul jumatatii de antrenare. Scalarea Platt, cu doar doi parametri, nu
schimba practic nimic (castiguri de ordinul 0.0002, adica zgomot).

Motivul e simplu: **modelul e deja bine calibrat pe 1X2.** Abaterea medie
ponderata dintre prezis si observat e 0.027 - sub trei puncte procentuale.
Nu exista distorsiune sistematica de corectat, deci orice strat suplimentar
adauga varianta fara sa scada eroarea.

De aceea calibratorul NU e legat in `pipeline.predict`. Modulul ramane ca
unealta de diagnostic: daca modelul se schimba mult, `evaluate_gain` spune in
cateva secunde daca situatia s-a schimbat.

NB - lucru diferit: pietele secundare (cornere, marcatori) CHIAR sunt prost
calibrate, iar acelea se corecteaza deja, din biletele decontate
(`tickets/learning.py`). Aici e vorba strict de 1X2.

---

Calibrarea probabilitatilor 1X2.

Un model poate fi bun la ordonat (stie care echipa e mai buna) si totusi prost
la exprimat increderea: sa spuna 70% acolo unde realitatea e 60%. Calibrarea
corecteaza exact asta, fara sa schimbe ordinea.

Metoda: **regresie izotonica**, cate una pentru fiecare rezultat (gazda, egal,
oaspete). E monotona - o probabilitate mai mare ramane mai mare - deci nu poate
strica ordonarea, doar reaseaza nivelurile. Dupa corectie, cele trei valori se
renormalizeaza ca sa insumeze 1.

Regula respectata: calibratorul se antreneaza DOAR pe predictii facute in afara
esantionului (iesirea backtestului walk-forward). Antrenat pe predictii in
esantion, ar invata zgomotul si ar inrautati lucrurile.

Izotonica e flexibila, deci se poate supraadapta cand sunt putine date. De aceea
`fit` cere un minim de observatii, iar `evaluate_gain` masoara castigul pe o
perioada tinuta deoparte - ca sa se poata decide cu cifre, nu din impresie.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression

from .evaluate import (
    OUTCOMES, brier_score, log_loss, ranked_probability_score,
)

log = logging.getLogger(__name__)

#: sub atatea meciuri, calibrarea invata mai mult zgomot decat semnal
MIN_OBSERVATIONS = 250

PROB_COLUMNS = ["p_gazda", "p_egal", "p_oaspete"]


@dataclass
class ProbabilityCalibrator:
    """Corecteaza probabilitatile 1X2, pastrandu-le ordinea."""

    models: dict[str, IsotonicRegression] = field(default_factory=dict)
    n_observations: int = 0
    fitted: bool = False

    # ------------------------------------------------------------------
    def fit(self, predictions: pd.DataFrame) -> "ProbabilityCalibrator":
        """Antreneaza pe iesirea backtestului (predictii out-of-sample)."""
        data = predictions.dropna(subset=[*PROB_COLUMNS, "rezultat"])
        if len(data) < MIN_OBSERVATIONS:
            raise ValueError(
                f"prea putine predictii pentru calibrare ({len(data)}, "
                f"minim {MIN_OBSERVATIONS})")

        for outcome, column in zip(OUTCOMES, PROB_COLUMNS):
            observed = (data["rezultat"] == outcome).astype(float).to_numpy()
            model = IsotonicRegression(y_min=0.001, y_max=0.999,
                                       out_of_bounds="clip")
            model.fit(data[column].to_numpy(), observed)
            self.models[outcome] = model

        self.n_observations = len(data)
        self.fitted = True
        log.info("calibrator antrenat pe %d predictii", self.n_observations)
        return self

    # ------------------------------------------------------------------
    def apply(self, probabilities: dict[str, float]) -> dict[str, float]:
        """Corecteaza un set de probabilitati 1X2 si il renormalizeaza."""
        if not self.fitted:
            return dict(probabilities)
        adjusted = {}
        for outcome in OUTCOMES:
            value = float(probabilities.get(outcome, 0.0))
            model = self.models.get(outcome)
            adjusted[outcome] = (float(model.predict([value])[0])
                                 if model is not None else value)
        total = sum(adjusted.values())
        if total <= 0:
            return dict(probabilities)
        return {k: v / total for k, v in adjusted.items()}

    def apply_frame(self, frame: pd.DataFrame) -> np.ndarray:
        """Varianta vectorizata, pentru evaluare."""
        columns = []
        for outcome, column in zip(OUTCOMES, PROB_COLUMNS):
            model = self.models.get(outcome)
            values = frame[column].to_numpy()
            columns.append(model.predict(values) if model is not None else values)
        matrix = np.column_stack(columns)
        totals = matrix.sum(axis=1, keepdims=True)
        return np.divide(matrix, totals, out=matrix, where=totals > 0)

    def to_dict(self) -> dict[str, Any]:
        return {"n_observations": self.n_observations, "fitted": self.fitted}


# ----------------------------------------------------------------------
def evaluate_gain(predictions: pd.DataFrame,
                  split: float = 0.6) -> dict[str, Any]:
    """Masoara daca ajuta calibrarea, pe o perioada tinuta deoparte.

    Impartim cronologic: antrenam calibratorul pe prima parte, il testam pe a
    doua. Antrenarea si testarea pe aceleasi date ar arata mereu o imbunatatire,
    inclusiv cand modelul doar a memorat zgomotul.
    """
    data = (predictions.dropna(subset=[*PROB_COLUMNS, "rezultat"])
            .sort_values("date").reset_index(drop=True))
    cut = int(len(data) * split)
    train, test = data.iloc[:cut], data.iloc[cut:]

    if len(train) < MIN_OBSERVATIONS or len(test) < 60:
        return {"posibil": False,
                "motiv": f"prea putine date ({len(train)} + {len(test)})"}

    calibrator = ProbabilityCalibrator().fit(train)

    index = test["rezultat"].map({o: i for i, o in enumerate(OUTCOMES)}).to_numpy()
    raw = test[PROB_COLUMNS].to_numpy()
    calibrated = calibrator.apply_frame(test)

    def metrics(probs: np.ndarray) -> dict[str, float]:
        return {
            "RPS": ranked_probability_score(probs, index),
            "log loss": log_loss(probs, index),
            "Brier": brier_score(probs, index),
        }

    before, after = metrics(raw), metrics(calibrated)
    return {
        "posibil": True,
        "antrenat_pe": len(train),
        "testat_pe": len(test),
        "inainte": before,
        "dupa": after,
        "castig": {k: before[k] - after[k] for k in before},
        "ajuta": after["RPS"] < before["RPS"] and after["log loss"] < before["log loss"],
    }


def fit_from_backtest(predictions: pd.DataFrame) -> ProbabilityCalibrator | None:
    """Antreneaza pe tot backtestul, daca sunt destule date."""
    try:
        return ProbabilityCalibrator().fit(predictions)
    except ValueError as exc:
        log.info("calibrare sarita: %s", exc)
        return None
