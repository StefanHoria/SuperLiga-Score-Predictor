"""Ce poate fi si ce nu poate fi o statistica de meci.

Verificarea sta **la scriere**, nu la citire, si nu in provideri. Fiecare sursa
are alt fel de a gresi - LPF scrie zero cand pagina nu e completata inca,
API-Football a scris posesie 0/0 la un meci din 2024 - iar o garda pusa in
fiecare provider s-ar uita la urmatorul. Toti trec prin `upsert_team_stats`,
deci acolo se pune.

Regula e simpla si nu incearca sa ghiceasca adevarul: **o valoare imposibila nu
se scrie ca zero, se scrie ca „nu stiu".** Diferenta conteaza peste tot mai
departe - modelul sare peste ce nu stie, decontarea anuleaza pontul in loc sa-l
piarda, iar cand sursa completeaza mai tarziu, valoarea adevarata intra peste
`NULL` fara sa se bata cu un zero inventat.

Ce NU face fisierul asta: nu spune care sursa are dreptate cand doua surse dau
cifre diferite si amandoua plauzibile. Aia e o alegere, si e scrisa in
`DEFER_TO_FLASHSCORE` din `lpf.py`.
"""
from __future__ import annotations

import logging
from typing import Any, Iterable

log = logging.getLogger(__name__)

#: Intervalul in care o statistica de ECHIPA poate cadea intr-un meci de 90 de
#: minute. Marginile de sus sunt largi dinadins: treaba lor e sa prinda o
#: eroare de format sau de unitate, nu sa taie un meci neobisnuit. Recordurile
#: reale stau confortabil inauntru - 21 de cornere intr-un meci de Liga 1, 9
#: cartonase galbene la o singura echipa.
RANGES: dict[str, tuple[float, float]] = {
    "shots": (0, 60),
    "shots_on_target": (0, 40),
    "shots_off_target": (0, 40),
    "shots_blocked": (0, 30),
    "shots_inside_box": (0, 50),
    "shots_outside_box": (0, 40),
    "corners": (0, 30),
    "offsides": (0, 20),
    "fouls": (0, 45),
    "yellow_cards": (0, 12),
    "red_cards": (0, 5),
    "possession": (0, 100),
    "passes": (0, 1200),
    "passes_accurate": (0, 1200),
    "pass_accuracy": (0, 100),
    "saves": (0, 25),
    "xg": (0, 10),
    "xg_estimated": (0, 10),
    "xga": (0, 10),
    "big_chances": (0, 25),
    "tackles": (0, 60),
    "attacks": (0, 300),
    "interceptions": (0, 100),
    "duels_won": (0, 150),
    "dribbles": (0, 60),
    "free_kicks": (0, 60),
    "passes_into_box": (0, 100),
}

#: Statistici care nu pot fi zero intr-un meci chiar jucat. O echipa nu termina
#: un meci cu zero pase si nici cu zero posesie; cand sursa scrie zero acolo,
#: inseamna ca n-are datele, nu ca s-a intamplat asa.
#:
#: Cornerele si suturile NU sunt in lista: sunt rare, dar posibile.
NEVER_ZERO = ("passes", "possession")


def clean_team_stats(stats: dict[str, Any], *, source: str = ""
                     ) -> tuple[dict[str, Any], list[str]]:
    """Statisticile, cu valorile imposibile scoase.

    Intoarce perechea (valori curate, campuri respinse). Campurile respinse
    devin `None` - adica „nu stiu" - nu dispar din dictionar, ca sa nu para ca
    providerul nici n-a incercat.
    """
    clean: dict[str, Any] = dict(stats)
    rejected: list[str] = []

    for name, value in stats.items():
        if value is None:
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            clean[name], _ = None, rejected.append(name)
            continue

        limits = RANGES.get(name)
        if limits is not None and not (limits[0] <= number <= limits[1]):
            clean[name] = None
            rejected.append(name)
            continue
        if name in NEVER_ZERO and number == 0:
            clean[name] = None
            rejected.append(name)

    if rejected:
        log.warning("[%s] valori imposibile, scrise ca necunoscute: %s",
                    source or "?", ", ".join(sorted(rejected)))
    return clean, rejected


def implausible_rows(db, *, season: int | None = None) -> list[dict[str, Any]]:
    """Randurile din baza care n-ar trece verificarea de azi.

    Folosit ca sa se vada ce a intrat inainte ca verificarea sa existe, si ca
    raport pe pagina de surse.
    """
    where, params = "", []
    if season is not None:
        where, params = " WHERE m.season = ?", [season]

    with db.connect() as conn:
        rows = conn.execute(
            "SELECT s.*, m.season, m.utc_date, m.status"
            " FROM match_team_stats s"
            " JOIN matches m ON m.match_id = s.match_id" + where,
            params,
        ).fetchall()

    out: list[dict[str, Any]] = []
    for row in rows:
        values = {name: row[name] for name in RANGES if name in row.keys()}
        _, rejected = clean_team_stats(values, source="baza")
        if rejected:
            out.append({
                "match_id": int(row["match_id"]),
                "team_id": int(row["team_id"]),
                "season": row["season"],
                "utc_date": row["utc_date"],
                "campuri": sorted(rejected),
                "valori": {name: values[name] for name in sorted(rejected)},
            })
    return out


def missing_columns(db, columns: Iterable[str] = ("corners", "yellow_cards"),
                    *, season: int | None = None) -> dict[str, int]:
    """Cate meciuri jucate n-au deloc coloanele date.

    Golurile din acoperire nu sunt erori - dar sunt limita reala a modelului,
    si merita numarate la vedere, nu descoperite dupa.
    """
    where, params = "", []
    if season is not None:
        where, params = " AND m.season = ?", [season]

    out: dict[str, int] = {}
    with db.connect() as conn:
        for column in columns:
            out[column] = int(conn.execute(
                "SELECT COUNT(*) c FROM matches m"
                " WHERE m.status = 'finished'"
                "   AND NOT EXISTS (SELECT 1 FROM match_team_stats s"
                f"                  WHERE s.match_id = m.match_id"
                f"                    AND s.{column} IS NOT NULL)" + where,
                params).fetchone()["c"])
    return out
