"""Meciurile reprogramate își primesc data nouă.

Corecția orelor accepta cel mult 36 de ore de diferență, ca să nu confunde
turul cu returul. O mutare cu două zile trecea neobservată: în septembrie 2026
opt meciuri aveau în bază data planificată inițial — FCSB — Petrolul pe 12 în
loc de 14, iar Rapid — Argeș și Dinamo — Farul își schimbaseră zilele între ele.

Pentru meciuri deja jucate greșeala e cosmetică. Pentru cele viitoare nu: o
dată greșită mută meciul în ziua altor bilete, iar garda meciurilor începute
l-ar scoate din calcul cu două zile înainte să se joace.
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from predictiescor.data.flashscore import (
    RESCHEDULE_LIMIT_SECONDS, FlashscoreProvider, LiveMatch,
)
from predictiescor.storage.db import Database

PLANIFICAT = datetime(2026, 9, 12, 17, 0, tzinfo=timezone.utc)
JUCAT = datetime(2026, 9, 14, 18, 0, tzinfo=timezone.utc)


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "reprogramari.db")


def _fs(home, away, kickoff):
    return LiveMatch(external_id=f"{home[:3]}{away[:3]}{kickoff:%d}",
                     kickoff=kickoff, home=home, away=away,
                     home_goals=None, away_goals=None, ht_home_goals=None,
                     ht_away_goals=None, state="programat", minute=None,
                     round=None)


def _provider(db, monkeypatch, feed):
    provider = FlashscoreProvider(db=db)
    monkeypatch.setattr(provider, "fetch_feed", lambda **_: feed)
    return provider


def _stored(db, match_id):
    with db.connect() as conn:
        value = conn.execute("SELECT utc_date FROM matches WHERE match_id=?",
                             (match_id,)).fetchone()["utc_date"]
    return datetime.fromisoformat(value)


def _match(db, when, home="FCSB", away="Petrolul Ploiesti"):
    return db.upsert_match(season=db_season(), utc_date=when.isoformat(),
                           home=home, away=away, status="scheduled")


def db_season():
    from predictiescor.config import get_config
    return get_config().current_season


# ----------------------------------------------------------------------
def test_a_two_day_reschedule_is_picked_up(db, monkeypatch):
    """Cazul FCSB — Petrolul: planificat pe 12, jucat pe 14."""
    match_id = _match(db, PLANIFICAT)
    provider = _provider(db, monkeypatch,
                         [_fs("FCSB", "Petrolul Ploiesti", JUCAT)])
    assert provider.correct_kickoff_times() == 1
    assert _stored(db, match_id) == JUCAT


def test_two_matches_that_swapped_days(db, monkeypatch):
    """Rapid — Argeș și Dinamo — Farul și-au schimbat zilele între ele."""
    vineri = datetime(2026, 9, 18, 18, 0, tzinfo=timezone.utc)
    duminica = datetime(2026, 9, 20, 17, 30, tzinfo=timezone.utc)
    rapid = _match(db, duminica, "Rapid Bucuresti", "FC Arges")
    dinamo = _match(db, vineri, "Dinamo Bucuresti", "Farul Constanta")
    provider = _provider(db, monkeypatch, [
        _fs("Rapid Bucuresti", "FC Arges", vineri),
        _fs("Dinamo Bucuresti", "Farul Constanta", duminica),
    ])
    provider.correct_kickoff_times()
    assert _stored(db, rapid) == vineri
    assert _stored(db, dinamo) == duminica


def test_a_small_drift_is_still_corrected(db, monkeypatch):
    """Cazul vechi, de ore greșite cu două ore, merge ca înainte."""
    match_id = _match(db, JUCAT + timedelta(hours=2))
    provider = _provider(db, monkeypatch,
                         [_fs("FCSB", "Petrolul Ploiesti", JUCAT)])
    provider.correct_kickoff_times()
    assert _stored(db, match_id) == JUCAT


def test_nothing_changes_when_the_date_is_right(db, monkeypatch):
    _match(db, JUCAT)
    provider = _provider(db, monkeypatch,
                         [_fs("FCSB", "Petrolul Ploiesti", JUCAT)])
    assert provider.correct_kickoff_times() == 0


def test_an_ambiguous_pair_keeps_the_cautious_limit(db, monkeypatch):
    """Aceeași pereche de două ori în feed (play-off): nu mutăm peste 36 de ore.

    Aici chiar nu se știe care meci din feed e al nostru.
    """
    match_id = _match(db, PLANIFICAT)
    provider = _provider(db, monkeypatch, [
        _fs("FCSB", "Petrolul Ploiesti", JUCAT),
        _fs("FCSB", "Petrolul Ploiesti", JUCAT + timedelta(days=40)),
    ])
    provider.correct_kickoff_times()
    assert _stored(db, match_id) == PLANIFICAT


def test_an_absurd_move_is_refused_even_for_a_unique_pair(db, monkeypatch):
    """Peste două luni nu e o reprogramare, e o potrivire greșită."""
    match_id = _match(db, PLANIFICAT)
    departe = PLANIFICAT + timedelta(seconds=RESCHEDULE_LIMIT_SECONDS + 3600)
    provider = _provider(db, monkeypatch,
                         [_fs("FCSB", "Petrolul Ploiesti", departe)])
    provider.correct_kickoff_times()
    assert _stored(db, match_id) == PLANIFICAT


def test_the_return_leg_is_not_confused_with_the_first(db, monkeypatch):
    """Returul are gazda și oaspetele inversate, deci e altă pereche."""
    tur = _match(db, PLANIFICAT, "FCSB", "Petrolul Ploiesti")
    provider = _provider(db, monkeypatch,
                         [_fs("Petrolul Ploiesti", "FCSB", JUCAT)])
    provider.correct_kickoff_times()
    assert _stored(db, tur) == PLANIFICAT
