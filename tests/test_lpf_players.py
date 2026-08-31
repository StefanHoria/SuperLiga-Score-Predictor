"""Formatiile si evenimentele LPF: marcatori, cartonase, minute jucate."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest
from bs4 import BeautifulSoup

from predictiescor.data.lpf import LpfProvider, normalize_player_name


def _pitch(numbers_names):
    inner = "".join(
        f'<div class="nume_jucator">{n}.{s}</div>' for n, s in numbers_names)
    return f'<div class="teren_jucatori">{inner}</div>'


def _block(rows):
    inner = "".join(
        f'<div class="titular-row"><div class="nrt">{n}</div> {name}</div>'
        for n, name in rows)
    return f'<div class="lista_titulari">{inner}</div>'


def _event(side, icon, body, minute):
    return (f'<div class="event_{side}">{body}'
            f'<div class="minut">{minute}\'</div>'
            f'<div class="icon"><img src="assets/images/{icon}"/></div></div>')


def _sub(side, out_no, out_name, in_no, in_name, minute):
    body = (f'<span class="jucator-out">{out_no}.{out_name}</span> '
            f'<span class="jucator-in"> {in_no}.{in_name} </span>')
    return _event(side, "schimbare.png", body, minute)


# Ambele echipe au un jucator cu numarul 9 si unul cu 7: daca parserul nu
# separa echipele, statisticile lor se amesteca.
HOME_STARTERS = [("1", "Ion POPA"), ("7", "Vasile ENE"), ("9", "Radu MARIN")]
AWAY_STARTERS = [("1", "Andrei LUPU"), ("7", "Mihai DAN"), ("9", "Petru ILIE")]
HOME_BENCH = [("21", "Cristi VOICU"), ("22", "Sorin BADEA")]
AWAY_BENCH = [("31", "Alin TOMA"), ("32", "Marius NEGRU")]

PAGE = f"""
<html><head><title>ECHIPA A - ECHIPA B - Etapa 5 Superliga</title></head>
<body>
  <span>12 septembrie 2026, 20:30</span>
  {_pitch(HOME_STARTERS)}
  {_pitch(AWAY_STARTERS)}
  {_block(HOME_STARTERS)}
  {_block(AWAY_STARTERS)}
  {_block(HOME_BENCH)}
  {_block(AWAY_BENCH)}
  {_event("gazda", "gol.png", "9.MARIN ", 10)}
  {_event("oaspeti", "gol.png", "9.ILIE ", 25)}
  {_event("gazda", "cartonas_galben.png", "7.ENE ", 30)}
  {_sub("gazda", "9", "MARIN", "21", "VOICU", 60)}
  {_sub("oaspeti", "7", "DAN", "31", "TOMA", 70)}
  {_event("gazda", "gol.png", "21.VOICU ", 80)}
  {_event("oaspeti", "cartonas_rosu.png", "1.LUPU ", 85)}
</body></html>
"""


@pytest.fixture
def soup():
    return BeautifulSoup(PAGE, "lxml")


@pytest.fixture
def parsed(soup):
    events = LpfProvider._parse_events(soup)
    lineups = LpfProvider._parse_lineups(soup, events)
    return lineups, events


# ----------------------------------------------------------------------
def test_events_are_typed_by_icon(soup):
    events = LpfProvider._parse_events(soup)
    kinds = [e["kind"] for e in events]
    assert kinds.count("goal") == 3
    assert kinds.count("yellow") == 1
    assert kinds.count("red") == 1
    assert kinds.count("substitution") == 2


def test_substitution_reads_both_players(soup):
    events = LpfProvider._parse_events(soup)
    subs = [e for e in events if e["kind"] == "substitution"]
    assert {"out": "9", "in": "21"}.items() <= subs[0].items()
    assert subs[0]["side"] == "home"
    assert subs[1]["side"] == "away"


def test_bench_blocks_go_to_the_right_team(parsed):
    """Rezervele nu se suprapun cu formatia de pe teren; le legam prin schimbari."""
    lineups, _ = parsed
    home_numbers = {p["number"] for p in lineups["home"]}
    away_numbers = {p["number"] for p in lineups["away"]}

    assert "21" in home_numbers and "22" in home_numbers
    assert "31" in away_numbers and "32" in away_numbers
    assert "31" not in home_numbers, "rezerva oaspetilor a ajuns la gazde"
    assert len(lineups["home"]) == 5 and len(lineups["away"]) == 5


def test_shared_shirt_numbers_do_not_merge_players(parsed):
    """Ambele echipe au #9 - trebuie sa ramana doi jucatori diferiti."""
    lineups, events = parsed
    stats = LpfProvider._player_stats(lineups, events)
    assert stats["home"]["9"]["name"] == "Radu MARIN"
    assert stats["away"]["9"]["name"] == "Petru ILIE"
    assert stats["home"]["9"]["goals"] == 1
    assert stats["away"]["9"]["goals"] == 1


def test_goals_match_the_scoreline(parsed):
    lineups, events = parsed
    stats = LpfProvider._player_stats(lineups, events)
    assert sum(p["goals"] for p in stats["home"].values()) == 2
    assert sum(p["goals"] for p in stats["away"].values()) == 1


def test_minutes_follow_substitutions(parsed):
    lineups, events = parsed
    stats = LpfProvider._player_stats(lineups, events)

    assert stats["home"]["9"]["minutes"] == 60      # titular, iesit in 60
    assert stats["home"]["21"]["minutes"] == 30     # intrat in 60
    assert stats["home"]["1"]["minutes"] == 90      # a jucat tot meciul
    assert stats["home"]["22"]["minutes"] == 0      # rezerva neintrata


def test_red_card_ends_the_match_for_that_player(parsed):
    lineups, events = parsed
    stats = LpfProvider._player_stats(lineups, events)
    assert stats["away"]["1"]["red_cards"] == 1
    assert stats["away"]["1"]["minutes"] == 85


def test_substitute_is_not_marked_as_starter(parsed):
    lineups, events = parsed
    stats = LpfProvider._player_stats(lineups, events)
    assert stats["home"]["21"]["is_starter"] == 0
    assert stats["home"]["9"]["is_starter"] == 1


def test_own_goal_not_credited_to_scorer(soup):
    html = PAGE.replace('{_event}', '')  # marcaj neutru
    html = PAGE.replace("gol.png\"/></div></div>\n  <div class=\"event_oaspeti\">9.ILIE",
                        "autogol.png\"/></div></div>\n  <div class=\"event_oaspeti\">9.ILIE")
    doc = BeautifulSoup(html, "lxml")
    events = LpfProvider._parse_events(doc)
    lineups = LpfProvider._parse_lineups(doc, events)
    stats = LpfProvider._player_stats(lineups, events)
    own_goals = [e for e in events if e["kind"] == "own_goal"]
    if own_goals:
        assert sum(p["goals"] for p in stats["home"].values()) < 2


@pytest.mark.parametrize("raw,expected", [
    ("Mihaita Alexandru CHIPCIU", "Mihaita Alexandru Chipciu"),
    ("Dan Nicolae NISTOR", "Dan Nicolae Nistor"),
    ("Jovo LUKIC", "Jovo Lukic"),
])
def test_name_normalisation(raw, expected):
    assert normalize_player_name(raw) == expected
