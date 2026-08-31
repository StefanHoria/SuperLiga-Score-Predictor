"""Ora afisata.

Baza tine totul in UTC, ca sa nu depinda de masina pe care ruleaza. Dar
utilizatorul e in Romania, iar un meci care incepe la 20:30 nu are de ce sa
apara ca 17:30 - cu atat mai putin fara sa scrie nicaieri ca e UTC.
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "predictiescor" / "app"))

import pandas as pd
import pytest

from common import LOCAL_TZ, local_series, local_time, to_local


# ----------------------------------------------------------------------
def test_summer_adds_three_hours():
    """EEST = UTC+3. Meciul de la 17:30 UTC incepe la 20:30 in Romania."""
    assert local_time("2026-08-31T17:30:00+00:00") == "31.08.2026 20:30"


def test_winter_adds_two_hours():
    """EET = UTC+2, dupa ce se da ceasul inapoi."""
    assert local_time("2026-12-01T17:30:00+00:00") == "01.12.2026 19:30"


def test_late_kickoff_can_change_the_day():
    """22:30 UTC intr-o zi de vara inseamna 01:30 a doua zi."""
    assert local_time("2026-08-31T22:30:00+00:00") == "01.09.2026 01:30"


def test_a_naive_timestamp_is_treated_as_utc():
    """Baza scrie uneori fara fus; asa a fost mereu, deci UTC."""
    assert local_time("2026-08-31T17:30:00") == "31.08.2026 20:30"


def test_datetime_objects_work_too():
    moment = datetime(2026, 8, 31, 17, 30, tzinfo=timezone.utc)
    assert local_time(moment) == "31.08.2026 20:30"


def test_missing_values_give_an_empty_string():
    for value in (None, "", "nu e o dată", pd.NaT):
        assert local_time(value) == ""


def test_the_format_can_be_chosen():
    assert local_time("2026-08-31T17:30:00+00:00", "%H:%M") == "20:30"
    assert local_time("2026-08-31T17:30:00+00:00", "%d.%m") == "31.08"


def test_to_local_returns_an_aware_moment():
    moment = to_local("2026-08-31T17:30:00+00:00")
    assert moment.tzinfo is not None
    assert moment.hour == 20
    assert moment.utcoffset().total_seconds() == 3 * 3600


def test_a_whole_column_is_converted():
    out = local_series(["2026-08-31T17:30:00+00:00",
                        "2026-12-01T17:30:00+00:00", None])
    assert list(out) == ["31.08.2026 20:30", "01.12.2026 19:30", ""]


def test_an_empty_column_does_not_crash():
    assert list(local_series([])) == []


def test_conversion_is_never_a_shift_of_zero():
    """Daca cineva sterge din greseala conversia, testul asta cade."""
    utc = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)
    assert utc.astimezone(LOCAL_TZ).hour != utc.hour
