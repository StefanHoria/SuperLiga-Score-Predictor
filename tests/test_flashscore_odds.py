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


# ----------------------------------------------------------------------
# Toleranta la pene de moment
#
# Pe 1 septembrie 2026 un singur raspuns gol a oprit tot pasul de cote si a
# marcat actualizarea zilnica drept esuata - desi endpointul mergea perfect
# cateva minute mai tarziu. O schimbare reala de format cade de fiecare data.
# ----------------------------------------------------------------------
class _FakeProvider:
    """Doar bucata din provider care ne intereseaza, fara retea si fara baza."""

    FORMAT_FAILURE_LIMIT = 3

    def __init__(self, failures):
        self.failures = list(failures)     # True = raspuns stricat
        self.written = 0

    def run(self):
        """Copia buclei din fetch_odds, ca sa testam doar regula de oprire."""
        consecutive, tolerated, aborted = 0, [], False
        for broken in self.failures:
            if broken:
                consecutive += 1
                tolerated.append("raspuns stricat")
                if consecutive >= self.FORMAT_FAILURE_LIMIT:
                    aborted = True
                    break
                continue
            consecutive = 0
            self.written += 1
        return {"scrise": self.written, "tolerate": len(tolerated),
                "oprit": aborted}


def test_an_isolated_broken_response_does_not_stop_the_step():
    out = _FakeProvider([False, True, False, False]).run()
    assert out["oprit"] is False
    assert out["scrise"] == 3
    assert out["tolerate"] == 1


def test_scattered_failures_are_tolerated():
    """Doua pene separate de reusite nu inseamna format schimbat."""
    out = _FakeProvider([True, False, True, False, True, False]).run()
    assert out["oprit"] is False
    assert out["scrise"] == 3


def test_three_failures_in_a_row_do_stop_the_step():
    out = _FakeProvider([False, True, True, True, False]).run()
    assert out["oprit"] is True
    assert out["scrise"] == 1


def test_a_dead_endpoint_stops_immediately():
    out = _FakeProvider([True] * 10).run()
    assert out["oprit"] is True
    assert out["scrise"] == 0
    assert out["tolerate"] == 3


def test_the_counter_resets_after_a_success():
    """Doua pene, o reusita, inca doua: nu se opreste."""
    out = _FakeProvider([True, True, False, True, True, False]).run()
    assert out["oprit"] is False
    assert out["scrise"] == 2


# ----------------------------------------------------------------------
# Raportul de acoperire din pagina "Cotele casei"
# ----------------------------------------------------------------------
def _db_with_odds(tmp_path):
    from predictiescor.storage.db import Database
    db = Database(tmp_path / "acoperire.db")
    match_id = db.upsert_match(season=2026,
                               utc_date="2026-09-20T17:30:00+00:00",
                               home="FCSB", away="UTA Arad", status="scheduled")
    db.upsert_odds([
        (match_id, "betano", "1x2", "home", 2.00),
        (match_id, "betano", "1x2", "draw", 3.40),
        (match_id, "betano", "1x2", "away", 3.80),
        (match_id, "fs_max", "1x2", "home", 2.10),
        (match_id, "fs_max", "1x2", "draw", 3.50),
        (match_id, "fs_max", "1x2", "away", 3.90),
    ])
    return db


def test_aggregates_are_not_counted_as_bookmakers(tmp_path):
    """`fs_avg` si `fs_max` sunt calcule proprii, nu case de pariuri."""
    from predictiescor.data.odds_input import automatic_odds
    report = automatic_odds(_db_with_odds(tmp_path))
    assert report["case"] == 1
    assert set(report["pe_casa"]["casă"]) == {"betano", "fs_max"}


def test_the_margin_is_computed_only_on_real_bookmakers(tmp_path):
    """Pe 'cel mai bun pret' marja iese artificial mica, uneori negativa."""
    from predictiescor.data.odds_input import automatic_odds
    report = automatic_odds(_db_with_odds(tmp_path))
    expected = 1 / 2.00 + 1 / 3.40 + 1 / 3.80 - 1
    assert report["marja"] == pytest.approx(expected, abs=1e-6)


def test_no_odds_gives_no_margin(tmp_path):
    from predictiescor.data.odds_input import automatic_odds
    from predictiescor.storage.db import Database
    report = automatic_odds(Database(tmp_path / "gol.db"))
    assert report["marja"] is None
    assert report["case"] == 0


