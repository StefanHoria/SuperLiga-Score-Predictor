"""Feed-ul Flashscore si probabilitatile in timpul meciului."""
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np
import pytest

from predictiescor.data.flashscore import (
    _deduplicate, _live_minute, _parse_fields, parse_feed,
)

# Fragment cu structura reala: doua meciuri, unul incheiat si unul programat,
# plus o repetare (feed-ul listeaza acelasi meci in mai multe sectiuni).
FEED = (
    "~AA÷abc123¬AD÷1788112800¬CX÷FCSB¬AF÷UTA Arad¬AG÷1¬AH÷3"
    "¬BC÷1¬BD÷2¬AB÷3¬AO÷1788119531¬ER÷Runda 7¬"
    "~AA÷def456¬AD÷1788197400¬CX÷Rapid București¬AF÷Univ. Craiova"
    "¬AG÷—¬AH÷—¬AB÷1¬ER÷Runda 7¬"
    "~AA÷abc123¬AD÷1788112800¬CX÷FCSB¬AF÷UTA Arad¬AG÷1¬AH÷3¬AB÷3¬ER÷Runda 7¬"
)

#: Un meci in desfasurare, cu campurile ca in feed-ul real: `AB÷2` e starea
#: live, iar `AO` e momentul in care a pornit ceasul reprizei - nu finalul
#: meciului, cum s-a crezut la inceput. Start 18:00 UTC, ceasul pornit la
#: 18:01:39.
LIVE_FEED = (
    "~AA÷xyz789¬AD÷1788112800¬CX÷Dinamo București¬AF÷FCSB¬AG÷2¬AH÷1"
    "¬AB÷2¬AC÷12¬AO÷1788112899¬ER÷Runda 8¬"
)

#: Acelasi meci in repriza a doua: ceasul a pornit la 18:47.
LIVE_SECOND_HALF = (
    "~AA÷xyz789¬AD÷1788112800¬CX÷Dinamo București¬AF÷FCSB¬AG÷2¬AH÷1"
    "¬AB÷2¬AO÷1788115620¬ER÷Runda 8¬"
)


# ----------------------------------------------------------------------
def _by_id(feed):
    """Ca in utilizarea reala: intai deduplicam, apoi indexam."""
    return {m.external_id: m for m in _deduplicate(parse_feed(feed))}


def test_parses_finished_match():
    game = _by_id(FEED)["abc123"]
    assert game.home == "FCSB" and game.away == "UTA Arad"
    assert (game.home_goals, game.away_goals) == (1, 3)
    assert (game.ht_home_goals, game.ht_away_goals) == (1, 2)
    assert game.is_finished
    assert game.round == "Runda 7"


def test_parses_scheduled_match():
    game = _by_id(FEED)["def456"]
    assert game.state == "programat"
    assert game.home_goals is None and game.away_goals is None
    assert not game.is_live


def test_timestamps_are_utc():
    game = _by_id(FEED)["abc123"]
    assert game.kickoff == datetime(2026, 8, 30, 18, 0, tzinfo=timezone.utc)


def test_detects_live_match():
    """`AB÷2` e starea de meci in desfasurare - confirmata pe feed-ul real."""
    game = parse_feed(LIVE_FEED)[0]
    assert game.is_live
    assert game.score == "2-1"


def test_a_live_match_is_not_read_as_finished():
    """`AO` apare si la meciurile in desfasurare.

    Citit ca semn de meci incheiat, facea meciul in curs sa dispara din pagina
    de Live: Craiova - „U" Cluj se juca, iar aplicatia scria „niciun meci in
    desfasurare".
    """
    assert parse_feed(LIVE_FEED)[0].state == "live"


def test_the_minute_comes_from_the_clock_not_from_the_status_code():
    """`AC` e un cod de stare, nu un minut: arata 12 in minutul 67."""
    fields = _parse_fields(LIVE_FEED.split("~AA÷")[1])
    assert _live_minute(fields, now=1788116859) == 67


def test_the_second_half_adds_forty_five_minutes():
    fields = _parse_fields(LIVE_SECOND_HALF.split("~AA÷")[1])
    assert _live_minute(fields, now=1788116220) == 56


def test_no_clock_means_no_minute():
    """Mai bine fara minut decat cu unul inventat."""
    assert _live_minute({"AD": "1788112800"}) is None


def test_unknown_status_with_score_counts_as_live():
    """Un cod de stare nou nu trebuie sa strice nimic."""
    feed = LIVE_FEED.replace("¬AB÷9¬", "¬AB÷9¬").replace("¬AB÷2¬", "¬AB÷9¬")
    assert parse_feed(feed)[0].is_live


