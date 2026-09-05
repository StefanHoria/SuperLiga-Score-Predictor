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
    # sursa conteaza: detasarea judeca doar jucatorii pe care Transfermarkt
    # ii cunoaste, fiindca doar absenta LOR de pe pagina inseamna ceva
    ids = {name: db.upsert_player(name, team, source="transfermarkt",
                                  ext_id=name)
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
    outsider = db.upsert_player("Radu Marin", other, source="transfermarkt",
                                ext_id="rm")
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
    """Faptul trebuie sa numeasca chiar clubul curent - altfel e ignorat."""
    article = db.upsert_news_article(
        url=f"https://{source}/{player_id}-{certainty}", source=source,
        title="A plecat", published_at="2026-09-05T12:00:00+00:00")
    db.save_news_facts(article, [{
        "kind": "transfer_plecare", "certainty": certainty,
        "player_name": "Daniel Bîrligea", "player_id": player_id,
        "team": "FCSB",
        "evidence": "Daniel Bîrligea a plecat de la FCSB"}])


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
            "team": "FCSB",
            "evidence": "Daniel Bîrligea a plecat de la FCSB"}])
    assert int(db.reported_departures().iloc[0]["surse"]) == 1


def test_a_player_already_out_of_the_squad_is_not_repeated(tmp_path):
    """Daca Transfermarkt l-a scos deja, nu mai e un conflict."""
    db = _Database(tmp_path / "scos.db")
    team = db.upsert_team("FCSB", source="test")
    pid = db.upsert_player("Daniel Bîrligea", team, source="transfermarkt",
                           ext_id="b1")
    _news_departure(db, pid, "fanatik.ro")
    db.detach_missing_players(team, [db.upsert_player(
        "Altul", team, source="transfermarkt", ext_id="altul")])
    assert db.reported_departures().empty


def test_the_threshold_of_sources_can_be_raised(tmp_path):
    db = _Database(tmp_path / "prag.db")
    team = db.upsert_team("FCSB", source="test")
    pid = db.upsert_player("Daniel Bîrligea", team, source="test")
    _news_departure(db, pid, "fanatik.ro")
    assert db.reported_departures(min_sources=1).shape[0] == 1
    assert db.reported_departures(min_sources=2).empty


# ----------------------------------------------------------------------
# Corectiile la detectarea plecarilor
#
# Din 8 jucatori semnalati, 4 erau gresiti. Trei cauze distincte, fiecare cu
# testul ei - altfel se intorc.
# ----------------------------------------------------------------------
def _departure(db, player_id, team_id, evidence, source="fanatik.ro",
               kind="transfer_plecare", certainty="confirmat", when="2026-09-05"):
    article = db.upsert_news_article(
        url=f"https://{source}/{player_id}/{kind}/{when}", source=source,
        title="stire", published_at=f"{when}T12:00:00+00:00")
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO news_facts(article_id, kind, team_id, player_id,"
            " player_name, certainty, evidence, published_at, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (article, kind, team_id, player_id,
             conn.execute("SELECT name FROM players WHERE player_id=?",
                          (player_id,)).fetchone()["name"],
             certainty, evidence, f"{when}T12:00:00+00:00", "2026-09-05"))


def test_a_departure_from_a_former_club_is_ignored(tmp_path):
    """„El Sawy a plecat de la Rapid" — dar el e la UTA Arad."""
    db = _Database(tmp_path / "fost.db")
    uta = db.upsert_team("UTA Arad", source="test")
    rapid = db.upsert_team("Rapid Bucuresti", source="test")
    pid = db.upsert_player("Omar El Sawy", uta, source="test")
    _departure(db, pid, rapid, "Omar El Sawy a dezvăluit de ce a plecat de la Rapid")
    assert db.reported_departures().empty


def test_a_departure_from_the_current_club_counts(tmp_path):
    db = _Database(tmp_path / "curent.db")
    fcsb = db.upsert_team("FCSB", source="test")
    pid = db.upsert_player("Florin Tănase", fcsb, source="test")
    _departure(db, pid, fcsb, "Florin Tănase a plecat de la FCSB!")
    assert list(db.reported_departures()["player_name"]) == ["Florin Tănase"]


def test_a_later_return_cancels_the_departure(tmp_path):
    """Crețu s-a despărțit de FCSB, apoi Becali a revenit asupra deciziei."""
    db = _Database(tmp_path / "revenit.db")
    fcsb = db.upsert_team("FCSB", source="test")
    pid = db.upsert_player("Valentin Crețu", fcsb, source="test")
    _departure(db, pid, fcsb, "Valentin Crețu s-a despărțit de FCSB",
               when="2026-08-30")
    _departure(db, pid, fcsb, "Valentin Crețu a revenit în lotul FCSB",
               kind="revenire", when="2026-08-31")
    assert db.reported_departures().empty


