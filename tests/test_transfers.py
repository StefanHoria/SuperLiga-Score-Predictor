"""Transferuri: veniri, plecari si sumele lor.

Testele apara doua lucruri: citirea corecta a sumelor din formele in care le
publica sursa, si distinctia dintre "gratis" (zero, cunoscut) si "nepublicata"
(necunoscut). Confundandu-le, un club care si-a vandut jucatorii pe sume
secrete ar parea ca i-a dat degeaba.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from predictiescor.data.transfers import (
    PLECARE, VENIRE, Transfer, balance, parse_fee, parse_transfers,
)


# ----------------------------------------------------------------------
# Sumele
# ----------------------------------------------------------------------
@pytest.mark.parametrize("text,euro,imprumut", [
    ("€1.00m", 1_000_000.0, False),
    ("€600k", 600_000.0, False),
    ("€3.00m", 3_000_000.0, False),
    ("€100k", 100_000.0, False),
    ("Loan fee: €1.50m", 1_500_000.0, True),
    ("free transfer", 0.0, False),
    ("loan transfer", 0.0, True),
])
def test_fee_forms(text, euro, imprumut):
    assert parse_fee(text) == (euro, imprumut)


@pytest.mark.parametrize("text", ["?", "-", "", "n/a"])
def test_an_undisclosed_fee_is_not_zero(text):
    """Miezul: „nu se știe" nu e „gratis"."""
    valoare, _ = parse_fee(text)
    assert valoare is None


def test_a_free_transfer_is_zero_not_unknown():
    valoare, _ = parse_fee("free transfer")
    assert valoare == 0.0


def test_a_loan_is_recognised_even_without_a_fee():
    assert parse_fee("loan transfer")[1] is True
    assert parse_fee("Loan fee: €1.50m")[1] is True
    assert parse_fee("€1.00m")[1] is False


# ----------------------------------------------------------------------
# Tabelele
# ----------------------------------------------------------------------
PAGE = """
<table class="items"><thead><tr><th></th><th>Player</th><th>Age</th>
<th>Nat.</th><th>Left</th><th>Fee</th></tr></thead><tbody>
<tr><td class="bg_Sturm"></td><td></td><td></td>
    <td class="hauptlink"><a>Meriton Korenica</a></td><td>Left Winger</td>
    <td class="zentriert">29</td><td></td><td></td><td></td>
    <td class="hauptlink"><a>CFR Cluj</a></td><td>SuperLiga</td>
    <td class="rechts hauptlink">€600k</td></tr>
</tbody></table>
<table class="items"><thead><tr><th></th><th>Player</th><th>Age</th>
<th>Nat.</th><th>Joined</th><th>Fee</th></tr></thead><tbody>
<tr><td class="bg_Sturm"></td><td></td><td></td>
    <td class="hauptlink"><a>Daniel Bîrligea</a></td><td>Centre-Forward</td>
    <td class="zentriert">26</td><td></td><td></td><td></td>
    <td class="hauptlink"><a>Frosinone</a></td><td>Serie A</td>
    <td class="rechts hauptlink">Loan fee: €1.50m</td></tr>
