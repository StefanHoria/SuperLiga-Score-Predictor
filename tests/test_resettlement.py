"""Anularea din lipsa de date e provizorie, nu definitiva.

Statisticile oficiale LPF apar la cateva ore dupa meci, iar decontarea ruleaza
imediat ce se fluiera finalul. Deci un pont pe cartonase prinde mereu baza
goala si era marcat "anulat" - pe veci, fiindca decontarea se uita doar la
ponturile inca deschise.

Efectul nu era cosmetic: doua bilete de pe 6 septembrie au ramas "anulate" desi
Petrolul - Corvinul avusese 10 cartonase galbene si Otelul - Rapid doua.
Amandoua pierdusera. Un pariu pierdut care dispare din acuratete o face sa
arate mai bine decat e.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from predictiescor.storage.db import Database
from predictiescor.tickets.settlement import (
    LOST, RETRYABLE_VOIDS, VOID, WON, settle_tickets,
)

ZIUA = "2026-09-06"


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "decontare.db")


def meci(db, *, goals=(2, 0)):
    return db.upsert_match(
        season=2026, utc_date=f"{ZIUA}T15:00:00+00:00",
        home="Petrolul Ploiesti", away="Corvinul Hunedoara",
        home_goals=goals[0], away_goals=goals[1], status="finished")


def bilet(db, match_id, *legs, stake=10.0):
    """Un bilet cu picioarele date, ca (piata, selectie)."""
    with db.connect() as conn:
        cur = conn.execute(
            "INSERT INTO tickets(created_at, match_day, strategy, label,"
            " stake, legs_total, status) VALUES ('x',?,'stat','Bilet',"
            " ?,?,'deschis')", (ZIUA, stake, len(legs)))
        ticket_id = cur.lastrowid
        for market, selection in legs:
            conn.execute(
                "INSERT INTO ticket_legs(ticket_id, match_id, market,"
                " selection, description, probability, fair_odds,"
                " offered_odds, result) VALUES (?,?,?,?,'x',0.5,2.0,2.0,"
                " 'deschis')", (ticket_id, match_id, market, selection))
    return ticket_id


def cartonase(db, match_id, acasa, deplasare):
    """Statisticile care sosesc dupa meci."""
    with db.connect() as conn:
        for team_id, is_home, galbene in ((1, 1, acasa), (2, 0, deplasare)):
            conn.execute(
                "INSERT INTO match_team_stats(match_id, team_id, is_home,"
                " yellow_cards, source) VALUES (?,?,?,?,'lpf')",
                (match_id, team_id, is_home, galbene))


def stare(db, ticket_id):
    with db.connect() as conn:
        row = conn.execute("SELECT status, profit FROM tickets"
                           " WHERE ticket_id = ?", (ticket_id,)).fetchone()
    return row["status"], row["profit"]


def rezultate(db, ticket_id):
    with db.connect() as conn:
        return [r["result"] for r in conn.execute(
            "SELECT result FROM ticket_legs WHERE ticket_id = ?"
            " ORDER BY leg_id", (ticket_id,))]


# ----------------------------------------------------------------------
def test_fara_statistici_biletul_e_anulat(db):
    """Prima decontare, la fluierul final: nu se ghiceste nimic."""
    m = meci(db)
    t = bilet(db, m, ("cartonașe", "sub 5.5"))
    settle_tickets(db)
    assert stare(db, t)[0] == "anulat"
    assert rezultate(db, t) == [VOID]


def test_statisticile_sosite_mai_tarziu_rejudeca_biletul(db):
    """Cazul Petrolul - Corvinul: 10 galbene, deci «sub 5.5» a pierdut."""
    m = meci(db)
    t = bilet(db, m, ("cartonașe", "sub 5.5"))
    settle_tickets(db)
    assert stare(db, t)[0] == "anulat"

    cartonase(db, m, 6, 4)
    settle_tickets(db)

    assert rezultate(db, t) == [LOST]
    assert stare(db, t) == ("pierdut", -10.0)


def test_un_bilet_castigat_cu_picior_anulat_se_recalculeaza(db):
    """Piciorul anulat iesise din cota; rezolvat ca pierdut, pierde biletul."""
    m = meci(db)
    t = bilet(db, m, ("total goluri", "peste 1.5"), ("cartonașe", "sub 5.5"))
    settle_tickets(db)
    assert stare(db, t)[0] == "castigat"

    cartonase(db, m, 6, 4)
    settle_tickets(db)

    assert rezultate(db, t) == [WON, LOST]
    assert stare(db, t) == ("pierdut", -10.0)


def test_fara_date_noi_decontarea_e_idempotenta(db):
    """Rulata de doua ori pe aceleasi date, nu schimba nimic."""
    m = meci(db)
    t = bilet(db, m, ("cartonașe", "peste 2.5"))
    settle_tickets(db)
    inainte = (stare(db, t), rezultate(db, t))
    settle_tickets(db)
    assert (stare(db, t), rezultate(db, t)) == inainte


def test_anularea_care_nu_se_poate_repara_ramane(db):
    """O piata nesuportata nu se rezolva singura, deci nu se reia degeaba."""
    m = meci(db)
    t = bilet(db, m, ("piață inventată", "orice"))
    settle_tickets(db)
    assert rezultate(db, t) == [VOID]

    with db.connect() as conn:
        nota = conn.execute("SELECT actual_value FROM ticket_legs"
                            " WHERE ticket_id = ?", (t,)).fetchone()[0]
    assert nota not in RETRYABLE_VOIDS

    settle_tickets(db)
    assert stare(db, t)[0] == "anulat"


def test_decontarea_pe_o_zi_anume_prinde_si_anularile(db):
    """`--settle --day` foloseste acelasi drum; filtrul nu trebuie sa-l rupa."""
    m = meci(db)
    t = bilet(db, m, ("cartonașe", "sub 5.5"))
    settle_tickets(db, match_day=ZIUA)
    assert stare(db, t)[0] == "anulat"

    cartonase(db, m, 6, 4)
    settle_tickets(db, match_day=ZIUA)
    assert stare(db, t) == ("pierdut", -10.0)


def test_o_zi_diferita_nu_atinge_biletul(db):
    m = meci(db)
    t = bilet(db, m, ("cartonașe", "sub 5.5"))
    settle_tickets(db)
    cartonase(db, m, 6, 4)

    settle_tickets(db, match_day="2026-09-05")
    assert stare(db, t)[0] == "anulat", "biletul e din alta zi"
