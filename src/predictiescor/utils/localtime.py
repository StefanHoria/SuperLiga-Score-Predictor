"""Ora locala, pentru tot ce se arata unui om.

Baza tine totul in UTC, ca sa nu depinda de masina pe care ruleaza. Dar
utilizatorul e in Romania, iar un meci care incepe la 20:30 nu are de ce sa
apara ca 17:30.

De ce aici si nu in `app/common.py`, unde a stat la inceput: conversia era
disponibila doar paginilor Streamlit, iar modulele de dedesubt - generatorul de
bilete, scripturile din linia de comanda - taiau ora direct din sirul ISO
(`kickoff[11:16]`) si afisau UTC fara sa spuna. Punand-o intre utilitare, o
poate folosi oricine are ceva de aratat.

Regula: **stocam UTC, afisam local**. Conversia se face DOAR la afisare;
nimic din ce ajunge in baza nu trece pe aici.
"""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd

#: Fusul in care se afiseaza totul. EEST vara (UTC+3), EET iarna (UTC+2).
LOCAL_TZ = ZoneInfo("Europe/Bucharest")

DEFAULT_FORMAT = "%d.%m.%Y %H:%M"


def to_local(value) -> datetime | None:
    """Un moment din baza (UTC) in ora Romaniei. `None` daca nu se poate citi.

    Accepta text ISO, `datetime` sau `pd.Timestamp`. Valorile fara fus orar
    sunt considerate UTC, fiindca asa le scrie baza.
    """
    if value is None or value == "":
        return None
    moment = pd.to_datetime(value, utc=True, errors="coerce")
    if moment is pd.NaT or pd.isna(moment):
        return None
    return moment.to_pydatetime().astimezone(LOCAL_TZ)


def local_time(value, fmt: str = DEFAULT_FORMAT) -> str:
    """Momentul, scris in ora Romaniei. Sir gol daca lipseste."""
    moment = to_local(value)
    return moment.strftime(fmt) if moment else ""


def local_series(values, fmt: str = DEFAULT_FORMAT) -> pd.Series:
    """Aceeasi conversie, pentru o coloana intreaga.

    **Pastreaza indexul intrarii.** Fara asta, atribuind rezultatul intr-un
    tabel sortat sau filtrat, pandas alinia dupa index si punea orele pe
    randurile gresite - in tacere, fara nicio eroare. Intr-un tabel sortat
    descrescator, orele ieseau pur si simplu inversate.
    """
    source = values if isinstance(values, pd.Series) else pd.Series(list(values))
    moments = pd.to_datetime(source, utc=True, errors="coerce")
    converted = moments.dt.tz_convert(LOCAL_TZ).dt.strftime(fmt).fillna("")
    return pd.Series(converted.to_numpy(), index=source.index)


def now_local() -> datetime:
    return datetime.now(timezone.utc).astimezone(LOCAL_TZ)


def tz_note() -> str:
    """Cum se numeste acum fusul, ca sa scrie pe ecran ce ora e afisata."""
    return now_local().strftime("%Z") or "ora României"
