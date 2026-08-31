"""Rezultate si statistici neasteptate.

"Neasteptat" nu inseamna "scor mare" - inseamna *improbabil dupa model*.
Masuram surpriza cu **surprinderea informationala**: -log2(p), unde p este
probabilitatea pe care modelul o daduse rezultatului care s-a intamplat.

    p = 50%  ->  1 bit  (banal)
    p = 25%  ->  2 biti
    p = 10%  ->  3.3 biti (mare surpriza)

Pentru statistici (cornere, cartonase, suturi) folosim abaterea standardizata
fata de valoarea asteptata de model.
"""
from __future__ import annotations

import logging
import math
from typing import Any

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)


def _surprise_bits(probability: float) -> float:
    probability = max(1e-6, min(1.0, float(probability)))
    return -math.log2(probability)


def surprise_label(bits: float) -> str:
    if bits >= 3.3:
        return "🚨 șoc"
    if bits >= 2.3:
        return "❗ mare surpriză"
    if bits >= 1.7:
        return "⚠️ surpriză"
    return "normal"


# ----------------------------------------------------------------------
def surprising_results(pipeline, matches: pd.DataFrame, *,
                       limit: int = 25) -> pd.DataFrame:
    """Meciurile in care rezultatul a contrazis cel mai tare modelul.

    Modelul folosit este cel antrenat pe TOT istoricul, deci pentru meciurile
    vechi are avantajul de a le fi vazut. Coloana e utila pentru a scoate in
    evidenta anomaliile, nu ca masura de performanta - pentru asta exista
    backtestul walk-forward din pagina "Performanța modelului".
    """
    if not pipeline.ready or matches.empty:
        return pd.DataFrame()

    played = matches.dropna(subset=["home_goals", "away_goals"])
    rows: list[dict[str, Any]] = []

    for row in played.itertuples():
        try:
            prediction = pipeline.predict(row.home_team, row.away_team)
        except Exception:  # noqa: BLE001
            continue

        home_goals, away_goals = int(row.home_goals), int(row.away_goals)
        outcome = ("gazda" if home_goals > away_goals
                   else "oaspete" if away_goals > home_goals else "egal")
        p_outcome = prediction.markets["1X2"][outcome]

        matrix = prediction.matrix
        if home_goals < matrix.shape[0] and away_goals < matrix.shape[1]:
            p_score = float(matrix[home_goals, away_goals])
        else:
            p_score = 1e-6

        bits = _surprise_bits(p_outcome)
        rows.append({
            "data": row.date.strftime("%d.%m.%Y"),
            "meci": f"{row.home_team} — {row.away_team}",
            "scor": f"{home_goals}-{away_goals}",
            "rezultat": outcome,
            "șansa dată": p_outcome,
            "șansa scorului": p_score,
            "surpriză (biți)": round(bits, 2),
            "verdict": surprise_label(bits),
            "goluri așteptate": round(prediction.expected_total_goals, 2),
            "goluri reale": home_goals + away_goals,
        })

    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    return (frame.sort_values("surpriză (biți)", ascending=False)
            .head(limit).reset_index(drop=True))


# ----------------------------------------------------------------------
def surprising_stats(pipeline, team_stats: pd.DataFrame, *,
                     limit: int = 25, min_z: float = 2.0) -> pd.DataFrame:
    """Statisticile de echipa cele mai departe de asteptarea modelului."""
    secondary = getattr(pipeline, "secondary", None)
    if secondary is None or not secondary.models or team_stats.empty:
        return pd.DataFrame()

    rows: list[dict[str, Any]] = []
    for stat, model in secondary.models.items():
        if stat not in team_stats.columns:
            continue
        subset = team_stats.dropna(subset=[stat])
        if subset.empty:
            continue

        # abaterea tipica a acestei statistici, pentru standardizare
        spread = float(subset[stat].std()) or 1.0
        for row in subset.itertuples():
            expected = model.expected(row.team, row.opponent, bool(row.is_home))
            actual = float(getattr(row, stat))
            z = (actual - expected) / spread
            if abs(z) < min_z:
                continue
            rows.append({
                "data": row.date.strftime("%d.%m.%Y") if pd.notna(row.date) else "",
                "echipă": row.team,
                "adversar": row.opponent,
                "acasă": "da" if row.is_home else "nu",
                "statistică": stat,
                "așteptat": round(expected, 1),
                "real": round(actual, 1),
                "abatere (σ)": round(z, 2),
                "verdict": "mult peste așteptări" if z > 0 else "mult sub așteptări",
            })

    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    frame["abs"] = frame["abatere (σ)"].abs()
    return (frame.sort_values("abs", ascending=False)
            .drop(columns=["abs"]).head(limit).reset_index(drop=True))


# ----------------------------------------------------------------------
def surprising_legs(db, *, limit: int = 25) -> pd.DataFrame:
    """Picioarele de bilet care au picat desi pareau sigure (si invers)."""
    from .settlement import LOST, WON, all_legs_frame

    legs = all_legs_frame(db, settled_only=True)
    if legs.empty:
        return pd.DataFrame()
    legs = legs[legs["result"].isin([WON, LOST])].copy()
    if legs.empty:
        return pd.DataFrame()

    legs["p_realizat"] = np.where(
        legs["result"] == WON, legs["probability"], 1.0 - legs["probability"])
    legs["surpriză (biți)"] = legs["p_realizat"].map(_surprise_bits).round(2)
    legs["verdict"] = legs["surpriză (biți)"].map(surprise_label)

    display = legs.rename(columns={
        "match_day": "ziua", "market": "piață", "description": "pontul",
        "probability": "șansa dată", "result": "rezultat",
        "actual_value": "ce s-a întâmplat", "strategy": "strategie"})
    columns = ["ziua", "strategie", "piață", "pontul", "șansa dată",
               "rezultat", "ce s-a întâmplat", "surpriză (biți)", "verdict"]
    return (display.sort_values("surpriză (biți)", ascending=False)
            [columns].head(limit).reset_index(drop=True))


def team_form_anomalies(pipeline, matches: pd.DataFrame,
                        *, window: int = 5) -> pd.DataFrame:
    """Echipe a caror forma recenta se abate mult de la valoarea lor de bază."""
    if not pipeline.ready or matches.empty:
        return pd.DataFrame()

    played = matches.dropna(subset=["home_goals", "away_goals"]).sort_values("date")
    rows = []
    for team in pipeline.teams:
        subset = played[(played["home_team"] == team)
                        | (played["away_team"] == team)].tail(window)
        if len(subset) < window:
            continue

        scored = np.where(subset["home_team"] == team,
                          subset["home_goals"], subset["away_goals"]).mean()
        conceded = np.where(subset["home_team"] == team,
                            subset["away_goals"], subset["home_goals"]).mean()

        expected_scored = math.exp(pipeline.goals.attack.get(team, 0.0))
        expected_conceded = math.exp(pipeline.goals.defence.get(team, 0.0))

        rows.append({
            "echipă": team,
            f"goluri marcate (ultimele {window})": round(float(scored), 2),
            "așteptat": round(expected_scored, 2),
            "diferență atac": round(float(scored) - expected_scored, 2),
            f"goluri primite (ultimele {window})": round(float(conceded), 2),
            "așteptat primite": round(expected_conceded, 2),
            "diferență apărare": round(expected_conceded - float(conceded), 2),
        })

    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    frame["abatere totală"] = (frame["diferență atac"].abs()
                               + frame["diferență apărare"].abs())
    return frame.sort_values("abatere totală", ascending=False).reset_index(drop=True)
