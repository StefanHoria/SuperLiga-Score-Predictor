"""Calibratorul de probabilitati - unealta de diagnostic, nu parte din predictie."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np
import pandas as pd
import pytest

from predictiescor.modeling.calibration import (
    MIN_OBSERVATIONS, ProbabilityCalibrator, evaluate_gain,
)


def _predictions(n=600, bias=0.0, seed=3):
    """Predictii sintetice; `bias` umfla artificial sansa gazdei."""
    rng = np.random.default_rng(seed)
    home = np.clip(rng.beta(3, 4, n) + bias, 0.02, 0.95)
    draw = np.clip(rng.beta(3, 8, n), 0.02, 0.6)
    away = np.clip(1.0 - home - draw, 0.02, None)
    total = home + draw + away
    home, draw, away = home / total, draw / total, away / total

    # Realitatea urmeaza probabilitatile ADEVARATE (fara bias). Vectorul se
    # renormalizeaza explicit: `rng.choice` refuza o suma care difera de 1
    # chiar si prin eroare de virgula mobila.
    true_home = np.clip(home - bias, 0.02, 0.95)
    rest = 1.0 - true_home
    picks = []
    for th, d, a, r in zip(true_home, draw, away, rest):
        weights = np.array([th, r * d / (d + a), r * a / (d + a)])
        weights = np.clip(weights, 1e-9, None)
        picks.append(rng.choice(["gazda", "egal", "oaspete"],
                                p=weights / weights.sum()))
    return pd.DataFrame({
        "date": pd.date_range("2024-01-01", periods=n, freq="D"),
        "p_gazda": home, "p_egal": draw, "p_oaspete": away, "rezultat": picks,
    })


# ----------------------------------------------------------------------
def test_refuses_to_fit_on_thin_data():
    """Izotonica e flexibila: cu putine date invata zgomot."""
    with pytest.raises(ValueError, match="prea putine"):
        ProbabilityCalibrator().fit(_predictions(n=MIN_OBSERVATIONS - 50))


def test_output_is_a_valid_probability_distribution():
    calibrator = ProbabilityCalibrator().fit(_predictions())
    out = calibrator.apply({"gazda": 0.5, "egal": 0.3, "oaspete": 0.2})
    assert pytest.approx(sum(out.values()), abs=1e-9) == 1.0
    assert all(0.0 <= v <= 1.0 for v in out.values())


def test_preserves_ordering():
    """Izotonica e monotona: nu poate rasturna clasamentul selectiilor."""
    calibrator = ProbabilityCalibrator().fit(_predictions())
    out = calibrator.apply({"gazda": 0.6, "egal": 0.25, "oaspete": 0.15})
    assert out["gazda"] > out["egal"] > out["oaspete"]


def test_unfitted_calibrator_is_a_no_op():
    original = {"gazda": 0.5, "egal": 0.3, "oaspete": 0.2}
    assert ProbabilityCalibrator().apply(original) == original


def test_detects_a_real_bias():
    """Cu o distorsiune injectata, calibrarea trebuie sa se vada ca ajuta."""
    result = evaluate_gain(_predictions(n=900, bias=0.12), split=0.6)
    assert result["posibil"]
    assert result["ajuta"], "o distorsiune sistematica trebuie corectata"


def test_reports_no_gain_on_an_already_calibrated_model():
    """Cazul real: modelul e deja calibrat, deci nu e nimic de castigat."""
    result = evaluate_gain(_predictions(n=900, bias=0.0), split=0.6)
    assert result["posibil"]
    assert result["castig"]["RPS"] < 0.01, "castigul ar fi doar zgomot"


def test_evaluation_needs_enough_data():
    result = evaluate_gain(_predictions(n=120), split=0.6)
    assert result["posibil"] is False
    assert "prea putine" in result["motiv"]
