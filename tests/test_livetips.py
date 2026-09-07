"""Ponturi live: proiectia din ce s-a intamplat pana acum.

Doua greseli usor de facut, si amandoua sunt testate aici:

* sa iei ritmul observat pe nemestecate — trei cornere in 45 de minute ar
  insemna sase la final, oricat de des ar avea echipele astea zece;
* sa ignori ce s-a jucat — proiectia ar ramane cea de dinainte de start.

Amestecul e o actualizare Gamma-Poisson, iar testele verifica exact capetele
ei: la minutul 0 conteaza doar asteptarea, la 90 doar ce s-a intamplat.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from predictiescor.modeling.inplay import InPlayState
from predictiescor.modeling.livetips import (
    MAX_PROBABILITY, MIN_PROBABILITY, Tip, live_tips, over_under_from_now,
    projected_total, remaining_mean,
)


# ----------------------------------------------------------------------
# Proiectia
# ----------------------------------------------------------------------
def test_before_kickoff_the_projection_is_the_expectation():
    """La minutul 0 nu s-a vazut nimic, deci ramane asteptarea."""
    assert projected_total(9.5, 0, 0) == pytest.approx(9.5)


def test_at_the_final_whistle_nothing_more_is_added():
    """Ce s-a jucat, s-a jucat."""
    assert projected_total(9.5, 4, 90) == pytest.approx(4.0)
    assert remaining_mean(9.5, 4, 90) == 0.0


def test_a_quiet_match_is_projected_below_the_expectation():
    """Trei cornere in 45 de minute: sub asteptare, dar nu la ritmul observat."""
    total = projected_total(9.5, 3, 45)
    assert 3 < total < 9.5, "proiectia sta intre ce s-a vazut si ce se astepta"
    # ritm = (1*9.5 + 3) / (1 + 0.5) = 8.33 pe 90; ramase 8.33 * 0.5 = 4.17
    assert total == pytest.approx(7.17, abs=0.05)


def test_a_busy_match_is_projected_above_the_expectation():
    total = projected_total(9.5, 8, 45)
    assert total > 9.5


def test_the_observed_rate_alone_would_be_more_extreme():
    """Amestecul trage spre asteptare — asta e tot rostul lui."""
    ritm_observat = 3 / 0.5          # trei cornere in jumatate de meci
    assert projected_total(9.5, 3, 45) > ritm_observat


def test_later_in_the_match_the_evidence_weighs_more():
    """Aceleasi trei cornere spun mai mult in minutul 80 decat in minutul 10."""
    devreme = projected_total(9.5, 3, 10)
    tarziu = projected_total(9.5, 3, 80)
    assert tarziu < devreme, "spre final proiectia se apropie de ce s-a vazut"


def test_the_projection_never_goes_below_what_happened():
    assert projected_total(2.0, 7, 80) >= 7


# ----------------------------------------------------------------------
# Peste/sub, stiind ce s-a intamplat deja
# ----------------------------------------------------------------------
def test_a_line_already_passed_is_certain():
    """Cu 9 cornere date, «peste 8.5» nu mai poate pica."""
    assert over_under_from_now(9, 2.0, 8.5) == {"peste": 1.0, "sub": 0.0}


def test_a_line_out_of_reach_at_the_whistle():
    assert over_under_from_now(3, 0.0, 8.5) == {"peste": 0.0, "sub": 1.0}


def test_the_line_moves_by_what_already_happened():
    """«Peste 8.5» cu 3 date inseamna «inca cel putin 6», nu «cel putin 9»."""
    cu_trei = over_under_from_now(3, 5.0, 8.5)["peste"]
    de_la_zero = over_under_from_now(0, 5.0, 8.5)["peste"]
    assert cu_trei > de_la_zero


def test_the_two_sides_add_up_to_one():
    book = over_under_from_now(3, 5.0, 8.5)
    assert book["peste"] + book["sub"] == pytest.approx(1.0)


# ----------------------------------------------------------------------
# Alegerea ponturilor
# ----------------------------------------------------------------------
class _FakeStat:
    def __init__(self, total, over=0.5):
        self.total, self.over = total, over

    def expected_match(self, home, away):
        return self.total / 2, self.total / 2

    def match_total_over_under(self, home, away, line):
        return {"peste": self.over, "sub": 1 - self.over}


class _FakeSecondary:
    def __init__(self):
        self.models = {"corners": _FakeStat(9.5, 0.45),
                       "yellow_cards": _FakeStat(4.5, 0.40)}


class _FakePrediction:
    markets = {"1X2": {"gazda": 0.45, "egal": 0.28, "oaspete": 0.27},
               "total goluri 2.5": {"peste": 0.52, "sub": 0.48}}


class _FakePipeline:
    secondary = _FakeSecondary()

    def predict(self, home, away):
        return _FakePrediction()


def _state(minute=45, home_goals=0, away_goals=0):
    return InPlayState("Gazda", "Oaspete", home_goals, away_goals, minute)


def _tips(monkeypatch, markets, **kwargs):
    """Ponturile, cu pietele de goluri fixate ca sa fie de urmarit."""
    import predictiescor.modeling.livetips as mod
    monkeypatch.setattr(mod, "live_markets",
                        lambda pipeline, state: {"piete": markets})
    stats = kwargs.pop("stats", {"corners": (3.0, 0.0),
                                 "yellow_cards": (1.0, 0.0)})
    return live_tips(_FakePipeline(), kwargs.pop("state", _state()),
                     stats, **kwargs)


def test_a_certainty_is_not_a_tip(monkeypatch):
    """«Peste 0.5 goluri» la 2-0 are probabilitatea 1 si nu spune nimic."""
    tips = _tips(monkeypatch, {"total goluri 0.5": {"peste": 1.0, "sub": 0.0}})
    assert all(t.market != "total goluri" for t in tips)


def test_a_coin_flip_is_not_a_tip(monkeypatch):
    tips = _tips(monkeypatch, {"1X2": {"gazda": 0.5, "egal": 0.3,
                                       "oaspete": 0.2}})
    assert all(t.market != "rezultat final" for t in tips)


def test_every_tip_is_inside_the_window(monkeypatch):
    tips = _tips(monkeypatch, {"1X2": {"gazda": 0.8, "egal": 0.1,
                                       "oaspete": 0.1}})
    assert tips
    for tip in tips:
        assert MIN_PROBABILITY <= tip.probability <= MAX_PROBABILITY


def test_one_tip_per_market(monkeypatch):
    """Doua linii vecine spun aproape acelasi lucru."""
    tips = _tips(monkeypatch, {})
    assert len(tips) == len({t.market for t in tips})


def test_ordered_by_how_much_the_match_moved_things(monkeypatch):
    tips = _tips(monkeypatch, {"1X2": {"gazda": 0.8, "egal": 0.1,
                                       "oaspete": 0.1}})
    miscari = [abs(t.movement) for t in tips]
    assert miscari == sorted(miscari, reverse=True)


def test_the_comparison_uses_the_same_selection(monkeypatch):
    """«Sub» se compara cu «sub» de dinainte, nu cu «peste».

    Comparate gresit, un pont linistit parea ca a mutat piata cu 66 de puncte
    cand o mutase cu 24.
    """
    tips = _tips(monkeypatch, {})
    sub = next(t for t in tips
               if t.market == "cornere" and t.selection.startswith("sub"))
    # modelul fals da 45% pentru „peste", deci 55% pentru „sub"
    assert sub.prematch == pytest.approx(0.55)


def test_after_the_final_whistle_there_is_nothing_to_propose(monkeypatch):
    assert _tips(monkeypatch, {"1X2": {"gazda": 0.8, "egal": 0.1,
                                       "oaspete": 0.1}},
                 state=_state(minute=90)) == []


def test_missing_statistics_only_drop_those_markets(monkeypatch):
    """Fara statistici raman ponturile pe goluri; nu se inventeaza cifre."""
    tips = _tips(monkeypatch, {"1X2": {"gazda": 0.8, "egal": 0.1,
                                       "oaspete": 0.1}}, stats={})
    assert tips
    assert all(t.market not in ("cornere", "cartonașe") for t in tips)


def test_the_limit_is_respected(monkeypatch):
    assert len(_tips(monkeypatch, {"1X2": {"gazda": 0.8, "egal": 0.1,
                                           "oaspete": 0.1}}, limit=2)) <= 2


def test_fair_odds_are_the_inverse_of_the_probability():
    tip = Tip("cornere", "sub 8.5", "cornere: sub 8.5", 0.80, 0.55, "")
    assert tip.fair_odds == pytest.approx(1.25)
    assert tip.movement == pytest.approx(0.25)
