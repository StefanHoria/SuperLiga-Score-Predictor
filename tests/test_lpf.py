"""Parsarea paginilor LPF - sursa oficiala pentru statisticile sezonului curent."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest
from bs4 import BeautifulSoup

from predictiescor.data.lpf import LpfProvider, parse_date

# Fragment redus la esential, cu structura reala de pe lpf.ro
PAGE = """
<html><head><title>FC UNIVERSITATEA CLUJ - FC PETROLUL - Etapa 7 Superliga
 - Statistici live</title></head>
<body>
  <span>28 august 2026, 21:00</span>
  <table>
    <tr><td class="stats_subtitle" colspan="2">Cartonașe galbene</td></tr>
    <tr>
      <td class="stats_val_gazda"><div class="val_gazda">2</div></td>
      <td class="stats_val_oaspeti"><div class="val_oaspeti">3</div></td>
    </tr>
    <tr><td class="stats_subtitle" colspan="2">Posesia mingii</td></tr>
    <tr>
      <td class="stats_val_gazda"><div class="val_gazda">69%</div></td>
      <td class="stats_val_oaspeti"><div class="val_oaspeti">31%</div></td>
    </tr>
    <tr><td class="stats_subtitle" colspan="2">Șuturi</td></tr>
    <tr>
      <td class="stats_val_gazda"><div class="val_gazda">24</div></td>
      <td class="stats_val_oaspeti"><div class="val_oaspeti">11</div></td>
    </tr>
    <tr><td class="stats_subtitle" colspan="2">Șuturi pe poartă</td></tr>
    <tr>
      <td class="stats_val_gazda"><div class="val_gazda">9</div></td>
      <td class="stats_val_oaspeti"><div class="val_oaspeti">7</div></td>
    </tr>
    <tr><td class="stats_subtitle" colspan="2">Șuturi blocate</td></tr>
    <tr>
      <td class="stats_val_gazda"><div class="val_gazda">8</div></td>
      <td class="stats_val_oaspeti"><div class="val_oaspeti">0</div></td>
    </tr>
    <tr><td class="stats_subtitle" colspan="2">Șuturi din afara careului</td></tr>
    <tr>
      <td class="stats_val_gazda"><div class="val_gazda">7</div></td>
      <td class="stats_val_oaspeti"><div class="val_oaspeti">7</div></td>
    </tr>
    <tr><td class="stats_subtitle" colspan="2">Total pase</td></tr>
    <tr>
      <td class="stats_val_gazda"><div class="val_gazda">562</div></td>
      <td class="stats_val_oaspeti"><div class="val_oaspeti">229</div></td>
    </tr>
    <tr><td class="stats_subtitle" colspan="2">Pase precise</td></tr>
    <tr>
      <td class="stats_val_gazda"><div class="val_gazda">469</div></td>
      <td class="stats_val_oaspeti"><div class="val_oaspeti">167</div></td>
    </tr>
    <tr><td class="stats_subtitle" colspan="2">Cornere</td></tr>
    <tr>
      <td class="stats_val_gazda"><div class="val_gazda">10</div></td>
      <td class="stats_val_oaspeti"><div class="val_oaspeti">4</div></td>
    </tr>
  </table>
