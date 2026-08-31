"""Extragerea de fapte din stiri.

Multe dintre teste sunt greseli prinse pe articole reale, pastrate ca sa nu se
intoarca: "Andrei Ratiu" legat de jucatorul "Doru Andrei", "UTA Arad" citit ca
jucatorul "Ofri Arad", o stire de tenis despre Djokovic atribuita fundasului
Damjan Djokovic.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from predictiescor.data.news import (
    Article, is_relevant, looks_useful, parse_feed, strip_html,
)
from predictiescor.data.newsfacts import (
    CONFIRMED, LIKELY, RUMOUR, PlayerIndex, certainty_of, corroboration,
    extract_facts, kind_of, normalise, sentences,
)

ROSTER = [
    {"player_id": 59, "name": "Florin Tănase", "team_id": 1},
    {"player_id": 58, "name": "Valentin Crețu", "team_id": 1},
    {"player_id": 282, "name": "Doru Andrei", "team_id": 2},
    {"player_id": 60, "name": "Ofri Arad", "team_id": 3},
    {"player_id": 134, "name": "Damjan Djokovic", "team_id": 4},
    {"player_id": 226, "name": "Marius Coman", "team_id": 5},
]
TEAMS = ["FCSB", "UTA Arad", "CFR Cluj", "Universitatea Craiova"]


@pytest.fixture
def index():
    return PlayerIndex.build(ROSTER, TEAMS)


# ----------------------------------------------------------------------
# Text
# ----------------------------------------------------------------------
def test_diacritics_are_removed_for_matching():
    assert normalise("Florin Tănase") == "florin tanase"
    assert normalise("Valentin Creţu") == "valentin cretu"


def test_sentences_split_on_punctuation():
    assert len(sentences("A plecat. A revenit! Chiar așa?")) == 3


def test_html_is_stripped_with_entities():
    assert strip_html("<p>Gol &amp; asist</p>") == "Gol & asist"


# ----------------------------------------------------------------------
# Increderea
# ----------------------------------------------------------------------
@pytest.mark.parametrize("text,expected", [
    ("FCSB a anunțat oficial despărțirea", CONFIRMED),
    ("Jucătorul s-a accidentat la antrenament", CONFIRMED),
    ("Nu va juca în meciul de duminică", CONFIRMED),
    ("Transferul e aproape gata", LIKELY),
    ("Mutarea e în proporție de 99%", LIKELY),
    ("Potrivit surselor, ar putea pleca", RUMOUR),
    ("Clubul negociază cu jucătorul", RUMOUR),
    ("A fost o zi frumoasă la stadion", RUMOUR),
])
def test_certainty_levels(text, expected):
    assert certainty_of(text) == expected


def test_the_weakest_marker_wins():
    """„Surse spun că a semnat oficial” ramane zvon: ancora e „surse spun”."""
    assert certainty_of("Potrivit surselor, a semnat oficial") == RUMOUR


def test_an_unmarked_sentence_is_never_a_confirmation():
    assert certainty_of("Se discută despre viitorul lui") == RUMOUR


# ----------------------------------------------------------------------
# Tipul faptului
# ----------------------------------------------------------------------
@pytest.mark.parametrize("text,kind", [
    ("S-a accidentat grav la genunchi", "absenta"),
    ("Este suspendat pentru următorul meci", "absenta"),
    ("A revenit după accidentare", "revenire"),
    ("A semnat cu Rapid București", "transfer_venire"),
    ("A plecat de la FCSB", "transfer_plecare"),
    ("Iată echipa probabilă a gazdelor", "formatie_probabila"),
])
def test_kind_detection(text, kind):
    found = kind_of(text)
    assert found is not None and found[0] == kind


def test_negation_cancels_the_fact():
    """„Nu s-a accidentat” nu e o absenta."""
    assert kind_of("Din fericire, nu s-a accidentat") is None


def test_a_sentence_about_nothing_yields_no_kind():
    assert kind_of("Meciul începe la ora 21:00") is None


# ----------------------------------------------------------------------
# Potrivirea numelor - greselile prinse pe articole reale
# ----------------------------------------------------------------------
def test_full_name_is_matched(index):
    found = index.find("Florin Tănase a plecat de la FCSB")
    assert [(n, i) for n, i, _ in found] == [("Florin Tănase", 59)]


def test_a_first_name_is_not_read_as_someone_elses_surname(index):
    """„Andrei Rațiu” nu e jucatorul „Doru Andrei”."""
    assert index.find("Transferul lui Andrei Rațiu e gata") == []


def test_a_team_name_is_not_read_as_a_player(index):
    """„UTA Arad” nu e jucatorul „Ofri Arad”."""
    assert index.find("FCSB a pierdut cu UTA Arad") == []


def test_a_different_first_name_blocks_the_link(index):
    """„Florinel Coman” nu e „Marius Coman” - il consemnam, nu il legam."""
    found = index.find("Revenirea lui Florinel Coman e discutată")
    assert [(n, i) for n, i, _ in found] == [("Florinel Coman", None)]


def test_a_connective_before_the_surname_still_links(index):
    found = index.find("Contractul lui Crețu a fost reziliat")
    assert [(n, i) for n, i, _ in found] == [("Valentin Crețu", 58)]


def test_positions_are_reported_in_order(index):
    found = index.find("Florin Tănase și Valentin Crețu au plecat")
    assert [n for n, _, _ in found] == ["Florin Tănase", "Valentin Crețu"]
    assert found[0][2] < found[1][2]


# ----------------------------------------------------------------------
# Extragerea completa
# ----------------------------------------------------------------------
def test_a_simple_departure_is_extracted(index):
    facts = extract_facts("Florin Tănase a plecat de la FCSB.", index)
    assert len(facts) == 1
    assert facts[0].kind == "transfer_plecare"
    assert facts[0].certainty == CONFIRMED
    assert facts[0].player_id == 59


def test_the_fact_goes_to_the_player_nearest_the_verb(index):
    """Doi oameni, un singur verb: conteaza cine e langa el.

    Fara regula asta, „cine vine la FCSB după plecarea lui Tănase” producea un
    transfer de VENIRE pe numele celui care tocmai plecase.
    """
    text = "Se caută înlocuitor după ce Florin Tănase a plecat de la FCSB."
    facts = extract_facts(text, index)
    assert len(facts) == 1
    assert facts[0].player_name == "Florin Tănase"

    # verbul e langa Crețu, deci faptul e al lui
    other = "Florin Tănase a marcat, iar Valentin Crețu a fost scos din lot."
    assert extract_facts(other, index)[0].player_name == "Valentin Crețu"


def test_the_strongest_kind_in_a_sentence_wins(index):
    """O propozitie cu doua verbe da un singur fapt, nu doua contradictorii."""
    text = ("Florin Tănase a fost cedat la Omonia, în timp ce "
            "Valentin Crețu a fost scos din lot.")
    facts = extract_facts(text, index)
    assert len(facts) == 1
    assert facts[0].kind == "absenta"
    assert facts[0].player_name == "Valentin Crețu"


def test_every_fact_keeps_the_sentence_it_came_from(index):
    facts = extract_facts("Se pare că Valentin Crețu revine la echipă.", index)
    assert facts and "Crețu revine" in facts[0].evidence


def test_a_surname_shadow_of_a_linked_player_is_dropped(index):
    """„Crețu” si „Valentin Crețu” in acelasi articol sunt acelasi fapt."""
    text = "Valentin Crețu a revenit. Crețu a revenit la antrenamente."
    facts = extract_facts(text, index)
    assert len(facts) == 1
    assert facts[0].player_id == 58


def test_nothing_is_invented_from_an_empty_text(index):
    assert extract_facts("", index) == []
    assert extract_facts("Vremea e frumoasă azi.", index) == []


def test_the_team_finder_is_used_when_given(index):
    facts = extract_facts("Florin Tănase a plecat de la FCSB.", index,
                          team_finder=lambda s: "FCSB" if "FCSB" in s else None)
    assert facts[0].team == "FCSB"


# ----------------------------------------------------------------------
# Fluxul RSS
# ----------------------------------------------------------------------
FEED = """<?xml version="1.0"?><rss><channel>
<item><title>Tănase a plecat de la FCSB</title>
<link>https://exemplu.ro/a</link>
<description>&lt;p&gt;Oficial: a semnat cu Omonia&lt;/p&gt;</description>
<category>Superliga</category>
<pubDate>Mon, 31 Aug 2026 13:00:00 +0300</pubDate></item>
<item><title>Djokovic, eliminat la US Open</title>
<link>https://exemplu.ro/b</link><description>Tenis</description>
<category>Tenis</category>
<pubDate>Mon, 31 Aug 2026 12:00:00 +0300</pubDate></item>
</channel></rss>"""


def test_feed_items_are_parsed():
    articles = parse_feed(FEED, "test")
    assert len(articles) == 2
    assert articles[0].title == "Tănase a plecat de la FCSB"
    assert "a semnat cu Omonia" in articles[0].summary
    assert articles[0].category == "Superliga"


def test_publication_time_is_converted_to_utc():
    article = parse_feed(FEED, "test")[0]
    assert article.published_at.isoformat() == "2026-08-31T10:00:00+00:00"


def test_a_broken_feed_says_so():
    with pytest.raises(RuntimeError, match="ilizibil"):
        parse_feed("<not xml", "test")


def test_the_category_decides_when_the_source_publishes_one():
    """Stirea de tenis despre Djokovic nu trebuie sa intre in liga romana."""
    superliga, tennis = parse_feed(FEED, "test")
    categories = ("superliga",)
    assert is_relevant(superliga, categories, set()) is True
    assert is_relevant(tennis, categories, {"djokovic"}) is False


def test_without_categories_the_team_names_decide():
    superliga, tennis = parse_feed(FEED, "test")
    assert is_relevant(superliga, (), {"fcsb"}) is True
    assert is_relevant(tennis, (), {"fcsb"}) is False


def test_an_article_without_a_subject_marker_is_skipped():
    boring = Article(source="t", url="u", title="Prețurile la bilete",
                     summary="Stadionul e mic", body="", category=None,
                     published_at=None)
    assert looks_useful(boring) is False


def test_an_article_that_promises_a_fact_is_kept():
    assert looks_useful(parse_feed(FEED, "test")[0]) is True


# ----------------------------------------------------------------------
# Coroborare intre surse
# ----------------------------------------------------------------------
def test_two_sources_reporting_the_same_thing_count_twice():
    facts = [
        {"kind": "absenta", "player_name": "Florin Tănase", "source": "a.ro"},
        {"kind": "absenta", "player_name": "Florin Tănase", "source": "b.ro"},
        {"kind": "absenta", "player_name": "Valentin Crețu", "source": "a.ro"},
    ]
    counts = corroboration(facts)
    assert counts[("absenta", "Florin Tănase")] == 2
    assert counts[("absenta", "Valentin Crețu")] == 1


def test_the_same_source_twice_is_still_one_source():
    facts = [{"kind": "absenta", "player_name": "X", "source": "a.ro"}] * 3
    assert corroboration(facts)[("absenta", "X")] == 1


# ----------------------------------------------------------------------
# Verbul potrivit, obiectul gresit - clasa de erori gasita la prima masuratoare
# ----------------------------------------------------------------------
@pytest.mark.parametrize("text", [
    "Arnold Garita a părăsit terenul la pauză",
    "Jucătorul a părăsit ședința de pregătire",
    "A părăsit vestiarul nervos",
    "A părăsit stadionul înainte de final",
])
def test_leaving_the_pitch_is_not_a_transfer(text):
    """Trei din patru greseli de la prima masuratoare veneau de aici."""
    assert kind_of(text) is None


def test_leaving_the_club_is_still_a_transfer():
    found = kind_of("Florin Tănase a părăsit clubul după 7 etape")
    assert found is not None and found[0] == "transfer_plecare"


def test_reversing_a_decision_is_not_a_players_return():
    assert kind_of("Gigi Becali a revenit asupra deciziei") is None


def test_a_real_return_is_still_detected():
    found = kind_of("Jucătorul a revenit după accidentare")
    assert found is not None and found[0] == "revenire"


def test_a_question_is_never_a_confirmation():
    """„Baiaram pleacă de la U Craiova?” - titlul insusi se intreaba."""
    assert certainty_of("Ștefan Baiaram pleacă de la U Craiova?") == LIKELY
    assert certainty_of("Ștefan Baiaram pleacă de la U Craiova.") == CONFIRMED