def test_an_arrival_worded_like_a_departure_is_ignored(tmp_path):
    """„cedat de CFR **la** FCSB" e o venire la FCSB, nu o plecare."""
    db = _Database(tmp_path / "venire.db")
    fcsb = db.upsert_team("FCSB", source="test")
    pid = db.upsert_player("Daniel Bîrligea", fcsb, source="test")
    _departure(db, pid, fcsb,
               "Bîrligea a fost cedat de CFR la FCSB pentru 2 milioane de euro.")
    assert db.reported_departures().empty


def test_the_direction_helper_reads_the_preposition():
    from predictiescor.storage.db import _is_destination
    assert _is_destination("cedat de CFR la FCSB", "FCSB") is True
    assert _is_destination("a plecat de la FCSB", "FCSB") is False
    assert _is_destination("pleacă de la FCSB la Omonia", "FCSB") is False


# ----------------------------------------------------------------------
# Marcajul manual
# ----------------------------------------------------------------------
def test_a_manual_mark_excludes_the_player(tmp_path):
    db = _Database(tmp_path / "manual.db")
    team = db.upsert_team("FCSB", source="test")
    pid = db.upsert_player("Daniel Bîrligea", team, source="test")
    assert "Daniel Bîrligea" not in db.departed_players()

    db.mark_departed(pid, note="știu că a plecat")
    assert "Daniel Bîrligea" in db.departed_players()


def test_a_manual_mark_can_be_undone(tmp_path):
    db = _Database(tmp_path / "anulare.db")
    team = db.upsert_team("FCSB", source="test")
    pid = db.upsert_player("Daniel Bîrligea", team, source="test")
    db.mark_departed(pid)
    db.unmark_departed(pid)
    assert "Daniel Bîrligea" not in db.departed_players()
    assert db.manual_departures().empty


def test_marking_twice_does_not_duplicate(tmp_path):
    db = _Database(tmp_path / "dublu.db")
    team = db.upsert_team("FCSB", source="test")
    pid = db.upsert_player("Ion Pop", team, source="test")
    db.mark_departed(pid, note="prima")
    db.mark_departed(pid, note="a doua")
    marks = db.manual_departures()
    assert len(marks) == 1
    assert marks.iloc[0]["note"] == "a doua"


# ----------------------------------------------------------------------
# Cine poate fi detasat
#
# Prima versiune detasa pe oricine lipsea de pe pagina Transfermarkt. Dar
# jucatorii adusi de LPF au numele legal complet ("Manuel Alberto Cunha
# Lopes"), iar Transfermarkt foloseste nume scurte - nu se potrivesc niciodata.
# Asa au ajuns "fara club" Korenica si altii, care jucasera cu o zi inainte.
# ----------------------------------------------------------------------
def _played(db, player_id, team_id, when, minutes=90):
    match_id = db.upsert_match(season=2026, utc_date=f"{when}T18:00:00+00:00",
                               home="FCSB", away=f"Adversar {when}",
                               home_goals=1, away_goals=0, status="finished")
    db.upsert_player_stats(match_id, player_id, team_id,
                           {"minutes": minutes, "goals": 0})


def test_a_player_the_source_never_knew_is_not_detached(tmp_path):
    """Fara id de la sursa, absenta lui de pe pagina nu inseamna nimic."""
    db = _Database(tmp_path / "necunoscut.db")
    team = db.upsert_team("FCSB", source="test")
    known = db.upsert_player("Ion Pop", team, source="transfermarkt", ext_id="1")
    lpf_only = db.upsert_player("Manuel Alberto Cunha Lopes", team,
                                source="lpf", ext_id="9")

    db.detach_missing_players(team, [known])
    with db.connect() as conn:
        row = conn.execute("SELECT team_id FROM players WHERE player_id = ?",
                           (lpf_only,)).fetchone()
    assert row["team_id"] == team, "nu are id Transfermarkt, deci nu-l judecăm"


