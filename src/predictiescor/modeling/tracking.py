"""Inregistrarea predictiilor INAINTE de meci si evaluarea lor dupa.

De ce conteaza: backtestul walk-forward e retrospectiv - reface istoria cu un
model reantrenat. Oricat de corect ar fi facut, ramane o simulare. Un jurnal
scris inainte de fluierul de start e o dovada de alt fel: la momentul X am spus
Y, iar apoi s-a intamplat Z. Nimic nu se poate ajusta ulterior.

Predictiile se salveaza o singura data per meci si piata: daca modelul se
reantreneaza intre timp, prima estimare ramane cea inregistrata, altfel
jurnalul si-ar pierde rostul.
"""
from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

from ..storage.db import Database
from .evaluate import (
    OUTCOMES, actual_outcome, brier_score, calibration_table, log_loss,
    ranked_probability_score,
)

log = logging.getLogger(__name__)

MODEL_NAME = "dixon_coles"

#: pietele inregistrate pentru fiecare meci
TRACKED_MARKETS = ("1X2", "total goluri 2.5", "ambele marcheaza")


def record_predictions(pipeline, db: Database | None = None, *,
                       limit: int = 60, overwrite: bool = False) -> int:
    """Salveaza predictiile pentru meciurile care urmeaza. Intoarce cate randuri."""
    db = db or Database()
    if not getattr(pipeline, "ready", False):
        return 0

    upcoming = db.upcoming_matches(limit=limit)
    if upcoming.empty:
        return 0

    existing = _already_recorded(db)
    rows: list[tuple] = []

    for match in upcoming.itertuples():
        match_id = int(match.match_id)
        if match_id in existing and not overwrite:
            continue
        try:
            prediction = pipeline.predict(match.home_team, match.away_team)
        except Exception as exc:  # noqa: BLE001
            log.debug("nu pot prezice %s: %s", match_id, exc)
            continue

        for market in TRACKED_MARKETS:
            book = prediction.markets.get(market)
            if not book:
                continue
            for selection, probability in book.items():
                rows.append((match_id, MODEL_NAME, market, selection,
                             float(probability)))

    saved = db.save_predictions(rows)
    if saved:
        log.info("am inregistrat %d predictii pentru %d meciuri",
                 saved, len({r[0] for r in rows}))
    return saved


def _already_recorded(db: Database) -> set[int]:
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT match_id FROM predictions WHERE model = ?",
            (MODEL_NAME,)).fetchall()
    return {int(r["match_id"]) for r in rows}


# ----------------------------------------------------------------------
def predictions_frame(db: Database | None = None) -> pd.DataFrame:
    """Predictiile inregistrate, alaturate rezultatului real (unde exista)."""
    db = db or Database()
    sql = """
        SELECT p.match_id, p.market, p.selection, p.probability, p.created_at,
               m.utc_date, m.home_goals, m.away_goals,
               ht.name AS home_team, at.name AS away_team
        FROM predictions p
        JOIN matches m ON m.match_id = p.match_id
        JOIN teams ht ON ht.team_id = m.home_team_id
        JOIN teams at ON at.team_id = m.away_team_id
        WHERE p.model = ?
        ORDER BY m.utc_date
    """
    with db.connect() as conn:
        return pd.read_sql_query(sql, conn, params=[MODEL_NAME])


def scoreboard(db: Database | None = None) -> dict[str, Any]:
    """Cat de bine s-au tinut predictiile inregistrate inainte de meci."""
    frame = predictions_frame(db)
    if frame.empty:
        return {"inregistrate": 0, "evaluate": 0}

    played = frame.dropna(subset=["home_goals", "away_goals"])
    if played.empty:
        return {"inregistrate": int(frame["match_id"].nunique()), "evaluate": 0}

    result: dict[str, Any] = {
        "inregistrate": int(frame["match_id"].nunique()),
        "evaluate": int(played["match_id"].nunique()),
    }

    outcomes = played[played["market"] == "1X2"]
    if not outcomes.empty:
        wide = outcomes.pivot_table(
            index=["match_id", "home_goals", "away_goals"],
            columns="selection", values="probability").reset_index()
        needed = [o for o in OUTCOMES if o in wide.columns]
        if len(needed) == 3:
            probs = wide[list(OUTCOMES)].to_numpy()
            actual = wide.apply(
                lambda r: actual_outcome(int(r.home_goals), int(r.away_goals)),
                axis=1)
            index = actual.map({o: i for i, o in enumerate(OUTCOMES)}).to_numpy()
            result["RPS"] = ranked_probability_score(probs, index)
            result["log loss"] = log_loss(probs, index)
            result["Brier"] = brier_score(probs, index)
            result["acuratete"] = float(
                np.mean(np.argmax(probs, axis=1) == index))
            result["calibrare"] = calibration_table(probs, index)
            result["detalii"] = _match_details(wide, actual)
    return result


def _match_details(wide: pd.DataFrame, actual: pd.Series) -> pd.DataFrame:
    frame = wide.copy()
    frame["rezultat"] = actual.values
    frame["p_rezultat"] = [
        row[row["rezultat"]] for _, row in frame.iterrows()]
    frame["scor"] = (frame["home_goals"].astype(int).astype(str) + "-"
                     + frame["away_goals"].astype(int).astype(str))
    frame["surpriza"] = -np.log2(frame["p_rezultat"].clip(lower=1e-6))
    return frame[["match_id", "scor", "rezultat", "p_rezultat", "surpriza"]]