def test_unknown_status_long_after_kickoff_counts_as_finished():
    """La doua ore dupa start, orice meci s-a terminat.

    `AO` e ultima schimbare de stare: la ~2 minute dupa start inseamna ca a
    pornit ceasul, la ~112 minute inseamna fluierul final. Fara distinctia
    asta, un rezultat cu cod necunoscut ar fi citit ca meci in desfasurare si
    nu s-ar mai scrie in baza.
    """
    feed = (LIVE_FEED.replace("¬AB÷2¬", "¬AB÷9¬")
                     .replace("¬AO÷1788112899¬", "¬AO÷1788119520¬"))
    assert parse_feed(feed)[0].is_finished


def test_deduplicates_repeated_matches():
    """Feed-ul listeaza acelasi meci si la rezultate, si la program."""
    assert len(parse_feed(FEED)) == 3
    assert len(_deduplicate(parse_feed(FEED))) == 2


def test_deduplication_keeps_the_richer_row():
    empty = "~AA÷k1¬AD÷1788112800¬CX÷A¬AF÷B¬AB÷1¬"
    scored = "~AA÷k1¬AD÷1788112800¬CX÷A¬AF÷B¬AG÷2¬AH÷0¬AB÷3¬"
    kept = _deduplicate(parse_feed(empty + scored))
    assert len(kept) == 1 and kept[0].home_goals == 2


def test_deduplication_prefers_the_row_with_halftime_score():
    """Ordinea nu conteaza: castiga randul cu mai multe campuri, oricare ar fi."""
    poor = "~AA÷k2¬AD÷1788112800¬CX÷A¬AF÷B¬AG÷1¬AH÷3¬AB÷3¬"
    rich = ("~AA÷k2¬AD÷1788112800¬CX÷A¬AF÷B¬AG÷1¬AH÷3"
            "¬BC÷1¬BD÷2¬AB÷3¬ER÷Runda 7¬")
    for feed in (poor + rich, rich + poor):
        kept = _deduplicate(parse_feed(feed))
        assert len(kept) == 1
        assert kept[0].ht_home_goals == 1, "randul bogat trebuie pastrat"


# ----------------------------------------------------------------------
# Probabilitati in timpul meciului
# ----------------------------------------------------------------------
class _FakeGoals:
    max_goals = 8
    teams = ["A", "B"]
    attack = {"A": 0.0, "B": 0.0}

    def expected_goals(self, home, away):
        return 1.5, 1.0


class _FakePipeline:
    goals = _FakeGoals()
    secondary = None


@pytest.fixture
def pipeline():
    return _FakePipeline()


def _live(home_goals, away_goals, minute):
    from predictiescor.modeling.inplay import InPlayState, live_markets
    state = InPlayState("A", "B", home_goals, away_goals, minute)
    return live_markets(_FakePipeline(), state)["piete"]


def test_lead_becomes_near_certain_at_the_end():
    early = _live(1, 0, 5)["1X2"]["gazda"]
    late = _live(1, 0, 89)["1X2"]["gazda"]
    assert late > 0.95
    assert late > early


def test_a_goal_flips_the_favourite():
    """La 0-1 in minutul 80, gazda nu mai e favorita, oricat de buna ar fi."""
    trailing = _live(0, 1, 80)["1X2"]
    assert trailing["oaspete"] > trailing["gazda"]


def test_draw_grows_when_nobody_scores():
    assert _live(0, 0, 85)["1X2"]["egal"] > _live(0, 0, 10)["1X2"]["egal"]


def test_over_line_already_reached_is_certain():
    """La 3-0, 'peste 2.5' nu mai poate pica."""
    assert _live(3, 0, 50)["total goluri 2.5"]["peste"] == pytest.approx(1.0)


def test_over_line_becomes_impossible_at_full_time():
    at_end = _live(0, 0, 90)["total goluri 2.5"]["peste"]
    assert at_end == pytest.approx(0.0, abs=1e-9)


def test_btts_settled_once_both_scored():
    assert _live(1, 1, 30)["ambele marcheaza"]["da"] == pytest.approx(1.0)


def test_probabilities_always_sum_to_one():
    for minute in (0, 30, 60, 90):
        for score in ((0, 0), (2, 1), (0, 3)):
            book = _live(score[0], score[1], minute)["1X2"]
            assert sum(book.values()) == pytest.approx(1.0, abs=1e-9)


def test_at_kickoff_it_matches_a_full_match():
    """In minutul 0 la 0-0, distributia trebuie sa fie cea a unui meci intreg."""
    from predictiescor.modeling.inplay import remaining_matrix
    full = remaining_matrix(1.5, 1.0, 1.0)
    index = np.arange(full.shape[0])
    assert float((full.sum(axis=1) * index).sum()) == pytest.approx(1.5, abs=0.01)
    assert float((full.sum(axis=0) * index).sum()) == pytest.approx(1.0, abs=0.01)