</body></html>
"""

# Aceeasi pagina, dar meciul nu s-a jucat: toate valorile sunt zero.
FUTURE_PAGE = PAGE
for _pair in ("2", "3", "69%", "31%", "24", "11", "9", "7", "8", "0",
              "562", "229", "469", "167", "10", "4"):
    FUTURE_PAGE = FUTURE_PAGE.replace(f">{_pair}</div>", ">0</div>")


@pytest.fixture
def provider(tmp_path, monkeypatch):
    from predictiescor.storage.db import Database
    prov = LpfProvider.__new__(LpfProvider)
    prov.db = Database(tmp_path / "test.db")
    return prov


def parse(html):
    return LpfProvider._parse_stats(BeautifulSoup(html, "lxml"))


# ----------------------------------------------------------------------
def test_extracts_every_statistic():
    stats = parse(PAGE)
    assert stats["Cornere"] == ("10", "4")
    assert stats["Șuturi"] == ("24", "11")
    assert stats["Posesia mingii"] == ("69%", "31%")
    assert len(stats) == 9


def test_values_are_not_shifted_between_rows():
    """Bug clasic la parsarea pe text: valorile se decaleaza cu un rand."""
    stats = parse(PAGE)
    assert stats["Cartonașe galbene"] == ("2", "3")
    assert stats["Total pase"] == ("562", "229")
    assert stats["Pase precise"] == ("469", "167")


def test_future_match_detected_as_empty():
    """Paginile meciurilor viitoare exista, dar cu zerouri peste tot."""
    assert LpfProvider._has_real_values(parse(PAGE)) is True
    assert LpfProvider._has_real_values(parse(FUTURE_PAGE)) is False


# ----------------------------------------------------------------------
# Pagina completata pe jumatate
#
# LPF publica pagina meciului TREPTAT: imediat dupa fluierul final apar
# cartonasele, iar restul tabelului sta pe zero cateva ore. Garda veche cerea
# doar ca *ceva* sa nu fie zero, deci pagina asta trecea si scriam „0 cornere,
# 0 suturi" ca valori adevarate. Cinci meciuri din 61 au ajuns asa in baza.
#
# Masurat pe 7 septembrie 2026, fata de Flashscore: FCSB - UTA Arad avea in
# baza 0 cornere si avusese 9; CFR Cluj - Farul 0 si avusese 11. Pontul „sub
# 8.5 cornere" iesise castigat contra unui zero inventat.
# ----------------------------------------------------------------------
HALF_FILLED_PAGE = """
<html><head><title>FCSB - UTA ARAD - Etapa 7 Superliga - Statistici live</title>
</head>
<body>
  <span>30 august 2026, 21:00</span>
  <table>
    <tr><td class="stats_subtitle" colspan="2">Cartonașe galbene</td></tr>
    <tr>
      <td class="stats_val_gazda"><div class="val_gazda">2</div></td>
      <td class="stats_val_oaspeti"><div class="val_oaspeti">0</div></td>
    </tr>
    <tr><td class="stats_subtitle" colspan="2">Posesia mingii</td></tr>
    <tr>
      <td class="stats_val_gazda"><div class="val_gazda">0%</div></td>
      <td class="stats_val_oaspeti"><div class="val_oaspeti">0%</div></td>
    </tr>
    <tr><td class="stats_subtitle" colspan="2">Cornere</td></tr>
    <tr>
      <td class="stats_val_gazda"><div class="val_gazda">0</div></td>
      <td class="stats_val_oaspeti"><div class="val_oaspeti">0</div></td>
    </tr>
    <tr><td class="stats_subtitle" colspan="2">Total pase</td></tr>
    <tr>
      <td class="stats_val_gazda"><div class="val_gazda">0</div></td>
      <td class="stats_val_oaspeti"><div class="val_oaspeti">0</div></td>
    </tr>
  </table>
