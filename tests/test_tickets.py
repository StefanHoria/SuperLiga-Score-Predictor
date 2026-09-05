"""Decontarea biletelor: fiecare piata trebuie judecata corect."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from predictiescor.tickets.generator import Leg, Ticket
from predictiescor.tickets.settlement import (
    LOST, VOID, WON, MatchFacts, settle_leg,
)


def facts(home=2, away=1, *, stats=None, scorers=None, has_players=True):
    stats = stats or {}
    return MatchFacts(
        match_id=1, home_goals=home, away_goals=away,
        home_stats=stats.get("home", {}), away_stats=stats.get("away", {}),
        scorers=set(scorers or []), has_player_data=has_players,
    )


# ----------------------------------------------------------------------
@pytest.mark.parametrize("selection,expected", [
    ("gazda", WON), ("egal", LOST), ("oaspete", LOST)])
def test_1x2(selection, expected):
    assert settle_leg("1X2", selection, facts(2, 1))[0] == expected


@pytest.mark.parametrize("selection,expected", [
    ("1X", WON), ("12", WON), ("X2", LOST)])
def test_double_chance_home_win(selection, expected):
    assert settle_leg("șansă dublă", selection, facts(2, 1))[0] == expected


@pytest.mark.parametrize("selection,expected", [
    ("1X", WON), ("X2", WON), ("12", LOST)])
def test_double_chance_draw(selection, expected):
    assert settle_leg("șansă dublă", selection, facts(1, 1))[0] == expected


@pytest.mark.parametrize("selection,expected", [
    ("peste 0.5", WON), ("peste 2.5", WON), ("peste 3.5", LOST),
    ("sub 3.5", WON), ("sub 2.5", LOST)])
def test_total_goals(selection, expected):
    # 2-1 = 3 goluri
    assert settle_leg("total goluri", selection, facts(2, 1))[0] == expected


def test_btts():
    assert settle_leg("ambele marchează", "da", facts(2, 1))[0] == WON
    assert settle_leg("ambele marchează", "da", facts(2, 0))[0] == LOST
    assert settle_leg("ambele marchează", "nu", facts(2, 0))[0] == WON


def test_team_to_score():
    assert settle_leg("echipa marchează", "gazda", facts(1, 0))[0] == WON
    assert settle_leg("echipa marchează", "oaspete", facts(1, 0))[0] == LOST


# ----------------------------------------------------------------------
def test_corners_use_match_total():
    stats = {"home": {"corners": 6}, "away": {"corners": 4}}  # total 10
    assert settle_leg("cornere", "peste 9.5", facts(stats=stats))[0] == WON
    assert settle_leg("cornere", "peste 10.5", facts(stats=stats))[0] == LOST
    assert settle_leg("cornere", "sub 10.5", facts(stats=stats))[0] == WON


def test_missing_stats_void_not_guessed():
    """Fara statistici, piciorul se anuleaza - nu se ghiceste."""
    result, message = settle_leg("cornere", "peste 9.5", facts())
    assert result == VOID
    assert "indisponibile" in message


def test_missing_player_data_voids_scorer_leg():
    result, _ = settle_leg("marcatori", "gol::Daniel Birligea",
                           facts(has_players=False))
    assert result == VOID


def test_scorer_matching_tolerates_spelling():
    played = facts(scorers=["Daniel Bîrligea"])
    assert settle_leg("marcatori", "gol::Daniel Birligea", played)[0] == WON
    assert settle_leg("marcatori", "gol::D. Birligea", played)[0] == WON
    assert settle_leg("marcatori", "gol::Florin Tanase", played)[0] == LOST


def test_unknown_market_is_void():
    assert settle_leg("piata inventata", "orice", facts())[0] == VOID


# ----------------------------------------------------------------------
def test_ticket_odds_and_probability_compose():
    legs = [
        Leg(1, "A", "B", "", "1X2", "gazda", "A câștigă", 0.5),
        Leg(2, "C", "D", "", "1X2", "gazda", "C câștigă", 0.25),
    ]
    ticket = Ticket("test", "Test", "2026-08-30", legs, stake=10.0)
    assert ticket.probability == pytest.approx(0.125)
    assert ticket.total_odds == pytest.approx(2.0 * 4.0)
    assert ticket.potential_payout == pytest.approx(80.0)
    # cu cote corecte, valoarea asteptata e exact zero
    assert ticket.expected_value == pytest.approx(0.0, abs=1e-9)


def test_offered_odds_create_positive_value():
    leg = Leg(1, "A", "B", "", "1X2", "gazda", "A câștigă", 0.5,
              offered_odds=2.5)
    ticket = Ticket("test", "Test", "2026-08-30", [leg], stake=10.0)
    assert ticket.expected_value > 0


# ----------------------------------------------------------------------
# Increderea in marcatori: doar daca reconstruiesc scorul fiecarei echipe
# ----------------------------------------------------------------------
def _match_with_players(db, home_goals, away_goals, credited):
    """`credited` = [(nume, echipa_gazda?, goluri), ...]"""
    match_id = db.upsert_match(
        season=2026, utc_date="2026-08-30T20:00:00+00:00",
        home="FCSB", away="UTA Arad", home_goals=home_goals,
        away_goals=away_goals, status="finished")
    home_id = db.team_id_by_name("FCSB")
    away_id = db.team_id_by_name("UTA Arad")
    for name, is_home, goals in credited:
        team_id = home_id if is_home else away_id
        player_id = db.upsert_player(name, team_id)
        db.upsert_player_stats(match_id, player_id, team_id,
                               {"minutes": 90, "goals": goals}, source="lpf")
    return match_id


def test_scorers_trusted_when_they_rebuild_the_score(tmp_path):
    from predictiescor.storage.db import Database
    from predictiescor.tickets.settlement import load_match_facts

    db = Database(tmp_path / "t.db")
    match_id = _match_with_players(db, 2, 1, [
        ("Daniel Birligea", True, 2.0), ("Alex Pop", False, 1.0)])

    facts = load_match_facts(db, match_id)
    assert facts.has_player_data is True
    assert settle_leg("marcatori", "gol::Daniel Birligea", facts)[0] == WON
    assert settle_leg("marcatori", "gol::Alex Pop", facts)[0] == WON


def test_own_goal_makes_scorers_untrusted(tmp_path):
    """Autogolul pastreaza totalul, dar muta golul la echipa gresita."""
    from predictiescor.storage.db import Database
    from predictiescor.tickets.settlement import load_match_facts

    db = Database(tmp_path / "t.db")
    # scor real 2-1, dar atribuirea arata 1-2 (un autogol)
    match_id = _match_with_players(db, 2, 1, [
        ("Daniel Birligea", True, 1.0), ("Alex Pop", False, 2.0)])

    facts = load_match_facts(db, match_id)
    assert facts.has_player_data is False, "totalul e corect, pe echipe nu"
    assert settle_leg("marcatori", "gol::Alex Pop", facts)[0] == VOID


def test_missing_goal_makes_scorers_untrusted(tmp_path):
    from predictiescor.storage.db import Database
    from predictiescor.tickets.settlement import load_match_facts

    db = Database(tmp_path / "t.db")
    match_id = _match_with_players(db, 3, 0, [("Daniel Birligea", True, 2.0)])

    facts = load_match_facts(db, match_id)
    assert facts.has_player_data is False
    assert settle_leg("marcatori", "gol::Daniel Birligea", facts)[0] == VOID


# ----------------------------------------------------------------------
# Decontare partiala: un bilet cu meciuri in zile diferite
# ----------------------------------------------------------------------
def _two_leg_ticket(db, first_selection, second_selection="peste 0.5"):
    """Bilet cu un meci jucat (0-0) si unul inca neinceput."""
    from predictiescor.tickets.generator import Leg, Ticket, TicketGenerator

    played = db.upsert_match(
        season=2026, utc_date="2026-08-30T15:00:00+00:00",
        home="Farul Constanta", away="FC Botosani",
        home_goals=0, away_goals=0, status="finished")
    pending = db.upsert_match(
        season=2026, utc_date="2026-08-30T20:00:00+00:00",
        home="FCSB", away="UTA Arad", status="scheduled")

    ticket = Ticket("test", "Test", "2026-08-30", [
        Leg(played, "Farul Constanta", "FC Botosani", "", "total goluri",
            first_selection, "pont 1", 0.8),
        Leg(pending, "FCSB", "UTA Arad", "", "total goluri",
            second_selection, "pont 2", 0.8),
    ])

    class _Pipeline:  # generatorul are nevoie doar de `save`
        pass

    generator = TicketGenerator(_Pipeline(), db)
    return generator.save([ticket])[0]


def _ticket_row(db, ticket_id):
    with db.connect() as conn:
        return dict(conn.execute(
            "SELECT * FROM tickets WHERE ticket_id=?", (ticket_id,)).fetchone())


def _leg_results(db, ticket_id):
    with db.connect() as conn:
        return [r["result"] for r in conn.execute(
            "SELECT result FROM ticket_legs WHERE ticket_id=? ORDER BY leg_id",
            (ticket_id,)).fetchall()]


def test_lost_leg_settles_ticket_without_waiting(tmp_path):
    """Un pont ratat decide biletul; nu mai asteptam restul meciurilor."""
    from predictiescor.storage.db import Database
    from predictiescor.tickets.settlement import settle_tickets

    db = Database(tmp_path / "t.db")
    # meciul jucat s-a terminat 0-0, deci "peste 0.5" e pierdut
    ticket_id = _two_leg_ticket(db, "peste 0.5")

    outcome = settle_tickets(db)
    assert outcome["decontate"] == 1

    row = _ticket_row(db, ticket_id)
    assert row["status"] == "pierdut"
    assert row["payout"] == 0.0


def test_won_leg_keeps_ticket_open_but_shows_progress(tmp_path):
    from predictiescor.storage.db import Database
    from predictiescor.tickets.settlement import settle_tickets

    db = Database(tmp_path / "t.db")
    # 0-0 => "sub 3.5" e castigat, dar al doilea meci nu s-a jucat
    ticket_id = _two_leg_ticket(db, "sub 3.5")

    outcome = settle_tickets(db)
    assert outcome["in_asteptare"] == 1

    row = _ticket_row(db, ticket_id)
    assert row["status"] == "deschis"
    assert row["legs_won"] == 1, "progresul trebuie sa fie vizibil"
    assert row["settled_at"] is None


def test_played_leg_gets_its_result_while_ticket_is_open(tmp_path):
    """Piciorul unui meci incheiat nu ramane 'deschis' degeaba."""
    from predictiescor.storage.db import Database
    from predictiescor.tickets.settlement import settle_tickets

    db = Database(tmp_path / "t.db")
    ticket_id = _two_leg_ticket(db, "sub 3.5")
    settle_tickets(db)

    results = _leg_results(db, ticket_id)
    assert results[0] == WON, "meciul jucat trebuie decontat imediat"
    assert results[1] == "deschis", "meciul viitor ramane deschis"


def test_legs_settle_even_after_the_ticket_is_lost(tmp_path):
    """Regresie: un bilet inchis devreme nu trebuie sa-si abandoneze ponturile.

    Biletul se pierde de indata ce un pont rateaza. Daca decontarea cauta doar
    bilete cu starea 'deschis', ponturile ramase pe meciuri care se joaca mai
    tarziu nu mai sunt niciodata evaluate - raman 'deschis' pentru totdeauna si
    dispar din datele de invatare.
    """
    from predictiescor.storage.db import Database
    from predictiescor.tickets.settlement import settle_tickets

    db = Database(tmp_path / "t.db")
    # meciul 1 s-a jucat 0-0 => "peste 0.5" pierde, biletul e decis
    ticket_id = _two_leg_ticket(db, "peste 0.5", "sub 3.5")

    first = settle_tickets(db)
    assert first["decontate"] == 1
    assert _ticket_row(db, ticket_id)["status"] == "pierdut"
    assert _leg_results(db, ticket_id) == [LOST, "deschis"]

    # al doilea meci se joaca abia acum, 1-3
    with db.connect() as conn:
        conn.execute(
            "UPDATE matches SET home_goals=1, away_goals=3, status='finished'"
            " WHERE home_team_id=(SELECT team_id FROM teams WHERE name='FCSB')")

    settle_tickets(db)
    results = _leg_results(db, ticket_id)
    assert results[1] == LOST, "4 goluri: 'sub 3.5' trebuie decontat, nu lasat deschis"
    assert _ticket_row(db, ticket_id)["status"] == "pierdut"


def test_no_open_leg_survives_a_played_match(tmp_path):
    from predictiescor.storage.db import Database
    from predictiescor.tickets.settlement import settle_tickets

    db = Database(tmp_path / "t.db")
    _two_leg_ticket(db, "peste 0.5", "peste 0.5")
    with db.connect() as conn:
        conn.execute(
            "UPDATE matches SET home_goals=2, away_goals=1, status='finished'"
            " WHERE home_goals IS NULL")

    settle_tickets(db)
    with db.connect() as conn:
        remaining = conn.execute(
            "SELECT COUNT(*) c FROM ticket_legs l"
            " JOIN matches m ON m.match_id = l.match_id"
            " WHERE l.result = 'deschis' AND m.home_goals IS NOT NULL"
        ).fetchone()["c"]
    assert remaining == 0


# ----------------------------------------------------------------------
# Ziua pentru care se genereaza bilete
#
# Ziua de azi apare in "urmatoarele zile cu meciuri" chiar daca meciurile ei
# s-au jucat deja sau sunt in desfasurare. Construind pe ele am "prezice"
# rezultate cunoscute, sau am da un pont pe un meci intrat in minutul 65.
# ----------------------------------------------------------------------
def _pick_day(db, now):
    """Aceeasi regula ca in refresh._generate_next_tickets."""
    from predictiescor.tickets import matches_on, next_match_days
    for candidate in next_match_days(db, 5):
        upcoming = matches_on(db, candidate, include_finished=False)
        if not upcoming.empty:
            upcoming = upcoming[upcoming["utc_date"] > now]
        if len(upcoming) >= 1:
            return candidate, upcoming
    return None, None


def test_a_day_whose_matches_all_finished_is_skipped(tmp_path):
    from predictiescor.storage.db import Database
    db = Database(tmp_path / "zi.db")
    db.upsert_match(season=2026, utc_date="2026-09-05T12:00:00+00:00",
                    home="FCSB", away="UTA Arad", home_goals=2, away_goals=0,
                    status="finished")
    db.upsert_match(season=2026, utc_date="2026-09-06T17:00:00+00:00",
                    home="CFR Cluj", away="Rapid București", status="scheduled")

    day, matches = _pick_day(db, "2026-09-05T15:00:00+00:00")
    assert day == "2026-09-06"
    assert len(matches) == 1


def test_a_match_already_under_way_is_not_offered(tmp_path):
    """Meciul a inceput acum 40 de minute: nu mai e obiect de predictie."""
    from predictiescor.storage.db import Database
    db = Database(tmp_path / "live.db")
    db.upsert_match(season=2026, utc_date="2026-09-05T14:30:00+00:00",
                    home="UTA Arad", away="Csikszereda", status="scheduled")
    db.upsert_match(season=2026, utc_date="2026-09-05T17:30:00+00:00",
                    home="Dinamo București", away="FCSB", status="scheduled")

    day, matches = _pick_day(db, "2026-09-05T15:10:00+00:00")
    assert day == "2026-09-05"
    # numele ajunge in baza in forma canonica, fara diacritice
    assert list(matches["home_team"]) == ["Dinamo Bucuresti"]


def test_nothing_scheduled_gives_no_day(tmp_path):
    from predictiescor.storage.db import Database
    db = Database(tmp_path / "gol.db")
    db.upsert_match(season=2026, utc_date="2026-09-05T12:00:00+00:00",
                    home="FCSB", away="UTA Arad", home_goals=1, away_goals=1,
                    status="finished")
    assert _pick_day(db, "2026-09-05T15:00:00+00:00")[0] is None
