"""Normalizarea numelor de echipe - partea cea mai fragila a ingestiei."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from predictiescor.utils.teamnames import (
    canonical_team, slugify, strip_diacritics, unknown_teams,
)


@pytest.mark.parametrize("raw,expected", [
    ("FCSB", "FCSB"),
    ("Steaua Bucuresti", "FCSB"),
    ("FC FCSB", "FCSB"),
    ("(2.) FCSB", "FCSB"),
    ("FCSB (3.)", "FCSB"),
    ("CFR 1907 Cluj", "CFR Cluj"),
    ("O\u021belul Gala\u021bi", "Otelul Galati"),
    ("SC Otelul Galati", "Otelul Galati"),
    ("FC Rapid 1923", "Rapid Bucuresti"),
    ("ACSC FC Arges", "FC Arges"),
    ("FCV Farul Constanta", "Farul Constanta"),
    ("FCV Farul", "Farul Constanta"),
    ("Viitorul Constanta", "Farul Constanta"),
    ("Sepsi Sf. Gheorghe", "Sepsi OSK"),
    ("Csikszereda M. Ciuc", "Csikszereda"),
    ("AFK Csikszereda Miercurea Ciuc", "Csikszereda"),
    ("Politehnica Iasi", "Poli Iasi"),
    ("SCM Gloria Buz\u0103u", "Gloria Buzau"),
    ("Metaloglobus Bucharest", "Metaloglobus"),
    ("CS Hunedoara", "Corvinul Hunedoara"),
])
def test_maps_known_variants(raw, expected):
    assert canonical_team(raw) == expected


def test_the_two_craiovas_stay_apart():
    """CS Universitatea Craiova si FC U Craiova 1948 sunt cluburi diferite.

    Fara regula dura pe '1948', potrivirea aproximativa le contopeste - ceea ce
    ar amesteca rezultatele a doua echipe intr-una singura.
    """
    craiova = {"U Craiova", "Univ. Craiova", "Universitatea Craiova",
               "CS U Craiova"}
    mititelu = {"U Craiova 1948", "FC U Craiova 1948", "FCU Craiova 1948",
                "Universitatea Craiova 1948"}

    assert {canonical_team(n) for n in craiova} == {"Universitatea Craiova"}
    assert {canonical_team(n) for n in mititelu} == {"FC U Craiova 1948"}


def test_years_are_ignored_but_1948_is_not():
    assert canonical_team("FC Rapid 1923") == canonical_team("Rapid Bucuresti")
    assert canonical_team("CFR 1907 Cluj") == canonical_team("CFR Cluj")
    assert canonical_team("U Craiova 1948") != canonical_team("U Craiova")


def test_unknown_name_is_returned_and_recorded():
    result = canonical_team("Echipa Inexistenta XYZ")
    assert result == "Echipa Inexistenta XYZ"
    assert "Echipa Inexistenta XYZ" in unknown_teams()


def test_strip_diacritics():
    assert strip_diacritics("O\u021belul Gala\u021bi") == "Otelul Galati"
    assert strip_diacritics("Buz\u0103u") == "Buzau"


def test_slugify_removes_rank_markers():
    assert slugify("(11.) CFR Cluj") == "cfr cluj"
    assert slugify("CFR Cluj (11.)") == "cfr cluj"