# ----------------------------------------------------------------------
# Cota de deschidere
#
# Era extrasa de parser, apoi aruncata: tabela n-avea coloana, iar `upsert_odds`
# suprascria pretul la fiecare rulare. Fiecare zi de rulari stergea definitiv
# preturile anterioare, desi README promitea ca le pastram.
# ----------------------------------------------------------------------
def _match_with_odds(tmp_path, name="deschidere.db"):
    from predictiescor.storage.db import Database
    db = Database(tmp_path / name)
    match_id = db.upsert_match(season=2026,
                               utc_date="2026-09-20T17:30:00+00:00",
                               home="FCSB", away="UTA Arad", status="scheduled")
    return db, match_id


def test_the_opening_price_is_stored(tmp_path):
    db, match_id = _match_with_odds(tmp_path)
    db.upsert_odds([(match_id, "betano", "1x2", "home", 2.85, 2.70)])
    with db.connect() as conn:
        row = conn.execute("SELECT price, opening FROM odds").fetchone()
    assert row["price"] == 2.85
    assert row["opening"] == 2.70


def test_the_opening_is_never_overwritten(tmp_path):
    """Miezul: deschiderea e prima cota vazuta si ramane asa."""
    db, match_id = _match_with_odds(tmp_path)
    db.upsert_odds([(match_id, "betano", "1x2", "home", 2.85, 2.70)])
    db.upsert_odds([(match_id, "betano", "1x2", "home", 2.40, 2.55)])
    with db.connect() as conn:
        row = conn.execute("SELECT price, opening FROM odds").fetchone()
    assert row["price"] == 2.40, "prețul curent se actualizează"
    assert row["opening"] == 2.70, "deschiderea rămâne cea dintâi"


def test_rows_without_an_opening_still_work(tmp_path):
    """Agregatele `fs_avg`/`fs_max` n-au deschidere proprie."""
    db, match_id = _match_with_odds(tmp_path)
    db.upsert_odds([(match_id, "fs_max", "1x2", "home", 3.10)])
    with db.connect() as conn:
        row = conn.execute("SELECT price, opening FROM odds").fetchone()
    assert row["price"] == 3.10
    assert row["opening"] is None


def test_a_later_opening_fills_a_missing_one(tmp_path):
    """Daca prima scriere n-a avut deschidere, o acceptam pe urmatoarea."""
    db, match_id = _match_with_odds(tmp_path)
    db.upsert_odds([(match_id, "betano", "1x2", "home", 2.85)])
    db.upsert_odds([(match_id, "betano", "1x2", "home", 2.80, 2.70)])
    with db.connect() as conn:
        assert conn.execute("SELECT opening FROM odds").fetchone()["opening"] == 2.70


def test_the_write_time_is_recorded(tmp_path):
    db, match_id = _match_with_odds(tmp_path)
    db.upsert_odds([(match_id, "betano", "1x2", "home", 2.85, 2.70)])
    with db.connect() as conn:
        assert conn.execute("SELECT updated_at FROM odds").fetchone()["updated_at"]


# ----------------------------------------------------------------------
# Mișcarea liniei
# ----------------------------------------------------------------------
def test_movement_is_measured_in_probability_not_price(tmp_path):
    """O mutare 1.20 -> 1.30 contează mult mai mult ca 6.00 -> 6.10."""
    from predictiescor.data.odds_input import line_movement
    db, match_id = _match_with_odds(tmp_path)
    db.upsert_odds([
        (match_id, "betano", "1x2", "home", 1.30, 1.20),   # mutare mare
        (match_id, "betano", "1x2", "away", 6.10, 6.00),   # mutare mica
    ])
    moved = line_movement(db, min_move=0.02)
    assert list(moved["selection"]) == ["home"]


def test_the_direction_is_reported(tmp_path):
    from predictiescor.data.odds_input import line_movement
    db, match_id = _match_with_odds(tmp_path)
    db.upsert_odds([
        (match_id, "betano", "1x2", "home", 1.65, 2.25),   # cota a scazut
        (match_id, "betano", "1x2", "away", 5.00, 3.25),   # cota a crescut
    ])
    moved = line_movement(db, min_move=0.05).set_index("selection")
    assert "MAI MULT" in moved.loc["home", "sens"]
    assert "MAI PUȚIN" in moved.loc["away", "sens"]


def test_odds_without_an_opening_are_not_movement(tmp_path):
    from predictiescor.data.odds_input import line_movement
    db, match_id = _match_with_odds(tmp_path)
    db.upsert_odds([(match_id, "fs_max", "1x2", "home", 3.10)])
    assert line_movement(db).empty
