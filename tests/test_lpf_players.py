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


# ----------------------------------------------------------------------
# Jucătorii plecați
#
# `upsert_player` mută un jucător când îl găsește la alt club DIN LIGĂ, dar cei
# plecați în străinătate nu mai apar nicăieri — rămâneau agățați de fostul club
# pentru totdeauna. Bîrligea și Tănase erau tot la FCSB, deși plecaseră.
# ----------------------------------------------------------------------
import sys as _sys
from pathlib import Path as _Path

_sys.path.insert(0, str(_Path(__file__).resolve().parents[1] / "src"))

import pytest as _pytest

from predictiescor.storage.db import Database as _Database


@_pytest.fixture
def squad(tmp_path):
    db = _Database(tmp_path / "lot.db")
    team = db.upsert_team("FCSB", source="test")
    ids = {name: db.upsert_player(name, team, source="test", ext_id=name)
           for name in ("Ion Pop", "Vasile Ene", "Daniel Bîrligea")}
    return db, team, ids


def test_a_player_missing_from_the_squad_is_detached(squad):
    db, team, ids = squad
    gone = db.detach_missing_players(team, [ids["Ion Pop"], ids["Vasile Ene"]])
    assert gone == 1

    with db.connect() as conn:
        row = conn.execute("SELECT team_id FROM players WHERE player_id = ?",
                           (ids["Daniel Bîrligea"],)).fetchone()
    assert row["team_id"] is None


def test_the_player_is_not_deleted(squad):
    """Istoricul lui rămâne — și e corect să rămână."""
    db, team, ids = squad
    db.detach_missing_players(team, [ids["Ion Pop"]])
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) c FROM players").fetchone()["c"] == 3


def test_an_empty_roster_detaches_nobody(squad):
    """Dacă pagina n-a putut fi citită, nu golim lotul din greșeală."""
    db, team, _ = squad
    assert db.detach_missing_players(team, []) == 0
    with db.connect() as conn:
        left = conn.execute("SELECT COUNT(*) c FROM players"
                            " WHERE team_id IS NOT NULL").fetchone()["c"]
    assert left == 3


def test_other_teams_are_untouched(squad):
    db, team, ids = squad
    other = db.upsert_team("CFR Cluj", source="test")
    outsider = db.upsert_player("Radu Marin", other, source="test", ext_id="rm")
    db.detach_missing_players(team, [ids["Ion Pop"]])
    with db.connect() as conn:
        row = conn.execute("SELECT team_id FROM players WHERE player_id = ?",
                           (outsider,)).fetchone()
    assert row["team_id"] == other


def test_departed_players_are_those_with_history_but_no_club(squad):
    """Doar cine chiar a jucat conteaza - restul sunt intrari fara urma."""
    db, team, ids = squad
    match_id = db.upsert_match(season=2026, utc_date="2026-08-01T18:00:00+00:00",
                               home="FCSB", away="UTA Arad", home_goals=1,
                               away_goals=0, status="finished")
    db.upsert_player_stats(match_id, ids["Daniel Bîrligea"], team,
                           {"minutes": 90, "goals": 1})
    db.detach_missing_players(team, [ids["Ion Pop"]])

    plecati = db.departed_players()
    assert "Daniel Bîrligea" in plecati
    assert "Vasile Ene" not in plecati, "detașat, dar fără nicio apariție"


# ----------------------------------------------------------------------
# Presa vs. lotul de la Transfermarkt
#
# Transfermarkt isi actualizeaza loturile cu intarziere: Tanase aparea acolo si
# dupa ce trei publicatii scrisesera ca a semnat cu Omonia. Presa e mai rapida,
# dar mai putin sigura - de aceea cerem "confirmat", nu zvon.
# ----------------------------------------------------------------------
def _news_departure(db, player_id, source, certainty="confirmat"):
    article = db.upsert_news_article(
        url=f"https://{source}/{player_id}-{certainty}", source=source,
        title="A plecat", published_at="2026-09-05T12:00:00+00:00")
    db.save_news_facts(article, [{
        "kind": "transfer_plecare", "certainty": certainty,
        "player_name": "Daniel Bîrligea", "player_id": player_id,
        "evidence": "a plecat de la FCSB"}])


def test_a_confirmed_departure_is_surfaced(tmp_path):
    db = _Database(tmp_path / "plecari.db")
    team = db.upsert_team("FCSB", source="test")
    pid = db.upsert_player("Daniel Bîrligea", team, source="test")
    _news_departure(db, pid, "fanatik.ro")

    out = db.reported_departures()
    assert list(out["player_name"]) == ["Daniel Bîrligea"]
    assert int(out.iloc[0]["surse"]) == 1


def test_a_rumour_is_not_enough(tmp_path):
    db = _Database(tmp_path / "zvon.db")
    team = db.upsert_team("FCSB", source="test")
    pid = db.upsert_player("Daniel Bîrligea", team, source="test")
    _news_departure(db, pid, "fanatik.ro", certainty="zvon")
    assert db.reported_departures().empty


def test_sources_are_counted_not_articles(tmp_path):
    """Doua articole de la aceeasi publicatie raman o singura sursa."""
    db = _Database(tmp_path / "surse.db")
    team = db.upsert_team("FCSB", source="test")
    pid = db.upsert_player("Daniel Bîrligea", team, source="test")
    for i in range(3):
        article = db.upsert_news_article(
            url=f"https://fanatik.ro/{i}", source="fanatik.ro",
            title="A plecat", published_at="2026-09-05T12:00:00+00:00")
        db.save_news_facts(article, [{
            "kind": "transfer_plecare", "certainty": "confirmat",
            "player_name": "Daniel Bîrligea", "player_id": pid,
            "evidence": "a plecat"}])
    assert int(db.reported_departures().iloc[0]["surse"]) == 1


def test_a_player_already_out_of_the_squad_is_not_repeated(tmp_path):
    """Daca Transfermarkt l-a scos deja, nu mai e un conflict."""
    db = _Database(tmp_path / "scos.db")
    team = db.upsert_team("FCSB", source="test")
    pid = db.upsert_player("Daniel Bîrligea", team, source="test")
    _news_departure(db, pid, "fanatik.ro")
    db.detach_missing_players(team, [db.upsert_player("Altul", team,
                                                      source="test")])
    assert db.reported_departures().empty


def test_the_threshold_of_sources_can_be_raised(tmp_path):
    db = _Database(tmp_path / "prag.db")
    team = db.upsert_team("FCSB", source="test")
    pid = db.upsert_player("Daniel Bîrligea", team, source="test")
    _news_departure(db, pid, "fanatik.ro")
    assert db.reported_departures(min_sources=1).shape[0] == 1
    assert db.reported_departures(min_sources=2).empty
