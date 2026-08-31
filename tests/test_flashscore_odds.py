"""Cotele pre-meci de la Flashscore.

Testele lucreaza pe raspunsuri reale, copiate din endpoint pe 31 august 2026.
Partea care conteaza cel mai mult: cand formatul se schimba, sa se auda - nu sa
scriem tacut zero randuri si sa credem ca meciul n-are cote.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from predictiescor.data.flashscoreodds import (
    AVERAGE, BEST, OddsFormatChanged, OddsRow, aggregate, decode,
    implied_margin, menu_query, odds_query, parse_bookmakers, parse_odds,
)

# raspuns real pentru Rapid Bucuresti - Univ. Craiova, casa 516
REAL_1X2 = {
    "data": {"findPrematchOddsForBookmaker": {
        "__typename": "EventOddsOverviewHomeDrawAway", "bookmakerId": 516,
        "type": "HOME_DRAW_AWAY",
        "home": {"value": "2.87", "opening": "2.7", "active": True},
        "draw": {"value": "3.35", "opening": "3.2", "active": True},
        "away": {"value": "2.62", "opening": "2.82", "active": True}}},
}

REAL_OVER_UNDER = {
    "data": {"findPrematchOddsForBookmaker": {
        "type": "OVER_UNDER", "opportunities": [
            {"over": {"value": "1.36", "opening": "1.4"},
             "under": {"value": "3.2", "opening": "3.0"},
             "handicap": {"value": "1.5"}},
            {"over": {"value": "2.05", "opening": "2.1"},
             "under": {"value": "1.78", "opening": "1.75"},
             "handicap": {"value": "2.5"}}]}},
}

REAL_BTTS = {
    "data": {"findPrematchOddsForBookmaker": {
        "type": "BOTH_TEAMS_TO_SCORE",
        "yes": {"value": "1.78", "opening": "1.88"},
        "no": {"value": "1.95", "opening": "1.85"}}},
}


# ----------------------------------------------------------------------
def test_1x2_is_parsed_with_opening_prices():
    rows = parse_odds(REAL_1X2, "HOME_DRAW_AWAY")
    assert {(r.selection, r.price) for r in rows} == {
        ("home", 2.87), ("draw", 3.35), ("away", 2.62)}
    assert all(r.market == "1x2" for r in rows)
    assert next(r.opening for r in rows if r.selection == "home") == 2.7


def test_every_over_under_line_becomes_its_own_market():
    rows = parse_odds(REAL_OVER_UNDER, "OVER_UNDER")
    assert {r.market for r in rows} == {"ou_1.5", "ou_2.5"}
    price = {(r.market, r.selection): r.price for r in rows}
    assert price[("ou_2.5", "over")] == 2.05
    assert price[("ou_2.5", "under")] == 1.78


def test_both_teams_to_score_is_parsed():
    rows = parse_odds(REAL_BTTS, "BOTH_TEAMS_TO_SCORE")
    assert {(r.selection, r.price) for r in rows} == {("yes", 1.78), ("no", 1.95)}


def test_a_match_without_odds_is_not_an_error():
    """Meciul de peste doua saptamani inca n-are cote - e normal, nu o defectiune."""
    assert parse_odds({"data": {"findPrematchOddsForBookmaker": None}},
                      "HOME_DRAW_AWAY") == []


def test_impossible_prices_are_dropped():
    """O cota sub 1.0 nu exista; daca apare, ceva e gresit si o ignoram."""
    payload = {"data": {"findPrematchOddsForBookmaker": {
        "home": {"value": "0.5"}, "draw": {"value": "3.35"},
        "away": {"value": "abc"}}}}
    rows = parse_odds(payload, "HOME_DRAW_AWAY")
    assert [(r.selection, r.price) for r in rows] == [("draw", 3.35)]


def test_a_line_without_a_handicap_is_skipped():
    payload = {"data": {"findPrematchOddsForBookmaker": {"opportunities": [
        {"over": {"value": "2.0"}, "under": {"value": "1.8"},
         "handicap": {"value": None}}]}}}
    assert parse_odds(payload, "OVER_UNDER") == []


# ----------------------------------------------------------------------
# Cand se schimba formatul, trebuie sa se auda
# ----------------------------------------------------------------------
def test_a_graphql_error_is_raised_not_swallowed():
    with pytest.raises(OddsFormatChanged):
        parse_odds({"errors": [{"message": "PersistedQueryNotFound"}]},
                   "HOME_DRAW_AWAY")


def test_a_missing_data_key_is_raised():
    with pytest.raises(OddsFormatChanged, match="data"):
        parse_odds({"altceva": 1}, "HOME_DRAW_AWAY")


def test_a_non_object_response_is_raised():
    with pytest.raises(OddsFormatChanged):
        parse_odds([1, 2, 3], "HOME_DRAW_AWAY")


def test_an_unknown_bet_type_is_raised():
    with pytest.raises(OddsFormatChanged, match="tip de pariu"):
        parse_odds(REAL_1X2, "CEVA_NOU")


def test_broken_json_is_reported_clearly():
    with pytest.raises(OddsFormatChanged, match="JSON"):
        decode("<html>eroare</html>")


# ----------------------------------------------------------------------
# Agregate
# ----------------------------------------------------------------------
def _book(home, draw, away):
    return [OddsRow("1x2", "home", home), OddsRow("1x2", "draw", draw),
            OddsRow("1x2", "away", away)]


def test_best_price_takes_the_highest_of_each_outcome():
    combined = aggregate({"a": _book(2.80, 3.25, 2.60),
                          "b": _book(3.10, 3.40, 2.35)})
    best = {r.selection: r.price for name, r in combined if name == BEST}
    assert best == {"home": 3.10, "draw": 3.40, "away": 2.60}


def test_average_sits_between_the_books():
    combined = aggregate({"a": _book(2.80, 3.20, 2.60),
                          "b": _book(3.20, 3.40, 2.40)})
    avg = {r.selection: r.price for name, r in combined if name == AVERAGE}
    assert avg["home"] == pytest.approx(3.00)
    assert avg["draw"] == pytest.approx(3.30)


def test_the_best_price_always_beats_the_average():
    """Altfel am incurcat agregatele intre ele."""
    combined = aggregate({"a": _book(2.80, 3.25, 2.60),
                          "b": _book(3.10, 3.40, 2.35)})
    avg = {r.selection: r.price for n, r in combined if n == AVERAGE}
    best = {r.selection: r.price for n, r in combined if n == BEST}
    assert all(best[k] >= avg[k] for k in avg)


def test_aggregating_nothing_gives_nothing():
    assert aggregate({}) == []


def test_margin_is_computed_from_the_three_prices():
    margin = implied_margin(_book(2.87, 3.35, 2.62))
    assert margin == pytest.approx(0.029, abs=0.002)


def test_best_price_across_books_can_erase_the_margin():
    """Semn ca datele sunt reale: cel mai bun pret la fiecare rezultat
    lasa marja aproape de zero, uneori sub."""
    assert implied_margin(_book(3.10, 3.45, 2.62)) < 0.01


def test_margin_needs_at_least_two_prices():
    assert implied_margin([OddsRow("1x2", "home", 2.0)]) is None


# ----------------------------------------------------------------------
# Adrese
# ----------------------------------------------------------------------
def test_the_odds_url_carries_every_parameter():
    url = odds_query("GKc8uThe", 516, "HOME_DRAW_AWAY")
    for part in ("eventId=GKc8uThe", "bookmakerId=516",
                 "betType=HOME_DRAW_AWAY", "betScope=FULL_TIME", "_hash="):
        assert part in url


def test_the_menu_url_includes_the_subdivision():
    """Fara `geoIpSubdivisionCode` endpointul raspunde 400 - verificat direct."""
    assert "geoIpSubdivisionCode=" in menu_query("GKc8uThe")


def test_bookmakers_are_read_and_named_short():
    payload = {"data": {"menu": {"settings": {"bookmakers": [
        {"bookmaker": {"id": 516, "name": "Betano.ro"}},
        {"bookmaker": {"id": 592, "name": "Superbet.ro"}},
        {"bookmaker": {"id": 999, "name": "Casa Noua SRL"}}]}}}}
    assert parse_bookmakers(payload) == [
        (516, "betano"), (592, "superbet"), (999, "casanouasrl")]


def test_a_menu_without_bookmakers_gives_an_empty_list():
    assert parse_bookmakers({"data": {"menu": {}}}) == []
    assert parse_bookmakers({}) == []


# ----------------------------------------------------------------------
# Sansa dubla: numele campurilor au fost verificate in raspunsul real, nu
# ghicite. Prima incercare ("homeDraw"/"homeAway"/"drawAway") nu potrivea
# nimic, iar cotele n-ar fi ajuns niciodata pe bilete.
# ----------------------------------------------------------------------
REAL_DOUBLE_CHANCE = {
    "data": {"findPrematchOddsForBookmaker": {
        "type": "DOUBLE_CHANCE",
        "homeOrDraw": {"value": "1.3", "opening": "1.3"},
        "awayOrDraw": {"value": "1.85", "opening": "1.9"},
        "noDraw": {"value": "1.28", "opening": "1.3"}}},
}


def test_double_chance_uses_the_real_field_names():
    rows = parse_odds(REAL_DOUBLE_CHANCE, "DOUBLE_CHANCE")
    assert {(r.selection, r.price) for r in rows} == {
        ("1x", 1.3), ("x2", 1.85), ("12", 1.28)}
    assert all(r.market == "dc" for r in rows)


def test_double_chance_selections_match_the_ticket_vocabulary():
    """Cheile trebuie sa fie exact cele cautate de generatorul de bilete."""
    selections = {r.selection for r in parse_odds(REAL_DOUBLE_CHANCE,
                                                  "DOUBLE_CHANCE")}
    assert selections == {"1x", "x2", "12"}


def test_the_old_guessed_field_names_yield_nothing():
    """Daca cineva revine la numele ghicite, testul cade."""
    guessed = {"data": {"findPrematchOddsForBookmaker": {
        "homeDraw": {"value": "1.3"}, "homeAway": {"value": "1.28"},
        "drawAway": {"value": "1.85"}}}}
    assert parse_odds(guessed, "DOUBLE_CHANCE") == []
