"""Pe ce meciuri se mai poate paria.

Un pont pe un meci terminat nu e o predictie - e o consultare a rezultatului.
Si totusi asa s-au generat opt bilete pe FC Voluntari - FC Arges dupa fluierul
final: ziua de 7 septembrie avea un meci la 17:30 si unul la 20:30, iar
`scripts/tickets.py` lua toate meciurile zilei. Doua dintre bilete apucasera
sa se si deconteze - unul "castigat" - inainte sa fie sterse.

Garda sta acum in `bettable()`, chemata din `build()`, ca sa nu mai depinda de
tinerea de minte a fiecarui apelant.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import pandas as pd
import pytest

from predictiescor.tickets import bettable


def meciuri(*randuri) -> pd.DataFrame:
    """Un tabel ca cel intors de `matches_on`."""
    return pd.DataFrame([
        {"match_id": i, "utc_date": kickoff, "status": status,
         "home_goals": hg, "away_goals": ag,
         "home_team": f"Gazda {i}", "away_team": f"Oaspete {i}"}
        for i, (kickoff, status, hg, ag) in enumerate(randuri, 1)
    ])


def peste(ore: float) -> str:
    return (pd.Timestamp.now(tz="UTC") + pd.Timedelta(hours=ore)).isoformat()


# ----------------------------------------------------------------------
def test_un_meci_viitor_ramane():
    frame = meciuri((peste(3), "scheduled", None, None))
    assert len(bettable(frame)) == 1


def test_meciul_cu_scor_e_scos():
    """Cazul Voluntari - Arges: terminat, notat, dar in aceeasi zi."""
    frame = meciuri((peste(-2), "finished", 1, 0))
    assert bettable(frame).empty


def test_meciul_inceput_fara_scor_e_scos():
    """Minutul 20: nicio sursa n-a scris inca scorul, dar pariul e inchis."""
    frame = meciuri((peste(-0.4), "scheduled", None, None))
    assert bettable(frame).empty


def test_statusul_singur_e_de_ajuns():
    """Sursa a inchis meciul fara sa dea scorul - tot nu se pariaza."""
    frame = meciuri((peste(2), "finished", None, None))
    assert bettable(frame).empty


def test_ziua_amestecata_pastreaza_doar_ce_urmeaza():
    """Exact 7 septembrie: unul terminat la 17:30, unul la 20:30."""
    frame = meciuri((peste(-2.5), "finished", 1, 0),
                    (peste(0.5), "scheduled", None, None))
    ramase = bettable(frame)
    assert list(ramase["match_id"]) == [2]


def test_indexul_e_refacut():
    """Rezultatul se itereaza cu `.iloc`, deci indexul trebuie sa fie 0..n-1."""
    frame = meciuri((peste(-2), "finished", 2, 2),
                    (peste(1), "scheduled", None, None))
    assert list(bettable(frame).index) == [0]


def test_un_tabel_gol_nu_crapa():
    assert bettable(pd.DataFrame()).empty


def test_o_ora_ilizibila_nu_trece():
    """Fara ora nu se poate spune ca meciul n-a inceput, deci nu se pariaza."""
    frame = meciuri(("nu e o dată", "scheduled", None, None))
    assert bettable(frame).empty


def test_coloanele_lipsa_nu_crapa():
    """`upcoming_matches` nu intoarce coloanele de scor."""
    frame = pd.DataFrame([{"match_id": 1, "utc_date": peste(4)}])
    assert len(bettable(frame)) == 1


# ----------------------------------------------------------------------
# Garda din `build()`
# ----------------------------------------------------------------------
class _PipelineFals:
    ready = True


def test_build_refuza_meciurile_incepute(monkeypatch):
    from predictiescor.tickets.generator import TicketGenerator

    generator = TicketGenerator.__new__(TicketGenerator)
    apelat = []
    monkeypatch.setattr(TicketGenerator, "candidate_legs",
                        lambda self, match: apelat.append(match) or [])

    frame = meciuri((peste(-2), "finished", 1, 0))
    assert generator.build(frame, match_day="2026-09-07") == []
    assert not apelat, "n-ar fi trebuit sa se uite la meciul jucat"


def test_backfill_are_voie_pe_zile_trecute(monkeypatch):
    """Backfill-ul genereaza dinadins in trecut, cu modelul taiat inainte."""
    from predictiescor.tickets.generator import TicketGenerator

    generator = TicketGenerator.__new__(TicketGenerator)
    apelat = []
    monkeypatch.setattr(TicketGenerator, "candidate_legs",
                        lambda self, match: apelat.append(match) or [])

    frame = meciuri((peste(-2), "finished", 1, 0))
    generator.build(frame, match_day="2026-09-07", allow_started=True)
    assert len(apelat) == 1
