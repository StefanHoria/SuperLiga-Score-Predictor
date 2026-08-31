"""Introducerea cotelor pentru meciurile care urmeaza.

De ce manual si nu prin scraping: singurele cote pe care le avem automat sunt
cele de INCHIDERE, de la football-data.co.uk, si doar pentru meciuri deja
jucate. Pentru meciurile viitoare nu exista sursa gratuita si stabila - casele
de pariuri randeaza cotele din JavaScript si le schimba constant, iar
raclarea lor e si fragila, si intr-o zona incerta din punct de vedere legal.

Consecinta practica: fara cote, strategia "Bilet de valoare" nu are cu ce sa
compare si nu produce nimic. Modulul asta rezolva exact acel gol: introduci
cotele pe care le vezi la casa ta (in interfata sau dintr-un CSV), iar modelul
le foloseste ca sa gaseasca diferentele in favoarea ta.
"""
from __future__ import annotations

import io
import logging
from typing import Any, Iterable

import pandas as pd

from ..storage.db import Database
from ..utils.teamnames import canonical_team

log = logging.getLogger(__name__)

#: numele coloanei din CSV -> (piata, selectie)
COLUMN_MARKETS: dict[str, tuple[str, str]] = {
    "1": ("1x2", "home"),
    "x": ("1x2", "draw"),
    "2": ("1x2", "away"),
    "gazda": ("1x2", "home"),
    "egal": ("1x2", "draw"),
    "oaspete": ("1x2", "away"),
    "peste_2.5": ("ou_2.5", "over"),
    "sub_2.5": ("ou_2.5", "under"),
    "peste2.5": ("ou_2.5", "over"),
    "sub2.5": ("ou_2.5", "under"),
    "gg": ("btts", "yes"),
    "ng": ("btts", "no"),
}

DEFAULT_BOOKMAKER = "manual"


def find_match(db: Database, home: str, away: str,
               day: str | None = None) -> int | None:
    """Gaseste meciul dupa echipe (si optional ziua). None daca nu exista."""
    home_id = db.team_id_by_name(canonical_team(home))
    away_id = db.team_id_by_name(canonical_team(away))
    if home_id is None or away_id is None:
        return None

    sql = ("SELECT match_id FROM matches WHERE home_team_id=? AND away_team_id=?"
           " AND home_goals IS NULL")
    params: list[Any] = [home_id, away_id]
    if day:
        sql += " AND abs(julianday(substr(utc_date,1,10)) - julianday(?)) <= 3"
        params.append(day)
    sql += " ORDER BY utc_date LIMIT 1"

    with db.connect() as conn:
        row = conn.execute(sql, params).fetchone()
    return int(row["match_id"]) if row else None


def store_odds(db: Database, entries: Iterable[dict[str, Any]], *,
               bookmaker: str = DEFAULT_BOOKMAKER) -> dict[str, Any]:
    """Salveaza cotele. Fiecare intrare: gazda, oaspete, [data], si coloane de cote."""
    rows: list[tuple] = []
    unmatched: list[str] = []
    matched = 0

    for entry in entries:
        home = str(entry.get("gazda") or entry.get("home") or "").strip()
        away = str(entry.get("oaspete") or entry.get("away") or "").strip()
        if not home or not away:
            continue

        day = entry.get("data") or entry.get("date")
        day = str(day)[:10] if day else None
        match_id = find_match(db, home, away, day)
        if match_id is None:
            unmatched.append(f"{home} - {away}")
            continue
        matched += 1

        for column, value in entry.items():
            key = str(column).strip().lower()
            market = COLUMN_MARKETS.get(key)
            if market is None:
                continue
            try:
                price = float(str(value).replace(",", "."))
            except (TypeError, ValueError):
                continue
            if price <= 1.0:
                continue
            rows.append((match_id, bookmaker, market[0], market[1], price))

    saved = db.upsert_odds(rows)
    log.info("cote salvate: %d randuri pentru %d meciuri (%d nepotrivite)",
             saved, matched, len(unmatched))
    return {"meciuri": matched, "cote": saved, "nepotrivite": unmatched}


def import_csv(db: Database, content: str | bytes, *,
               bookmaker: str = DEFAULT_BOOKMAKER) -> dict[str, Any]:
    """Importa un CSV cu antet: gazda,oaspete[,data],1,X,2[,peste_2.5,sub_2.5]."""
    if isinstance(content, bytes):
        content = content.decode("utf-8-sig", errors="replace")
    frame = pd.read_csv(io.StringIO(content))
    frame.columns = [str(c).strip().lower() for c in frame.columns]
    return store_odds(db, frame.to_dict("records"), bookmaker=bookmaker)


def upcoming_template(db: Database, limit: int = 20) -> pd.DataFrame:
    """Tabel gol de completat, cu meciurile care urmeaza si cotele existente."""
    upcoming = db.upcoming_matches(limit=limit)
    if upcoming.empty:
        return pd.DataFrame(columns=["data", "gazda", "oaspete", "1", "X", "2",
                                     "peste_2.5", "sub_2.5"])

    existing = db.odds_df()
    known: dict[tuple[int, str, str], float] = {}
    if not existing.empty:
        for row in existing.itertuples():
            known[(int(row.match_id), row.market, row.selection)] = float(row.price)

    rows = []
    for match in upcoming.itertuples():
        match_id = int(match.match_id)
        rows.append({
            "data": str(match.utc_date)[:10],
            "gazda": match.home_team,
            "oaspete": match.away_team,
            "1": known.get((match_id, "1x2", "home")),
            "X": known.get((match_id, "1x2", "draw")),
            "2": known.get((match_id, "1x2", "away")),
            "peste_2.5": known.get((match_id, "ou_2.5", "over")),
            "sub_2.5": known.get((match_id, "ou_2.5", "under")),
        })
    return pd.DataFrame(rows)


def coverage(db: Database) -> dict[str, int]:
    """Cate meciuri viitoare au deja cote."""
    with db.connect() as conn:
        total = int(conn.execute(
            "SELECT COUNT(*) c FROM matches WHERE home_goals IS NULL"
            " AND status != 'finished'").fetchone()["c"])
        with_odds = int(conn.execute(
            "SELECT COUNT(DISTINCT o.match_id) c FROM odds o"
            " JOIN matches m ON m.match_id = o.match_id"
            " WHERE m.home_goals IS NULL").fetchone()["c"])
    return {"meciuri_viitoare": total, "cu_cote": with_odds}
