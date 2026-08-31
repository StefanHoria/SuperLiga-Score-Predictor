"""Fotografiile de absente: fara ele, absentele nu vor fi masurabile niciodata.

Transfermarkt publica doar starea de ACUM. Ca sa putem afla vreodata daca
absenta unui golgheter conteaza, trebuie sa stim cine lipsea la meciurile deja
jucate - iar asta se poate doar daca am inregistrat la timp.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from predictiescor.storage.db import Database


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "avail.db")


def _absent(db, team, players, kind="accidentare"):
    team_id = db.upsert_team(team, source="test")
    rows = [{"player_id": db.upsert_player(name, team_id, source="test"),
             "kind": kind, "reason": "test"} for name in players]
    db.replace_availability(team_id, rows, source="test")
    return team_id


# ----------------------------------------------------------------------
def test_snapshot_records_who_is_out(db):
    _absent(db, "FCSB", ["Ion Pop", "Vasile Ene"])
    assert db.snapshot_availability(taken_at="2026-08-01T00:00:00+00:00") == 2

    out = db.availability_on("2026-08-05T00:00:00+00:00")
    assert sorted(out["player"]) == ["Ion Pop", "Vasile Ene"]
    assert set(out["team"]) == {"FCSB"}


def test_a_later_snapshot_does_not_rewrite_an_earlier_one(db):
    """Miezul problemei: fotografia veche trebuie sa ramana asa cum era."""
    team = _absent(db, "CFR Cluj", ["Ion Pop"])
    db.snapshot_availability(taken_at="2026-08-01T00:00:00+00:00")

    # jucatorul s-a insanatosit, altul s-a accidentat
    db.replace_availability(
        team, [{"player_id": db.upsert_player("Radu Marin", team, source="test"),
                "kind": "accidentare"}], source="test")
    db.snapshot_availability(taken_at="2026-09-01T00:00:00+00:00")

    august = db.availability_on("2026-08-15T00:00:00+00:00")
    septembrie = db.availability_on("2026-09-15T00:00:00+00:00")
    assert list(august["player"]) == ["Ion Pop"]
    assert list(septembrie["player"]) == ["Radu Marin"]


def test_the_nearest_earlier_snapshot_is_used(db):
    _absent(db, "Rapid București", ["Ion Pop"])
    db.snapshot_availability(taken_at="2026-08-01T00:00:00+00:00")
    db.snapshot_availability(taken_at="2026-08-10T00:00:00+00:00")

    out = db.availability_on("2026-08-09T00:00:00+00:00")
    assert list(out["taken_at"]) == ["2026-08-01T00:00:00+00:00"]


def test_nothing_is_known_before_the_first_snapshot(db):
    _absent(db, "Dinamo București", ["Ion Pop"])
    db.snapshot_availability(taken_at="2026-08-01T00:00:00+00:00")

    assert db.availability_on("2026-07-01T00:00:00+00:00").empty
    assert db.had_availability_snapshot("2026-07-01T00:00:00+00:00") is False
    assert db.had_availability_snapshot("2026-08-02T00:00:00+00:00") is True


def test_an_empty_snapshot_still_counts_as_having_looked(db):
    """Deosebirea care conteaza: 'nimeni absent' nu e 'nu ne-am uitat'."""
    assert db.snapshot_availability(taken_at="2026-08-01T00:00:00+00:00") == 0

    assert db.availability_on("2026-08-05T00:00:00+00:00").empty
    assert db.had_availability_snapshot("2026-08-05T00:00:00+00:00") is True


def test_history_counts_players_and_teams(db):
    _absent(db, "FCSB", ["Ion Pop", "Vasile Ene"])
    _absent(db, "UTA Arad", ["Marin Radu"])
    db.snapshot_availability(taken_at="2026-08-01T00:00:00+00:00")

    history = db.availability_history()
    assert len(history) == 1
    assert int(history.iloc[0]["players"]) == 3
    assert int(history.iloc[0]["teams"]) == 2


def test_suspensions_are_kept_apart_from_injuries(db):
    _absent(db, "FC Botoșani", ["Ion Pop"], kind="suspendare")
    _absent(db, "Farul Constanța", ["Radu Marin"], kind="accidentare")
    db.snapshot_availability(taken_at="2026-08-01T00:00:00+00:00")

    out = db.availability_on("2026-08-02T00:00:00+00:00")
    assert dict(zip(out["player"], out["kind"])) == {
        "Ion Pop": "suspendare", "Radu Marin": "accidentare"}