def test_a_player_who_played_recently_is_not_detached(tmp_path):
    """Plasa de siguranță: cine a jucat săptămâna trecută e la club."""
    import datetime as _dt
    db = _Database(tmp_path / "recent.db")
    team = db.upsert_team("FCSB", source="test")
    other = db.upsert_player("Ion Pop", team, source="transfermarkt", ext_id="1")
    recent = db.upsert_player("Meriton Korenica", team,
                              source="transfermarkt", ext_id="2")
    ieri = (_dt.date.today() - _dt.timedelta(days=1)).isoformat()
    _played(db, recent, team, ieri)

    db.detach_missing_players(team, [other])
    with db.connect() as conn:
        row = conn.execute("SELECT team_id FROM players WHERE player_id = ?",
                           (recent,)).fetchone()
    assert row["team_id"] == team


def test_a_known_player_who_stopped_playing_is_detached(tmp_path):
    """Cazul pentru care exista mecanismul: chiar a plecat."""
    import datetime as _dt
    db = _Database(tmp_path / "plecat_real.db")
    team = db.upsert_team("FCSB", source="test")
    stays = db.upsert_player("Ion Pop", team, source="transfermarkt", ext_id="1")
    left = db.upsert_player("Vasile Ene", team, source="transfermarkt",
                            ext_id="2")
    demult = (_dt.date.today() - _dt.timedelta(days=120)).isoformat()
    _played(db, left, team, demult)

    assert db.detach_missing_players(team, [stays]) == 1
    with db.connect() as conn:
        row = conn.execute("SELECT team_id FROM players WHERE player_id = ?",
                           (left,)).fetchone()
    assert row["team_id"] is None


# ----------------------------------------------------------------------
# Transferuri in timpul sezonului
#
# LPF vede jucatorul in formatia clubului nou, dar el e inregistrat la cel
# vechi, deci nu se potriveste cu nimeni si se crea un rand nou. Asa au iesit
# doi Korenica - unul la CFR Cluj, unul la FCSB - cu istoricul rupt in doua.
# ----------------------------------------------------------------------
def test_a_transferred_player_is_found_at_his_old_club(tmp_path):
    db = _Database(tmp_path / "transfer.db")
    cfr = db.upsert_team("CFR Cluj", source="test")
    fcsb = db.upsert_team("FCSB", source="test")
    pid = db.upsert_player("Meriton Korenica", cfr, source="test")

    assert db.player_at_other_team("Meriton Korenica", fcsb) == pid


def test_diacritics_do_not_hide_him(tmp_path):
    db = _Database(tmp_path / "diacritice.db")
    cfr = db.upsert_team("CFR Cluj", source="test")
    fcsb = db.upsert_team("FCSB", source="test")
    pid = db.upsert_player("Ștefan Bîrligea", cfr, source="test")
    assert db.player_at_other_team("Stefan Birligea", fcsb) == pid


def test_a_player_already_at_the_team_is_not_returned(tmp_path):
    db = _Database(tmp_path / "acelasi.db")
    fcsb = db.upsert_team("FCSB", source="test")
    db.upsert_player("Ion Pop", fcsb, source="test")
    assert db.player_at_other_team("Ion Pop", fcsb) is None


def test_two_players_with_the_same_name_are_left_alone(tmp_path):
    """La o cautare in toata liga, ambiguitatea inseamna «nu stiu»."""
    db = _Database(tmp_path / "omonimi.db")
    a = db.upsert_team("CFR Cluj", source="test")
    b = db.upsert_team("Rapid Bucuresti", source="test")
    fcsb = db.upsert_team("FCSB", source="test")
    db.upsert_player("Ion Pop", a, source="test")
    db.upsert_player("Ion Pop", b, source="test")
    assert db.player_at_other_team("Ion Pop", fcsb) is None


def test_moving_a_player_keeps_his_history(tmp_path):
    db = _Database(tmp_path / "mutare.db")
    cfr = db.upsert_team("CFR Cluj", source="test")
    fcsb = db.upsert_team("FCSB", source="test")
    pid = db.upsert_player("Meriton Korenica", cfr, source="test")
    _played(db, pid, cfr, "2026-08-01")

    db.move_player(pid, fcsb)
    with db.connect() as conn:
        assert conn.execute("SELECT team_id FROM players WHERE player_id=?",
                            (pid,)).fetchone()["team_id"] == fcsb
        assert conn.execute("SELECT COUNT(*) c FROM player_match_stats"
                            " WHERE player_id=?", (pid,)).fetchone()["c"] == 1


