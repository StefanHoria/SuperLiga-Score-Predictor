"""Supraveghetorul: verifica biletele exact cand s-a incheiat un meci."""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from predictiescor.storage.db import Database
from predictiescor.tickets.watcher import (
    MATCH_DURATION, due_matches, next_check_at,
)

NOW = datetime(2026, 8, 30, 18, 0, tzinfo=timezone.utc)


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "watch.db")


def _match(db, hour, *, home="FCSB", away="UTA Arad", goals=None):
    kickoff = NOW.replace(hour=hour, minute=0)
    return db.upsert_match(
        season=2026, utc_date=kickoff.isoformat(), home=home, away=away,
        home_goals=None if goals is None else goals[0],
        away_goals=None if goals is None else goals[1],
        status="finished" if goals else "scheduled")


def _leg(db, match_id, result="deschis"):
    with db.connect() as conn:
        cur = conn.execute(
            "INSERT INTO tickets(created_at, match_day, strategy, label,"
            " stake, legs_total, status) VALUES ('x','2026-08-30','t','T',"
            " 10, 1, 'deschis')")
        ticket_id = cur.lastrowid
        conn.execute(
            "INSERT INTO ticket_legs(ticket_id, match_id, market, selection,"
            " description, probability, fair_odds, result)"
            " VALUES (?,?,'1X2','gazda','x',0.5,2.0,?)",
            (ticket_id, match_id, result))
    return ticket_id


# ----------------------------------------------------------------------
def test_match_without_result_is_due(db):
    _match(db, 15)  # a inceput acum 3 ore, fara rezultat
    due = due_matches(db, now=NOW)
    assert len(due) == 1
    assert due[0]["has_result"] is False


def test_match_still_in_progress_is_not_due(db):
    _match(db, 17)  # a inceput acum o ora, inca se joaca
    assert due_matches(db, now=NOW) == []


def test_finished_and_settled_match_is_not_due(db):
    """Nu reverificam un meci al carui pont a fost deja decontat."""
    match_id = _match(db, 15, goals=(2, 1))
    _leg(db, match_id, result="castigat")
    assert due_matches(db, now=NOW) == []


def test_finished_match_with_open_leg_is_due(db):
    """Are rezultat, dar pontul n-a fost decontat: trebuie verificat."""
    match_id = _match(db, 15, goals=(2, 1))
    _leg(db, match_id, result="deschis")

    due = due_matches(db, now=NOW)
    assert len(due) == 1
    assert due[0]["has_result"] is True
    assert due[0]["open_legs"] == 1


def test_open_ticket_on_another_match_does_not_make_this_one_due(db):
    """Biletul e deschis din cauza meciului de seara, nu a celui de dupa-amiaza."""
    played = _match(db, 15, goals=(0, 0), home="Farul Constanta",
                    away="FC Botosani")
    later = _match(db, 20)

    with db.connect() as conn:
        cur = conn.execute(
            "INSERT INTO tickets(created_at, match_day, strategy, label,"
            " stake, legs_total, status) VALUES ('x','2026-08-30','t','T',"
            " 10, 2, 'deschis')")
        ticket_id = cur.lastrowid
        for match_id, result in ((played, "castigat"), (later, "deschis")):
            conn.execute(
                "INSERT INTO ticket_legs(ticket_id, match_id, market,"
                " selection, description, probability, fair_odds, result)"
                " VALUES (?,?,'1X2','gazda','x',0.5,2.0,?)",
                (ticket_id, match_id, result))

    due = due_matches(db, now=NOW)
    labels = [m["label"] for m in due]
    assert "Farul Constanta - FC Botosani" not in labels
    assert due == [] or "FCSB - UTA Arad" in labels


def test_very_old_match_without_result_is_abandoned(db):
    """Un meci de acum doua saptamani fara rezultat nu se mai reinterogheaza."""
    old = NOW - timedelta(days=14)
    db.upsert_match(season=2026, utc_date=old.isoformat(),
                    home="CFR Cluj", away="Rapid Bucuresti", status="scheduled")
    assert due_matches(db, now=NOW) == []


