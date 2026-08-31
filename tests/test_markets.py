"""Derivarea pietelor din matricea de scor - aritmetica trebuie sa fie exacta."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np
import pytest

from predictiescor.modeling.markets import (
    asian_handicap, both_teams_to_score, clean_sheet, correct_score,
    double_chance, draw_no_bet, odd_even_goals, over_under, remove_margin,
    result_1x2, total_goals_distribution,
)


@pytest.fixture
def matrix():
    """Matrice 3x3 simpla, cu probabilitati usor de verificat manual."""
    m = np.array([
        [0.10, 0.10, 0.05],   # 0-0, 0-1, 0-2
        [0.15, 0.20, 0.05],   # 1-0, 1-1, 1-2
        [0.20, 0.10, 0.05],   # 2-0, 2-1, 2-2
    ])
    return m / m.sum()


def test_1x2_partitions_probability(matrix):
    result = result_1x2(matrix)
    assert pytest.approx(sum(result.values()), abs=1e-9) == 1.0
    # gazda castiga la 1-0, 2-0, 2-1 = 0.15 + 0.20 + 0.10
    assert result["gazda"] == pytest.approx(0.45, abs=1e-9)
    # egal la 0-0, 1-1, 2-2 = 0.10 + 0.20 + 0.05
    assert result["egal"] == pytest.approx(0.35, abs=1e-9)
    assert result["oaspete"] == pytest.approx(0.20, abs=1e-9)


def test_double_chance_matches_1x2(matrix):
    single, double = result_1x2(matrix), double_chance(matrix)
    assert double["1X"] == pytest.approx(single["gazda"] + single["egal"])
    assert double["X2"] == pytest.approx(single["egal"] + single["oaspete"])
    assert double["12"] == pytest.approx(single["gazda"] + single["oaspete"])


def test_draw_no_bet_excludes_draw(matrix):
    dnb = draw_no_bet(matrix)
    assert pytest.approx(sum(dnb.values()), abs=1e-9) == 1.0


def test_over_under_has_no_push_on_half_lines(matrix):
    for line in (0.5, 1.5, 2.5, 3.5):
        ou = over_under(matrix, line)
        assert pytest.approx(ou["peste"] + ou["sub"], abs=1e-9) == 1.0


def test_over_under_is_monotone(matrix):
    probs = [over_under(matrix, line)["peste"] for line in (0.5, 1.5, 2.5, 3.5)]
    assert probs == sorted(probs, reverse=True)


def test_btts(matrix):
    btts = both_teams_to_score(matrix)
    # ambele marcheaza = celulele cu i>=1 si j>=1: 0.20 + 0.05 + 0.10 + 0.05
    assert btts["da"] == pytest.approx(0.40, abs=1e-9)
    assert pytest.approx(btts["da"] + btts["nu"], abs=1e-9) == 1.0


def test_clean_sheet(matrix):
    cs = clean_sheet(matrix)
    # gazda pastreaza poarta goala daca oaspetele nu marcheaza: coloana 0
    assert cs["gazda"] == pytest.approx(0.45, abs=1e-9)
    assert cs["oaspete"] == pytest.approx(0.25, abs=1e-9)


def test_total_goals_distribution_sums_to_one(matrix):
    totals = total_goals_distribution(matrix)
    assert pytest.approx(totals.sum(), abs=1e-9) == 1.0


def test_odd_even(matrix):
    oe = odd_even_goals(matrix)
    assert pytest.approx(oe["par"] + oe["impar"], abs=1e-9) == 1.0


def test_asian_handicap_whole_line_has_push(matrix):
    ah = asian_handicap(matrix, 0.0)
    assert ah["nul"] > 0            # linia intreaga produce push
    assert pytest.approx(sum(ah.values()), abs=1e-9) == 1.0

    half = asian_handicap(matrix, -0.5)
    assert half["nul"] == pytest.approx(0.0, abs=1e-12)


def test_asian_handicap_zero_equals_draw_no_bet(matrix):
    ah, dnb = asian_handicap(matrix, 0.0), draw_no_bet(matrix)
    # dupa eliminarea push-ului, handicapul 0 e identic cu pariul fara egal
    live = ah["gazda"] + ah["oaspete"]
    assert ah["gazda"] / live == pytest.approx(dnb["gazda"], abs=1e-9)


def test_correct_score_is_sorted_and_complete(matrix):
    scores = correct_score(matrix, top_n=9)
    assert len(scores) == 9
    assert list(scores["probabilitate"]) == sorted(
        scores["probabilitate"], reverse=True)
    assert scores.iloc[0]["scor"] == "1-1"


def test_remove_margin_normalises():
    prices = {"gazda": 2.0, "egal": 3.5, "oaspete": 4.0}
    fair = remove_margin(prices)
    assert pytest.approx(sum(fair.values()), abs=1e-9) == 1.0
    # ordinea se pastreaza: cota mica ramane cea mai probabila
    assert fair["gazda"] > fair["egal"] > fair["oaspete"]
