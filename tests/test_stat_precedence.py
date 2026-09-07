"""Cine are ultimul cuvânt pe cornere și cartonașe.

LPF e sursa oficială a ligii și rămâne sursa pentru restul statisticilor, dar
pe cele două piețe pe care se pariază s-a dovedit nesigură: la Dinamo — FCSB
publică 2-2 cornere la un meci cu 32 de șuturi, unde Flashscore dă 14.

Precedența **nu** trebuie să depindă de ordinea pașilor din actualizare — „cine
scrie ultimul câștigă" s-ar strica tăcut la prima reordonare. De asta LPF scrie
coloanele astea cu `fill_only`: le pune doar dacă nu le are nimeni.
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from predictiescor.data.flashscore import (
    INGESTED_STATS, STATS_RECHECK_DAYS, FlashscoreProvider,
)
from predictiescor.data.lpf import DEFER_TO_FLASHSCORE
from predictiescor.storage.db import Database


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "precedenta.db")


def _match(db, *, zile_in_urma=1):
    cand = datetime.now(timezone.utc) - timedelta(days=zile_in_urma)
    return db.upsert_match(
        season=2026, utc_date=cand.isoformat(),
        home="Dinamo Bucuresti", away="FCSB",
        home_goals=2, away_goals=1, status="finished")


def _write(db, match_id, source, values, *, fill_only=()):
    team_id = db.upsert_team("Dinamo Bucuresti")
    db.upsert_team_stats(match_id, team_id, True, values,
                         source=source, fill_only=fill_only)
    return team_id


def _read(db, match_id, column):
    with db.connect() as conn:
        row = conn.execute(
            f"SELECT {column} FROM match_team_stats WHERE match_id=?",
            (match_id,)).fetchone()
    return row[column]


# ----------------------------------------------------------------------
# Precedența
# ----------------------------------------------------------------------
def test_flashscore_overwrites_lpf(db):
    """Ordinea firească: LPF scrie întâi, Flashscore corectează."""
    match_id = _match(db)
    _write(db, match_id, "lpf", {"corners": 2.0}, fill_only=DEFER_TO_FLASHSCORE)
    _write(db, match_id, "flashscore", {"corners": 7.0})
    assert _read(db, match_id, "corners") == 7.0


def test_lpf_does_not_overwrite_flashscore(db):
    """Ordinea inversă trebuie să dea același rezultat.

    Ăsta e tot rostul: dacă pașii se reordonează în `refresh.py`, precedența
    rămâne aceeași.
    """
    match_id = _match(db)
    _write(db, match_id, "flashscore", {"corners": 7.0})
    _write(db, match_id, "lpf", {"corners": 2.0}, fill_only=DEFER_TO_FLASHSCORE)
    assert _read(db, match_id, "corners") == 7.0


def test_lpf_still_fills_a_gap(db):
    """Când Flashscore n-are nimic, valoarea LPF intră."""
    match_id = _match(db)
    _write(db, match_id, "lpf", {"corners": 2.0}, fill_only=DEFER_TO_FLASHSCORE)
    assert _read(db, match_id, "corners") == 2.0


def test_the_rule_applies_to_cards_too(db):
    match_id = _match(db)
    _write(db, match_id, "flashscore", {"yellow_cards": 4.0, "red_cards": 1.0})
    _write(db, match_id, "lpf", {"yellow_cards": 3.0, "red_cards": 0.0},
           fill_only=DEFER_TO_FLASHSCORE)
    assert _read(db, match_id, "yellow_cards") == 4.0
    assert _read(db, match_id, "red_cards") == 1.0


def test_other_statistics_stay_with_lpf(db):
    """Doar cele două piețe trec la Flashscore; restul rămân ale LPF."""
    match_id = _match(db)
    _write(db, match_id, "flashscore", {"shots": 99.0})
    _write(db, match_id, "lpf", {"shots": 20.0}, fill_only=DEFER_TO_FLASHSCORE)
    assert _read(db, match_id, "shots") == 20.0, "LPF are ultimul cuvânt aici"


def test_an_empty_value_never_erases(db):
    """Un provider fără valoare nu șterge ce se știe deja — în ambele sensuri."""
    match_id = _match(db)
    _write(db, match_id, "flashscore", {"corners": 7.0})
    _write(db, match_id, "flashscore", {"corners": None})
    assert _read(db, match_id, "corners") == 7.0


def test_the_two_lists_agree():
    """Ce cedează LPF trebuie să fie exact ce aduce Flashscore."""
    assert set(DEFER_TO_FLASHSCORE) == set(INGESTED_STATS)


# ----------------------------------------------------------------------
# Ce meciuri se cer
# ----------------------------------------------------------------------
def _with_ext(db, match_id, ext_id="abc123"):
    with db.connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO match_external_ids(match_id, source, ext_id)"
            " VALUES (?,'flashscore',?)", (match_id, ext_id))


def _todo(db, full=False):
    return FlashscoreProvider(db=db)._stats_todo(2026, full)


def test_a_recent_match_is_asked_for(db):
    match_id = _match(db, zile_in_urma=1)
    _with_ext(db, match_id)
    _write(db, match_id, "flashscore", {"corners": 7.0})
    assert len(_todo(db)) == 1


def test_an_old_match_with_corners_is_left_alone(db):
    """O cerere per meci — lista trebuie să rămână scurtă."""
    match_id = _match(db, zile_in_urma=STATS_RECHECK_DAYS + 3)
    _with_ext(db, match_id)
    _write(db, match_id, "flashscore", {"corners": 7.0})
    assert _todo(db) == []


def test_an_old_match_without_corners_is_asked_for(db):
    """Golurile din date se umplu oricât de vechi ar fi."""
    match_id = _match(db, zile_in_urma=STATS_RECHECK_DAYS + 3)
    _with_ext(db, match_id)
    _write(db, match_id, "lpf", {"shots": 20.0})
    assert len(_todo(db)) == 1


def test_full_asks_for_everything(db):
    match_id = _match(db, zile_in_urma=90)
    _with_ext(db, match_id)
    _write(db, match_id, "flashscore", {"corners": 7.0})
    assert len(_todo(db, full=True)) == 1


def test_a_match_without_a_flashscore_id_is_skipped(db):
    """Fără id nu avem ce cere."""
    match_id = _match(db, zile_in_urma=1)
    _write(db, match_id, "lpf", {"corners": 2.0})
    assert _todo(db, full=True) == []


def test_an_unplayed_match_is_skipped(db):
    match_id = db.upsert_match(
        season=2026, utc_date="2026-12-01T18:00:00+00:00",
        home="Dinamo Bucuresti", away="FCSB", status="scheduled")
    _with_ext(db, match_id)
    assert _todo(db, full=True) == []
