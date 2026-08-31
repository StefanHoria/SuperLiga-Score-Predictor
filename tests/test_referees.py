"""Efectul arbitrului: cod masurat, nelegat de predictii.

Testele verifica estimatorul si, mai ales, ca masuratoarea nu minte: pe date
fara niciun efect real trebuie sa spuna ca nu exista efect.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np
import pandas as pd
import pytest

from predictiescor.modeling.referees import (
    evaluate_referee_effect, referee_effect, variance_decomposition,
)


def _matches(pairs):
    """pairs: (arbitru, galbene)."""
    return pd.DataFrame([{"match_id": i, "referee": r, "galbene": float(g)}
                         for i, (r, g) in enumerate(pairs)])


# ----------------------------------------------------------------------
def test_shrinkage_pulls_a_rare_referee_towards_the_league():
    """Un arbitru cu un singur meci nu poate muta estimarea prea mult."""
    data = _matches([("A", 4)] * 30 + [("Rar", 12)])
    effect = referee_effect(data, shrinkage=10.0)
    league = data["galbene"].mean()
    assert abs(effect["Rar"] - league) < abs(12 - league) / 2


def test_no_shrinkage_trusts_a_single_match_completely():
    effect = referee_effect(_matches([("A", 4)] * 30 + [("Rar", 12)]),
                            shrinkage=0.0)
    assert effect["Rar"] == pytest.approx(12.0)


def test_a_frequent_referee_keeps_his_own_average():
    data = _matches([("Strict", 8)] * 60 + [("Blând", 2)] * 60)
    effect = referee_effect(data, shrinkage=10.0)
    assert effect["Strict"] > 7.0
    assert effect["Blând"] < 3.0


def test_no_matches_gives_no_effects():
    assert referee_effect(pd.DataFrame()) == {}


# ----------------------------------------------------------------------
def test_variance_finds_signal_when_referees_really_differ():
    data = _matches([("Strict", 8)] * 40 + [("Blând", 1)] * 40)
    parts = variance_decomposition(data)
    assert parts["semnal estimat"] > 1.0


def test_variance_finds_nothing_when_referees_are_interchangeable():
    """Aceeasi distributie pentru toti: semnalul trebuie sa iasa zero."""
    rng = np.random.default_rng(1)
    data = _matches([(f"R{i % 20}", rng.poisson(4)) for i in range(200)])
    assert variance_decomposition(data)["semnal estimat"] == pytest.approx(0.0,
                                                                          abs=0.4)


def test_variance_needs_at_least_two_referees():
    assert variance_decomposition(_matches([("A", 4)] * 10)) == {}


# ----------------------------------------------------------------------
def test_evaluation_detects_a_real_effect():
    data = _matches([("Strict", 9)] * 50 + [("Blând", 1)] * 50)
    verdict = evaluate_referee_effect(data, samples=1000)
    assert verdict["câștig MAE"] > 0
    assert verdict["distinct de zero"] is True


def test_evaluation_stays_silent_on_pure_noise():
    """Testul care conteaza: fara efect real, nu inventam unul."""
    rng = np.random.default_rng(2)
    data = _matches([(f"R{i % 25}", rng.poisson(4.4)) for i in range(155)])
    verdict = evaluate_referee_effect(data, samples=2000)
    assert verdict["distinct de zero"] is False


def test_evaluation_refuses_a_tiny_sample():
    assert evaluate_referee_effect(_matches([("A", 4)] * 10)) == {"meciuri": 10}


def test_evaluation_reports_how_thin_the_data_is():
    data = _matches([(f"R{i % 10}", 4) for i in range(40)])
    verdict = evaluate_referee_effect(data, samples=500)
    assert verdict["arbitri"] == 10
    assert verdict["meciuri per arbitru (mediană)"] == 4.0