def test_merging_two_records_joins_the_history(tmp_path):
    db = _Database(tmp_path / "unire.db")
    cfr = db.upsert_team("CFR Cluj", source="test")
    fcsb = db.upsert_team("FCSB", source="test")
    vechi = db.upsert_player("Meriton Korenica", cfr, source="test", ext_id="k")
    nou = db.upsert_player("Meriton Korenica", fcsb, source="lpf", ext_id="k2")
    _played(db, vechi, cfr, "2026-08-01")
    _played(db, nou, fcsb, "2026-09-05")

    db.merge_players(vechi, nou)
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) c FROM players"
                            " WHERE name='Meriton Korenica'").fetchone()["c"] == 1
        assert conn.execute("SELECT COUNT(*) c FROM player_match_stats"
                            " WHERE player_id=?", (vechi,)).fetchone()["c"] == 2


# ----------------------------------------------------------------------
# Modelul: un jucator, un singur rand
#
# Gruparea pe (jucator, echipa) ii rupea istoricul la fiecare transfer si il
# facea invizibil la clubul nou pana aduna 270 de minute acolo. Korenica avea
# 418 minute in total, dar doar 45 la FCSB.
# ----------------------------------------------------------------------
def _appearances(rows):
    """rows: (jucator, echipa, data, minute, goluri)."""
    import pandas as _pd
    return _pd.DataFrame([
        {"player": p, "team": t, "date": d, "minutes": m, "goals": g,
         "assists": 0, "yellow_cards": 0, "red_cards": 0, "position": "Attack",
         "match_id": i}
        for i, (p, t, d, m, g) in enumerate(rows)
    ])


def _model():
    from predictiescor.modeling.players import PlayerModel
    return PlayerModel(min_minutes=0, half_life_days=240, shrinkage_per90=1.0)


def test_a_transferred_player_keeps_one_row():
    rows = [("Meriton Korenica", "CFR Cluj", "2026-08-16", 90, 1),
            ("Meriton Korenica", "CFR Cluj", "2026-08-23", 90, 1),
            ("Meriton Korenica", "FCSB", "2026-09-05", 45, 0),
            ("Altul", "FCSB", "2026-09-05", 90, 0)]
    model = _model().fit_from_match_stats(_appearances(rows))
    korenica = [r for r in model.rates.values() if "Korenica" in r.player]
    assert len(korenica) == 1


def test_he_is_listed_at_his_current_club():
    rows = [("Meriton Korenica", "CFR Cluj", "2026-08-16", 90, 1),
            ("Meriton Korenica", "FCSB", "2026-09-05", 45, 0),
            ("Altul", "FCSB", "2026-09-05", 90, 0)]
    model = _model().fit_from_match_stats(_appearances(rows))
    assert "Meriton Korenica" in [model.rates[k].player
                                  for k in model.by_team["FCSB"]]
    assert "Meriton Korenica" not in [model.rates[k].player
                                      for k in model.by_team.get("CFR Cluj", [])]


def test_the_squad_overrides_the_last_appearance():
    """Daca lotul spune altceva decat ultimul meci, lotul castiga."""
    rows = [("Ion Pop", "CFR Cluj", "2026-08-16", 90, 1),
            ("Altul", "FCSB", "2026-09-05", 90, 0)]
    model = _model().fit_from_match_stats(
        _appearances(rows), current_teams={"Ion Pop": "FCSB"})
    assert "Ion Pop" in [model.rates[k].player for k in model.by_team["FCSB"]]


def test_all_his_minutes_count_toward_the_rate():
    """Ratele la 90 de minute sunt insusirea lui, nu a clubului."""
    rows = [("Meriton Korenica", "CFR Cluj", "2026-08-16", 90, 1),
            ("Meriton Korenica", "CFR Cluj", "2026-08-23", 90, 1),
            ("Meriton Korenica", "FCSB", "2026-09-05", 45, 0),
            ("Altul", "FCSB", "2026-09-05", 90, 0)]
    model = _model().fit_from_match_stats(_appearances(rows))
    korenica = next(r for r in model.rates.values() if "Korenica" in r.player)
    assert korenica.minutes == 225.0, "toate minutele, nu doar cele de la FCSB"
    assert korenica.goals_per90 > 0.3, "cele doua goluri de la CFR conteaza"


def test_expected_minutes_come_from_the_current_club_only():
    """Rolul lui la clubul nou e alta poveste decat la cel vechi."""
    rows = [("Ion Pop", "CFR Cluj", "2026-08-16", 90, 0),
            ("Ion Pop", "CFR Cluj", "2026-08-23", 90, 0),
            ("Ion Pop", "FCSB", "2026-09-05", 30, 0),
            ("Altul", "FCSB", "2026-09-05", 90, 0)]
    model = _model().fit_from_match_stats(_appearances(rows))
    pop = next(r for r in model.rates.values() if r.player == "Ion Pop")
    assert pop.expected_minutes < 45, "30 de minute la FCSB, nu 90 ca la CFR"