</tbody></table>
"""


def test_the_header_says_which_table_is_which():
    """«Left» = de unde a venit; «Joined» = unde a plecat."""
    rows = parse_transfers(PAGE)
    kinds = {r.player_name: r.direction for r in rows}
    assert kinds["Meriton Korenica"] == VENIRE
    assert kinds["Daniel Bîrligea"] == PLECARE


def test_a_row_is_read_completely():
    row = next(r for r in parse_transfers(PAGE)
               if r.player_name == "Daniel Bîrligea")
    assert row.other_club == "Frosinone"
    assert row.other_league == "Serie A"
    assert row.position == "Centre-Forward"
    assert row.age == 26
    assert row.fee_eur == 1_500_000.0
    assert row.is_loan is True
    assert row.fee_text == "Loan fee: €1.50m", "textul sursei rămâne verificabil"


def test_a_page_without_tables_gives_nothing():
    assert parse_transfers("<html><body>nimic</body></html>") == []


# ----------------------------------------------------------------------
# Bilantul
# ----------------------------------------------------------------------
def _t(direction, fee, loan=False, name="X"):
    return Transfer(direction=direction, player_name=name, position=None,
                    age=None, other_club=None, other_league=None,
                    fee_eur=fee, fee_text="", is_loan=loan)


def test_profit_is_received_minus_paid():
    out = balance([_t(PLECARE, 3_000_000.0), _t(VENIRE, 1_000_000.0)])
    assert out["incasat"] == 3_000_000.0
    assert out["platit"] == 1_000_000.0
    assert out["profit"] == 2_000_000.0


def test_undisclosed_fees_do_not_count_as_zero():
    """Un club care a vândut pe sume secrete nu i-a dat degeaba."""
    out = balance([_t(PLECARE, None), _t(PLECARE, 1_000_000.0)])
    assert out["incasat"] == 1_000_000.0
    assert out["sume_nepublicate"] == 1


def test_free_transfers_are_counted_as_free():
    out = balance([_t(VENIRE, 0.0), _t(VENIRE, None)])
    assert out["gratis"] == 1
    assert out["sume_nepublicate"] == 1


def test_loans_are_counted_separately():
    out = balance([_t(PLECARE, 1_500_000.0, loan=True), _t(VENIRE, 0.0)])
    assert out["imprumuturi"] == 1


def test_an_empty_window_gives_zeroes():
    out = balance([])
    assert out["profit"] == 0.0
    assert out["veniri"] == 0 and out["plecari"] == 0


# ----------------------------------------------------------------------
# Transferurile ca sursa de plecari
#
# E cea mai buna sursa: e explicita, are si suma, si apare acolo inaintea
# actualizarii lotului. Birligea figura in lot la FCSB, dar in tabelul de
# plecari era deja trecut la Frosinone.
# ----------------------------------------------------------------------
from predictiescor.storage.db import Database


def _club_with(tmp_path, name, fee_text, when=None):
    db = Database(tmp_path / f"{name}.db")
    team = db.upsert_team("FCSB", source="test")
    pid = db.upsert_player(name, team, source="test")
    db.upsert_transfers(2026, team, [
        Transfer(direction=PLECARE, player_name=name, position=None, age=None,
                 other_club="Frosinone", other_league="Serie A",
                 fee_eur=None, fee_text=fee_text, is_loan=False)])
    if when:
        match_id = db.upsert_match(season=2026, utc_date=f"{when}T18:00:00+00:00",
                                   home="FCSB", away="UTA Arad", home_goals=1,
                                   away_goals=0, status="finished")
        db.upsert_player_stats(match_id, pid, team, {"minutes": 90})
    return db


def test_a_departure_in_the_transfer_table_excludes_the_player(tmp_path):
    db = _club_with(tmp_path, "Daniel Bîrligea", "Loan fee: €1.50m")
    assert "Daniel Bîrligea" in db.departed_players()


def test_an_end_of_loan_row_does_not_exclude_him(tmp_path):
    """«End of loan 31/12/2026» e un împrumut care se încheie mai TÂRZIU."""
    db = _club_with(tmp_path, "Arnold Garita", "End of loan 31/12/2026")
    assert "Arnold Garita" not in db.departed_players()


def test_someone_who_played_last_week_is_not_excluded(tmp_path):
    """Sursa poate greși; terenul nu."""
    import datetime as _dt
    ieri = (_dt.date.today() - _dt.timedelta(days=1)).isoformat()
    db = _club_with(tmp_path, "Meriton Korenica", "€600k", when=ieri)
    assert "Meriton Korenica" not in db.departed_players()


def test_an_old_appearance_does_not_protect_him(tmp_path):
    import datetime as _dt
    demult = (_dt.date.today() - _dt.timedelta(days=120)).isoformat()
    db = _club_with(tmp_path, "Vasile Ene", "€600k", when=demult)
    assert "Vasile Ene" in db.departed_players()


def test_the_balance_table_totals_by_club(tmp_path):
    db = Database(tmp_path / "bilant.db")
    fcsb = db.upsert_team("FCSB", source="test")
    db.upsert_transfers(2026, fcsb, [
        Transfer(PLECARE, "A", None, None, None, None, 3_000_000.0, "€3.00m", False),
        Transfer(VENIRE, "B", None, None, None, None, 1_000_000.0, "€1.00m", False),
        Transfer(VENIRE, "C", None, None, None, None, None, "?", False)])
    row = db.transfer_balance(2026).iloc[0]
    assert row["încasat"] == 3_000_000.0
    assert row["plătit"] == 1_000_000.0
    assert row["profit"] == 2_000_000.0
    assert row["sume nepublicate"] == 1


def test_refetching_replaces_instead_of_duplicating(tmp_path):
    db = Database(tmp_path / "rescriere.db")
    team = db.upsert_team("FCSB", source="test")
    row = Transfer(PLECARE, "A", None, None, None, None, 1.0, "€1", False)
    db.upsert_transfers(2026, team, [row])
    db.upsert_transfers(2026, team, [row])
    assert len(db.transfers_df(2026)) == 1
