"""xG reconstruit din zonele de sut si simularea pentru piete corelate."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np
import pandas as pd
import pytest

from predictiescor.modeling.xg import ExpectedGoalsModel


def _training_frame(n=120, seed=7):
    """Date sintetice cu o relatie cunoscuta, ca sa verificam recuperarea ei."""
    rng = np.random.default_rng(seed)
    inside = rng.integers(0, 12, n)
    outside = rng.integers(0, 10, n)
    on_target = rng.integers(0, 8, n)
    blocked = rng.integers(0, 6, n)
    # adevarul: 0.12 pe sut din careu, 0.02 in afara, restul zero
    xg = 0.12 * inside + 0.02 * outside + rng.normal(0, 0.05, n)
    return pd.DataFrame({
        "shots_inside_box": inside, "shots_outside_box": outside,
        "shots_on_target": on_target, "shots_blocked": blocked,
        "xg": np.clip(xg, 0, None),
    })


# ----------------------------------------------------------------------
def test_recovers_known_shot_values():
    model = ExpectedGoalsModel().fit(_training_frame())
    assert model.fitted
    assert model.weights["shots_inside_box"] == pytest.approx(0.12, abs=0.03)
    assert model.weights["shots_outside_box"] == pytest.approx(0.02, abs=0.03)
    assert model.r_squared > 0.9


def test_all_weights_are_non_negative():
    """Un sut nu poate SCADEA xG-ul; fara constrangere regresia da coeficienti
    negativi din cauza coliniaritatii."""
    model = ExpectedGoalsModel().fit(_training_frame())
    assert all(value >= 0 for value in model.weights.values())


def test_zero_shots_means_zero_xg():
    """Nu exista termen liber: fara suturi, xG-ul e zero."""
    model = ExpectedGoalsModel().fit(_training_frame())
    empty = {"shots_inside_box": 0, "shots_outside_box": 0,
             "shots_on_target": 0, "shots_blocked": 0}
    assert model.predict(empty) == 0.0


def test_prediction_grows_with_shots():
    model = ExpectedGoalsModel().fit(_training_frame())
    few = model.predict({"shots_inside_box": 2, "shots_outside_box": 1,
                         "shots_on_target": 1, "shots_blocked": 0})
    many = model.predict({"shots_inside_box": 10, "shots_outside_box": 4,
                          "shots_on_target": 5, "shots_blocked": 2})
    assert many > few


def test_missing_main_zone_returns_none():
    """Fara suturile din careu, estimarea ar fi o fictiune."""
    model = ExpectedGoalsModel().fit(_training_frame())
    assert model.predict({"shots_on_target": 5}) is None


def test_refuses_to_fit_on_too_few_rows():
    with pytest.raises(ValueError, match="prea putine"):
        ExpectedGoalsModel().fit(_training_frame(n=10))


def test_unfitted_model_predicts_nothing():
    assert ExpectedGoalsModel().predict({"shots_inside_box": 5}) is None


# ----------------------------------------------------------------------
# Simularea Monte Carlo pentru piete corelate
# ----------------------------------------------------------------------
class _FakeGoals:
    """Model minimal: matrice de scor fixa, cu maximum 3 goluri pe echipa."""
    max_goals = 3
    teams = ["A", "B"]
    attack = {"A": 0.0, "B": 0.0}

    def score_matrix(self, home, away):
        m = np.array([
            [0.10, 0.08, 0.02, 0.00],   # 0-0 .. 0-3
            [0.14, 0.12, 0.03, 0.00],
            [0.16, 0.10, 0.03, 0.00],
            [0.12, 0.06, 0.02, 0.00],
        ])
        return m / m.sum()


class _FakePipeline:
    goals = _FakeGoals()
    secondary = None


@pytest.fixture
def simulation():
    from predictiescor.modeling.simulation import simulate_match
    return simulate_match(_FakePipeline(), "A", "B", match_id=1, draws=40000)


def _leg(market, selection, probability=0.5, match_id=1):
    from predictiescor.tickets.generator import Leg
    return Leg(match_id, "A", "B", "", market, selection, "x", probability)


def test_simulation_reproduces_marginal_probabilities(simulation):
    from predictiescor.modeling.markets import result_1x2
    from predictiescor.modeling.simulation import leg_probability

    expected = result_1x2(_FakeGoals().score_matrix("A", "B"))
    got = leg_probability(_leg("1X2", "gazda"), simulation)
    assert got == pytest.approx(expected["gazda"], abs=0.01)


def test_same_match_legs_are_not_independent(simulation):
    """Miezul problemei: inmultirea probabilitatilor minte pe acelasi meci."""
    from predictiescor.modeling.simulation import (
        joint_probability, leg_probability,
    )

    home = _leg("1X2", "gazda")
    over = _leg("total goluri", "peste 1.5")
    home.probability = leg_probability(home, simulation)
    over.probability = leg_probability(over, simulation)

    naive = home.probability * over.probability
    joint = joint_probability([home, over], {1: simulation})
    assert abs(joint - naive) > 0.01, "corelatia trebuie sa se vada"


def test_contradictory_legs_are_impossible(simulation):
    from predictiescor.modeling.simulation import joint_probability

    over = _leg("total goluri", "peste 2.5")
    under = _leg("total goluri", "sub 2.5")
    assert joint_probability([over, under], {1: simulation}) == 0.0


def test_implied_leg_pair_keeps_the_smaller_probability(simulation):
    """'gazda castiga' implica 'gazda sau egal': comun = cel mai restrictiv."""
    from predictiescor.modeling.simulation import (
        joint_probability, leg_probability,
    )

    win = _leg("1X2", "gazda")
    double = _leg("șansă dublă", "1X")
    joint = joint_probability([win, double], {1: simulation})
    assert joint == pytest.approx(leg_probability(win, simulation), abs=0.01)


def test_legs_from_different_matches_stay_independent():
    from predictiescor.modeling.simulation import (
        joint_probability, leg_probability, simulate_match,
    )

    first = simulate_match(_FakePipeline(), "A", "B", match_id=1, draws=40000)
    second = simulate_match(_FakePipeline(), "C", "D", match_id=2, draws=40000)

    one = _leg("1X2", "gazda", match_id=1)
    two = _leg("1X2", "gazda", match_id=2)
    one.probability = leg_probability(one, first)
    two.probability = leg_probability(two, second)

    joint = joint_probability([one, two], {1: first, 2: second})
    assert joint == pytest.approx(one.probability * two.probability, abs=0.02)
