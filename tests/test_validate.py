"""O valoare imposibilă nu se scrie ca zero, se scrie ca „nu știu".

Verificarea stă la scriere, în `upsert_team_stats`, nu în provideri. Fiecare
sursă are alt fel de a greși — LPF scria zero când pagina nu era completată,
API-Football a scris posesie 0/0 la un meci din 2024 — iar o gardă pusă în
fiecare provider s-ar uita la următorul.

Diferența dintre `0` și `NULL` contează peste tot mai departe: modelul sare
peste ce nu știe, decontarea anulează pontul în loc să-l piardă, iar când sursa
completează mai târziu, valoarea adevărată intră peste `NULL` fără să se bată
cu un zero inventat.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from predictiescor.data.validate import (
    NEVER_ZERO, RANGES, clean_team_stats, implausible_rows, missing_columns,
)
from predictiescor.storage.db import Database


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "validare.db")


def _match(db, season=2026):
    return db.upsert_match(
        season=season, utc_date="2026-09-01T18:00:00+00:00",
        home="FCSB", away="UTA Arad",
        home_goals=1, away_goals=0, status="finished")


def _read(db, column):
    with db.connect() as conn:
        row = conn.execute(
            f"SELECT {column} FROM match_team_stats").fetchone()
    return row[column]


# ----------------------------------------------------------------------
# Regulile
# ----------------------------------------------------------------------
def test_a_normal_value_passes():
    clean, rejected = clean_team_stats({"corners": 7})
    assert clean["corners"] == 7
    assert rejected == []


def test_zero_passes_is_not_zero_it_is_unknown():
    """Nicio echipă nu termină un meci cu zero pase."""
    clean, rejected = clean_team_stats({"passes": 0})
    assert clean["passes"] is None
    assert rejected == ["passes"]


def test_zero_possession_is_unknown_too():
    """Cazul real: API-Football, Metaloglobus — Poli Iași, 2024."""
    clean, _ = clean_team_stats({"possession": 0})
    assert clean["possession"] is None


def test_zero_corners_is_allowed():
    """Rar, dar posibil — și de aceea nu e în lista celor care nu pot fi zero."""
    clean, rejected = clean_team_stats({"corners": 0})
    assert clean["corners"] == 0
    assert rejected == []
    assert "corners" not in NEVER_ZERO


def test_an_absurd_value_is_rejected():
    clean, _ = clean_team_stats({"yellow_cards": 99})
    assert clean["yellow_cards"] is None


def test_a_negative_value_is_rejected():
    clean, _ = clean_team_stats({"shots": -3})
    assert clean["shots"] is None


def test_possession_above_a_hundred_is_rejected():
    clean, _ = clean_team_stats({"possession": 140})
    assert clean["possession"] is None


def test_a_missing_value_stays_missing_not_zero():
    clean, rejected = clean_team_stats({"corners": None})
    assert clean["corners"] is None
    assert rejected == [], "lipsa nu e o eroare de raportat"


def test_text_that_is_not_a_number_is_rejected():
    clean, _ = clean_team_stats({"corners": "nu se știe"})
    assert clean["corners"] is None


def test_unknown_columns_are_left_alone():
    """Nu inventăm reguli pentru ce nu cunoaștem."""
    clean, rejected = clean_team_stats({"ceva_nou": 5000})
    assert clean["ceva_nou"] == 5000
    assert rejected == []


@pytest.mark.parametrize("column", ["corners", "yellow_cards", "shots"])
def test_the_upper_limits_leave_room_for_a_real_match(column):
    """Marginile prind erori de format, nu meciuri neobișnuite.

    Recordurile reale trebuie să treacă: 21 de cornere într-un meci, 9
    cartonașe galbene la o echipă.
    """
    limite = RANGES[column]
    assert limite[1] >= {"corners": 21, "yellow_cards": 9, "shots": 35}[column]


# ----------------------------------------------------------------------
# La scriere
# ----------------------------------------------------------------------
def test_the_database_never_stores_an_impossible_value(db):
    """Garda e la `upsert_team_stats`, deci nicio sursă nu o poate ocoli."""
    match_id = _match(db)
    db.upsert_team_stats(match_id, db.upsert_team("FCSB"), True,
                         {"corners": 5, "passes": 0, "yellow_cards": 99},
                         source="sursa-neglijenta")
    assert _read(db, "corners") == 5.0
    assert _read(db, "passes") is None
    assert _read(db, "yellow_cards") is None


def test_a_rejected_value_does_not_erase_a_good_one(db):
    """`COALESCE` lucrează pe valoarea curățată, deci un zero fals nu strică."""
    match_id = _match(db)
    team_id = db.upsert_team("FCSB")
    db.upsert_team_stats(match_id, team_id, True, {"passes": 450}, source="a")
    db.upsert_team_stats(match_id, team_id, True, {"passes": 0}, source="b")
    assert _read(db, "passes") == 450.0


# ----------------------------------------------------------------------
# Rapoartele
# ----------------------------------------------------------------------
def test_implausible_rows_finds_what_slipped_in_before(db):
    """Ce a intrat înainte ca verificarea să existe se poate număra."""
    match_id = _match(db)
    team_id = db.upsert_team("FCSB")
    db.upsert_team_stats(match_id, team_id, True, {"corners": 5}, source="a")
    with db.connect() as conn:
        conn.execute("UPDATE match_team_stats SET possession = 0")

    gasite = implausible_rows(db)
    assert len(gasite) == 1
    assert gasite[0]["campuri"] == ["possession"]


def test_a_clean_database_reports_nothing(db):
    match_id = _match(db)
    db.upsert_team_stats(match_id, db.upsert_team("FCSB"), True,
                         {"corners": 5, "passes": 450}, source="a")
    assert implausible_rows(db) == []


def test_missing_columns_counts_the_gaps(db):
    """Golurile din acoperire nu sunt erori, dar sunt limita reală a modelului."""
    _match(db)
    assert missing_columns(db, ("corners",))["corners"] == 1


def test_a_filled_column_is_not_counted_as_missing(db):
    match_id = _match(db)
    db.upsert_team_stats(match_id, db.upsert_team("FCSB"), True,
                         {"corners": 5}, source="a")
    assert missing_columns(db, ("corners",))["corners"] == 0