def test_next_check_is_after_the_next_kickoff(db):
    _match(db, 20)
    expected = NOW.replace(hour=20, minute=0) + MATCH_DURATION
    assert next_check_at(db, now=NOW) == expected


def test_no_next_check_when_nothing_is_scheduled(db):
    _match(db, 15, goals=(1, 0))
    assert next_check_at(db, now=NOW) is None


# ----------------------------------------------------------------------
# Calea rapida: Flashscore intai, refresh complet doar daca mai lipseste ceva
# ----------------------------------------------------------------------
def _spy_on_full_refresh(monkeypatch):
    """Numara de cate ori s-a chemat refresh-ul complet (cel scump)."""
    calls = []

    def fake_refresh(*args, **kwargs):
        from predictiescor.data.refresh import RefreshReport
        calls.append(kwargs)
        return RefreshReport()

    monkeypatch.setattr("predictiescor.data.refresh.run_refresh", fake_refresh)
    return calls


def test_flashscore_alone_avoids_the_expensive_refresh(db, monkeypatch):
    from predictiescor.tickets import watcher

    match_id = _match(db, 15)
    calls = _spy_on_full_refresh(monkeypatch)

    def fake_flashscore(database, cfg):
        with database.connect() as conn:
            conn.execute("UPDATE matches SET home_goals=2, away_goals=1,"
                         " status='finished' WHERE match_id=?", (match_id,))
        return 1

    monkeypatch.setattr(watcher, "_flashscore_pass", fake_flashscore)

    result = watcher.run_cycle(db, now=NOW)
    assert result.refreshed is True
    assert result.newly_finished == 1
    assert calls == [], "refresh-ul complet nu trebuia sa mai fie necesar"


def test_full_refresh_still_runs_when_flashscore_misses_the_match(db, monkeypatch):
    from predictiescor.tickets import watcher

    _match(db, 15)
    calls = _spy_on_full_refresh(monkeypatch)
    monkeypatch.setattr(watcher, "_flashscore_pass", lambda database, cfg: 0)

    watcher.run_cycle(db, now=NOW)
    assert len(calls) == 1


def test_a_broken_flashscore_does_not_stop_the_cycle(db, monkeypatch):
    """Daca sursa live cade, verificarea trebuie sa continue pe drumul lung."""
    from predictiescor.tickets import watcher

    _match(db, 15)
    calls = _spy_on_full_refresh(monkeypatch)

    def explode(database, cfg):
        raise RuntimeError("feed indisponibil")

    monkeypatch.setattr(watcher, "_flashscore_pass", explode)

    result = watcher.run_cycle(db, now=NOW)
    assert len(calls) == 1
    assert result.settled == 0


def test_no_source_is_queried_when_every_result_is_already_in(db, monkeypatch):
    """Meci cu scor, dar cu pont deschis: doar decontam, fara retea."""
    from predictiescor.tickets import watcher

    match_id = _match(db, 15, goals=(2, 1))
    _leg(db, match_id)
    calls = _spy_on_full_refresh(monkeypatch)

    def forbidden(database, cfg):
        raise AssertionError("nu trebuia interogata nicio sursa")

    monkeypatch.setattr(watcher, "_flashscore_pass", forbidden)

    result = watcher.run_cycle(db, now=NOW)
    assert calls == []
    assert result.refreshed is False
    assert result.settled == 1


def test_without_results_lists_only_the_ones_still_missing(db):
    from predictiescor.tickets.watcher import _without_results

    missing = _match(db, 15)
    complete = _match(db, 16, home="CFR Cluj", away="Rapid București",
                      goals=(1, 1))
    assert _without_results(db, [missing, complete]) == [missing]
    assert _without_results(db, []) == []


def test_grace_period_allows_checking_right_after_full_time(db):
    """Un meci inceput acum doua ore trebuie sa fie deja verificabil."""
    kickoff = NOW - timedelta(hours=2)
    db.upsert_match(season=2026, utc_date=kickoff.isoformat(),
                    home="Dinamo București", away="Petrolul Ploiești",
                    status="scheduled")
    assert len(due_matches(db, now=NOW)) == 1
    assert MATCH_DURATION < timedelta(hours=2)