</body></html>
"""


def test_a_half_filled_page_is_not_taken_for_data():
    """Cartonasele puse, restul pe zero: tabelul nu e completat inca."""
    assert LpfProvider._stats_are_filled(parse(HALF_FILLED_PAGE)) is False


def test_the_old_guard_let_it_through():
    """De asta a scapat: `_has_real_values` vedea cele doua cartonase."""
    assert LpfProvider._has_real_values(parse(HALF_FILLED_PAGE)) is True


def test_a_filled_page_passes():
    assert LpfProvider._stats_are_filled(parse(PAGE)) is True


def test_a_future_page_is_still_rejected():
    assert LpfProvider._stats_are_filled(parse(FUTURE_PAGE)) is False


def test_zero_passes_alone_is_enough_to_reject():
    """Nicio echipa nu termina un meci cu zero pase."""
    assert LpfProvider._stats_are_filled(
        {"Total pase": ("0", "0"), "Cornere": ("4", "6")}) is False


def test_a_missing_label_falls_back_instead_of_rejecting_everything():
    """Daca LPF redenumeste coloanele, nu ne oprim tacut din a mai citi."""
    assert LpfProvider._stats_are_filled({"Cornere": ("4", "6")}) is True
    assert LpfProvider._stats_are_filled({"Cornere": ("0", "0")}) is False


def test_columns_are_mapped_and_derived(provider):
    stats = parse(PAGE)
    home = provider._columns(0, stats)

    assert home["corners"] == 10
    assert home["shots"] == 24
    assert home["possession"] == 69
    assert home["yellow_cards"] == 2

    # derivate: interior = total - exterior
    assert home["shots_inside_box"] == 24 - 7
    # in afara portii = total - pe poarta - blocate
    assert home["shots_off_target"] == 24 - 9 - 8
    assert home["pass_accuracy"] == pytest.approx(83.5, abs=0.1)


def test_away_side_reads_second_value(provider):
    away = provider._columns(1, parse(PAGE))
    assert away["corners"] == 4
    assert away["shots"] == 11
    assert away["possession"] == 31


def test_derived_values_never_negative(provider):
    """Datele contradictorii nu trebuie sa produca statistici negative."""
    html = PAGE.replace(
        '<td class="stats_subtitle" colspan="2">Șuturi din afara careului</td></tr>\n'
        '    <tr>\n      <td class="stats_val_gazda"><div class="val_gazda">7</div>',
        '<td class="stats_subtitle" colspan="2">Șuturi din afara careului</td></tr>\n'
        '    <tr>\n      <td class="stats_val_gazda"><div class="val_gazda">99</div>')
    home = provider._columns(0, parse(html))
    assert home["shots_inside_box"] >= 0
    assert home["shots_off_target"] >= 0


# ----------------------------------------------------------------------
# LPF publica ora Romaniei; noi stocam UTC. Fara conversie, meciurile ajungeau
# in baza cu doua-trei ore mai tarziu decat se joaca.
@pytest.mark.parametrize("text,expected", [
    # vara (EEST, UTC+3)
    ("28 august 2026, 21:00", (2026, 8, 28, 18, 0)),
    ("7 septembrie 2026, 17:30", (2026, 9, 7, 14, 30)),
    # iarna (EET, UTC+2) - la miezul noptii, ziua se schimba
    ("1 decembrie 2025", (2025, 11, 30, 22, 0)),
    ("12 IANUARIE 2027, 00:00", (2027, 1, 11, 22, 0)),
])
def test_parse_romanian_dates_converts_to_utc(text, expected):
    parsed = parse_date(text)
    assert parsed is not None
    assert (parsed.year, parsed.month, parsed.day,
            parsed.hour, parsed.minute) == expected
    assert parsed.utcoffset().total_seconds() == 0, "trebuie sa fie UTC"


def test_summer_and_winter_use_different_offsets():
    """Ora de vara nu e o constanta: +3 vara, +2 iarna."""
    summer = parse_date("15 iulie 2026, 12:00")
    winter = parse_date("15 ianuarie 2026, 12:00")
    assert summer.hour == 9, "EEST = UTC+3"
    assert winter.hour == 10, "EET = UTC+2"


@pytest.mark.parametrize("text", ["", "fara data", "32 august 2026", None])
def test_bad_dates_return_none(text):
    assert parse_date(text) is None


def test_kickoff_read_from_page():
    kickoff = LpfProvider._parse_kickoff(BeautifulSoup(PAGE, "lxml"))
    assert kickoff is not None
    # pagina scrie "28 august 2026, 21:00" (ora Romaniei) => 18:00 UTC
    assert kickoff.isoformat() == "2026-08-28T18:00:00+00:00"


# ----------------------------------------------------------------------
# Bariera: LPF imbogateste doar meciuri despre care stim ca s-au jucat
# ----------------------------------------------------------------------
def _payload(home="FCSB", away="UTA Arad", day="2026-08-30"):
    from datetime import datetime, timezone
    return {
        "page_id": 9999, "home": home, "away": away, "round": "Etapa 7",
        "kickoff": datetime.fromisoformat(day).replace(tzinfo=timezone.utc),
        "stats": parse(PAGE),
    }


def test_gate_rejects_match_without_score(provider):
    """LPF publica statistici si pentru meciuri nejucate - nu le acceptam."""
    provider.db.upsert_match(
        season=2026, utc_date="2026-08-30T20:00:00+00:00",
        home="FCSB", away="UTA Arad", status="scheduled")

    assert provider._finished_match_id(2026, _payload()) is None
    assert provider.ingest_match(_payload(), 2026) == 0

    stats = provider.db.team_stats_df()
    assert stats.empty, "nu trebuie scrisa nicio statistica"


def test_gate_accepts_match_with_score(provider):
    provider.db.upsert_match(
        season=2026, utc_date="2026-08-30T20:00:00+00:00",
        home="FCSB", away="UTA Arad", home_goals=2, away_goals=1,
        status="finished")

    assert provider._finished_match_id(2026, _payload()) is not None
    assert provider.ingest_match(_payload(), 2026) == 2

    stats = provider.db.team_stats_df()
    assert len(stats) == 2
    assert set(stats["corners"]) == {10, 4}


def test_gate_tolerates_rescheduling(provider):
    """Meciul mutat cu o zi trebuie tot recunoscut."""
    provider.db.upsert_match(
        season=2026, utc_date="2026-08-31T20:00:00+00:00",
        home="FCSB", away="UTA Arad", home_goals=0, away_goals=0,
        status="finished")
    assert provider._finished_match_id(2026, _payload(day="2026-08-30")) is not None


def test_gate_never_creates_matches(provider):
    """Un meci necunoscut nu trebuie inventat de LPF."""
    provider.ingest_match(_payload(home="Echipa X", away="Echipa Y"), 2026)
    assert provider.db.matches_df().empty
