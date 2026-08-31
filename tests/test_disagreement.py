"""Dezacordul cu piata: masuram cine are dreptate, si cu ce marja de eroare."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np
import pandas as pd
import pytest

from predictiescor.modeling.disagreement import (
    bootstrap_roi, disagreement_table, edge_sweep, favourite_duel,
    total_variation, verdict,
)


def _frame(rows):
    """rows: (p_model 3-tuple, p_piata 3-tuple, rezultat)."""
    return pd.DataFrame([
        {"match_id": i, "date": pd.Timestamp("2026-01-01") + pd.Timedelta(days=i),
         "home_team": f"A{i}", "away_team": f"B{i}",
         "p_gazda": m[0], "p_egal": m[1], "p_oaspete": m[2],
         "piata_gazda": k[0], "piata_egal": k[1], "piata_oaspete": k[2],
         "cota_gazda": 1 / max(k[0], 1e-6), "cota_egal": 1 / max(k[1], 1e-6),
         "cota_oaspete": 1 / max(k[2], 1e-6),
         "rezultat": r}
        for i, (m, k, r) in enumerate(rows)
    ])


# ----------------------------------------------------------------------
# Distanta fata de piata
# ----------------------------------------------------------------------
def test_perfect_agreement_is_zero_distance():
    same = (0.5, 0.3, 0.2)
    assert total_variation(_frame([(same, same, "gazda")]))[0] == pytest.approx(0.0)


def test_distance_counts_moved_probability_once():
    """Zece puncte mutate din 'egal' in 'gazda' = distanta 0.10, nu 0.20."""
    frame = _frame([((0.6, 0.2, 0.2), (0.5, 0.3, 0.2), "gazda")])
    assert total_variation(frame)[0] == pytest.approx(0.10)


def test_opposite_opinions_reach_one():
    frame = _frame([((1.0, 0.0, 0.0), (0.0, 0.0, 1.0), "gazda")])
    assert total_variation(frame)[0] == pytest.approx(1.0)


# ----------------------------------------------------------------------
# Tabelul pe felii
# ----------------------------------------------------------------------
def test_matches_land_in_the_right_slice():
    rows = [((0.5, 0.3, 0.2), (0.5, 0.3, 0.2), "gazda")] * 4          # 0%
    rows += [((0.7, 0.2, 0.1), (0.5, 0.3, 0.2), "gazda")] * 4          # 20%
    table = disagreement_table(_frame(rows))
    assert list(table["meciuri"]) == [4, 4]
    assert table.iloc[0]["dezacord"] == "0%-3%"
    assert table.iloc[-1]["dezacord"] == "peste 10%"


def test_advantage_is_positive_when_the_model_is_the_better_one():
    """Modelul pune 90% pe rezultatul care chiar se intampla; piata pune 40%."""
    rows = [((0.9, 0.05, 0.05), (0.4, 0.3, 0.3), "gazda")] * 6
    table = disagreement_table(_frame(rows))
    assert (table["avantaj model"] > 0).all()


def test_advantage_is_negative_when_the_model_is_confidently_wrong():
    rows = [((0.9, 0.05, 0.05), (0.4, 0.3, 0.3), "oaspete")] * 6
    table = disagreement_table(_frame(rows))
    assert (table["avantaj model"] < 0).all()


def test_a_slice_with_a_single_match_is_dropped():
    """Un singur meci pe felie nu spune nimic, deci nu apare in tabel."""
    rows = [((0.5, 0.3, 0.2), (0.5, 0.3, 0.2), "gazda")] * 5
    rows += [((0.9, 0.05, 0.05), (0.4, 0.3, 0.3), "gazda")]
    table = disagreement_table(_frame(rows))
    assert list(table["meciuri"]) == [5]


def test_no_market_columns_gives_an_empty_table():
    frame = _frame([((0.5, 0.3, 0.2), (0.5, 0.3, 0.2), "gazda")])
    assert disagreement_table(frame.drop(columns=["piata_gazda"])).empty


# ----------------------------------------------------------------------
# Duelul favoritilor
# ----------------------------------------------------------------------
def test_agreement_on_the_favourite_is_not_a_duel():
    rows = [((0.6, 0.2, 0.2), (0.5, 0.3, 0.2), "gazda")] * 5
    assert favourite_duel(_frame(rows))["meciuri"] == 0


def test_duel_counts_who_was_right():
    # modelul zice gazda, piata zice oaspete; castiga oaspetele de 2 ori din 3
    rows = [((0.6, 0.2, 0.2), (0.2, 0.2, 0.6), r)
            for r in ("oaspete", "oaspete", "gazda")]
    duel = favourite_duel(_frame(rows))
    assert duel["meciuri"] == 3
    assert duel["model a avut dreptate"] == 1
    assert duel["piața a avut dreptate"] == 2
    assert duel["niciunul"] == 0


def test_a_draw_means_neither_side_was_right():
    rows = [((0.6, 0.2, 0.2), (0.2, 0.2, 0.6), "egal")] * 3
    duel = favourite_duel(_frame(rows))
    assert duel["niciunul"] == 3
    assert duel["rata model %"] == 0.0


# ----------------------------------------------------------------------
# Bootstrap: intervalul de incredere
# ----------------------------------------------------------------------
def _bets(profits, match_of):
    return pd.DataFrame({"profit": profits, "castigat": [p > 0 for p in profits],
                         "meci": match_of})


def test_interval_brackets_the_measured_roi():
    rng = np.random.default_rng(7)
    profits = rng.normal(0.1, 1.0, 300)
    stats = bootstrap_roi(_bets(profits, [f"m{i}" for i in range(300)]))
    assert stats["interval jos %"] < stats["ROI %"] < stats["interval sus %"]


def test_a_certain_outcome_has_a_zero_width_interval():
    """Daca toate pariurile dau acelasi profit, nu exista incertitudine."""
    stats = bootstrap_roi(_bets([1.0] * 50, [f"m{i}" for i in range(50)]))
    assert stats["ROI %"] == pytest.approx(100.0)
    assert stats["interval jos %"] == pytest.approx(100.0)
    assert stats["interval sus %"] == pytest.approx(100.0)


def test_correlated_legs_widen_the_interval():
    """Cheia metodei: ponturile de pe acelasi meci pica sau ies impreuna.

    Reesantionarea pe pariuri le-ar trata ca independente si ar da un interval
    fals de ingust - adica ne-ar convinge de un avantaj inexistent.
    """
    rng = np.random.default_rng(11)
    outcomes = rng.normal(0, 1.0, 60)
    profits = np.repeat(outcomes, 3)                    # 3 ponturi pe meci
    grouped = [f"m{i}" for i in range(60) for _ in range(3)]
    independent = [f"m{i}" for i in range(180)]

    wide = bootstrap_roi(_bets(profits, grouped), samples=3000)
    narrow = bootstrap_roi(_bets(profits, independent), samples=3000)

    width = lambda s: s["interval sus %"] - s["interval jos %"]
    assert width(wide) > width(narrow) * 1.4


def test_significance_flag_follows_the_interval():
    clear = bootstrap_roi(_bets([1.0] * 40, [f"m{i}" for i in range(40)]))
    assert clear["distinct de zero"] is True

    rng = np.random.default_rng(3)
    noisy = bootstrap_roi(_bets(rng.normal(0, 2.0, 40),
                                [f"m{i}" for i in range(40)]))
    assert noisy["distinct de zero"] is False


def test_bootstrap_is_reproducible():
    bets = _bets([1.0, -1.0] * 30, [f"m{i}" for i in range(60)])
    assert bootstrap_roi(bets) == bootstrap_roi(bets)


def test_no_bets_is_reported_not_crashed():
    assert bootstrap_roi(pd.DataFrame()) == {"pariuri": 0}


# ----------------------------------------------------------------------
# Sweep si verdict
# ----------------------------------------------------------------------
def test_sweep_shrinks_the_bet_count_as_the_threshold_rises():
    rows = [((0.5 + 0.02 * i, 0.3 - 0.01 * i, 0.2 - 0.01 * i),
             (0.40, 0.30, 0.30), "gazda") for i in range(20)]
    sweep = edge_sweep(_frame(rows), edges=(0.02, 0.10, 0.20), samples=200)
    counts = list(sweep["pariuri"])
    assert counts == sorted(counts, reverse=True)


def test_verdict_says_market_when_the_market_wins_the_duel():
    duel = {"meciuri": 100, "rata model %": 20.0, "rata piață %": 45.0}
    text = verdict(duel, pd.DataFrame())
    assert "erorile modelului" in text


def test_verdict_stays_cautious_without_significance():
    duel = {"meciuri": 100, "rata model %": 45.0, "rata piață %": 20.0}
    sweep = pd.DataFrame([{"distinct de zero": False}])
    assert "poate fi noroc" in verdict(duel, sweep)


def test_verdict_refuses_to_conclude_without_matches():
    assert "Nu sunt destule" in verdict({"meciuri": 0}, pd.DataFrame())
