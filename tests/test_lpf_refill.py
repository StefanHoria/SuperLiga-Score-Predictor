"""Ce se întâmplă după ce LPF își completează pagina.

Bug-ul avea două capete, iar reparat unul singur n-ar fi ajutat:

* pagina pe jumătate completată era luată drept date reale (`test_lpf.py`);
* pagina se citea **o singură dată în viața ei**, deci ce s-a scris atunci
  rămânea acolo pentru totdeauna.

Aici e al doilea capăt, plus redecontarea biletelor judecate pe cifrele
greșite: „sub 8.5 cornere" ieșise câștigat contra unui zero inventat, la un
meci care avusese nouă.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from predictiescor.data.lpf import LpfProvider
from predictiescor.storage.db import Database
from predictiescor.tickets.settlement import resettle_matches

ZIUA = "2026-08-30"


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "refill.db")


def _match(db, *, goals=(1, 3)):
    return db.upsert_match(
        season=2026, utc_date=f"{ZIUA}T18:00:00+00:00",
        home="FCSB", away="UTA Arad",
        home_goals=goals[0], away_goals=goals[1], status="finished")


def _lpf_row(db, match_id, page_id="3640", *, passes, corners):
    """Un rând de statistici scris de LPF, plus legătura cu pagina."""
    with db.connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO match_external_ids(match_id, source, ext_id)"
            " VALUES (?,?,?)", (match_id, "lpf", page_id))
    for team, is_home in (("FCSB", True), ("UTA Arad", False)):
        team_id = db.upsert_team(team)
        db.upsert_team_stats(match_id, team_id, is_home,
                             {"passes": passes, "corners": corners},
                             source="lpf")
        db.upsert_player_stats(match_id, db.upsert_player(team, team_id),
                               team_id, {"minutes": 90}, source="lpf")


def _known(db):
    return LpfProvider(db=db)._known_page_ids()


# ----------------------------------------------------------------------
# Recitirea
# ----------------------------------------------------------------------
def test_a_complete_page_is_left_alone(db):
    match_id = _match(db)
    _lpf_row(db, match_id, passes=400.0, corners=5.0)
    assert 3640 in _known(db)


def test_a_page_written_with_zero_passes_is_read_again(db):
    """Rândul scris dintr-o pagină necompletată trebuie reluat.

    Fără asta, cele cinci meciuri stricate ar fi rămas stricate pentru
    totdeauna — pagina nu se mai citea niciodată.
    """
    match_id = _match(db)
    _lpf_row(db, match_id, passes=0.0, corners=0.0)
    assert 3640 not in _known(db)


def test_a_page_with_no_passes_at_all_is_read_again(db):
    match_id = _match(db)
    _lpf_row(db, match_id, passes=None, corners=None)
    assert 3640 not in _known(db)


def test_a_refill_overwrites_the_zeros(db):
    """`upsert_team_stats` păstrează valoarea veche doar când cea nouă lipsește."""
    match_id = _match(db)
    _lpf_row(db, match_id, passes=0.0, corners=0.0)
    _lpf_row(db, match_id, passes=757.0, corners=9.0)
    with db.connect() as conn:
        total = conn.execute(
            "SELECT SUM(corners) c FROM match_team_stats WHERE match_id=?",
            (match_id,)).fetchone()["c"]
    assert total == 18.0, "9 pe echipă, cum le-a scris a doua citire"


def test_a_null_does_not_erase_a_real_value(db):
    """Cealaltă direcție: un provider fără valoare nu șterge ce se știe deja."""
    match_id = _match(db)
    _lpf_row(db, match_id, passes=757.0, corners=9.0)
    _lpf_row(db, match_id, passes=None, corners=None)
    with db.connect() as conn:
        total = conn.execute(
            "SELECT SUM(corners) c FROM match_team_stats WHERE match_id=?",
            (match_id,)).fetchone()["c"]
    assert total == 18.0


# ----------------------------------------------------------------------
# Redecontarea
# ----------------------------------------------------------------------
def _ticket(db, match_id, market, selection):
    with db.connect() as conn:
        cur = conn.execute(
            "INSERT INTO tickets(created_at, match_day, strategy, label, stake,"
            " legs_total, status) VALUES ('x',?,'s','Bilet',10,1,'deschis')",
            (ZIUA,))
        ticket_id = cur.lastrowid
        conn.execute(
            "INSERT INTO ticket_legs(ticket_id, match_id, market, selection,"
            " description, probability, fair_odds, offered_odds, result)"
            " VALUES (?,?,?,?,'x',0.5,2.0,2.0,'deschis')",
            (ticket_id, match_id, market, selection))
    return ticket_id


def _leg(db, ticket_id):
    with db.connect() as conn:
        row = conn.execute(
            "SELECT result, actual_value FROM ticket_legs WHERE ticket_id=?",
            (ticket_id,)).fetchone()
    return row["result"], row["actual_value"]


def test_a_bet_judged_on_bad_data_is_judged_again(db):
    """Exact cazul FCSB — UTA Arad."""
    from predictiescor.tickets.settlement import settle_tickets

    match_id = _match(db)
    _lpf_row(db, match_id, passes=0.0, corners=0.0)
    ticket_id = _ticket(db, match_id, "cornere", "sub 8.5")

    settle_tickets(db)
    assert _leg(db, ticket_id) == ("castigat", "0 cornere"), "verdictul greșit"

    # LPF își completează pagina: nouă cornere, nu zero
    _lpf_row(db, match_id, passes=757.0, corners=4.5)
    resettle_matches(db, [match_id])
    assert _leg(db, ticket_id) == ("pierdut", "9 cornere")


def test_the_other_direction_too(db):
    """Un pont dat pierdut pe zero devine câștigat pe cifra adevărată."""
    from predictiescor.tickets.settlement import settle_tickets

    match_id = _match(db)
    _lpf_row(db, match_id, passes=0.0, corners=0.0)
    ticket_id = _ticket(db, match_id, "cornere", "peste 7.5")

    settle_tickets(db)
    assert _leg(db, ticket_id)[0] == "pierdut"

    _lpf_row(db, match_id, passes=757.0, corners=4.5)
    resettle_matches(db, [match_id])
    assert _leg(db, ticket_id) == ("castigat", "9 cornere")


def test_resettling_leaves_other_matches_alone(db):
    """Se rejudecă doar ce s-a cerut, nu toată istoria."""
    from predictiescor.tickets.settlement import settle_tickets

    reparat = _match(db)
    _lpf_row(db, reparat, passes=757.0, corners=4.5)
    altul = db.upsert_match(
        season=2026, utc_date=f"{ZIUA}T13:00:00+00:00",
        home="Farul Constanta", away="FC Botosani",
        home_goals=0, away_goals=0, status="finished")

    de_pastrat = _ticket(db, altul, "total goluri", "sub 3.5")
    settle_tickets(db)
    inainte = _leg(db, de_pastrat)

    resettle_matches(db, [reparat])
    assert _leg(db, de_pastrat) == inainte


def test_resettling_nothing_is_harmless(db):
    assert resettle_matches(db, []) == {"decontate": 0, "in_asteptare": 0,
                                        "ponturi": 0}
