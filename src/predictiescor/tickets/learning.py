"""Invatarea din biletele deja decontate.

Doua lucruri se invata, amandoua din date reale, nu din presupuneri:

1. **Calibrarea pe piata.** Daca pe piata "cornere" modelul spune 70% dar se
   intampla in 55% din cazuri, e prea increzator acolo. Comparam prezis cu
   observat pe intervale de probabilitate si construim o corectie. Corectia e
   aplicata la generarea urmatoarelor bilete, deci biletele devin mai bune pe
   masura ce se aduna rezultate.

2. **Greutatea strategiilor.** Strategiile care aduc profit sunt propuse mai
   des, cele care pierd constant - mai rar. Greutatea nu ajunge niciodata zero:
   pastram o urma din fiecare, ca sa nu ne blocam pe o concluzie trasa din
   putine bilete.

Amandoua folosesc shrinkage: cu putine observatii, corectia e mica. Fara asta,
zece bilete norocoase ar rasuci modelul complet.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

import numpy as np
import pandas as pd

from .settlement import LOST, VOID, WON, all_legs_frame

log = logging.getLogger(__name__)

#: intervalele pe care masuram calibrarea
BUCKETS = [(0.0, 0.2), (0.2, 0.35), (0.35, 0.5), (0.5, 0.65),
           (0.65, 0.8), (0.8, 1.0)]

#: cate observatii "fictive" la probabilitatea prezisa (shrinkage)
PRIOR_STRENGTH = 12.0

#: minim de bilete pentru ca o strategie sa isi schimbe greutatea
MIN_TICKETS_FOR_WEIGHT = 8


def bucket_of(probability: float) -> str:
    for low, high in BUCKETS:
        if low <= probability < high:
            return f"{low:.0%}-{high:.0%}"
    return f"{BUCKETS[-1][0]:.0%}-{BUCKETS[-1][1]:.0%}"


# ----------------------------------------------------------------------
@dataclass
class Calibrator:
    """Corectia invatata, per (piata, interval de probabilitate)."""

    table: dict[tuple[str, str], tuple[float, float, int]] = field(
        default_factory=dict)

    def __call__(self, market: str, probability: float) -> float:
        """Aplica corectia. Fara date suficiente, intoarce valoarea originala."""
        entry = self.table.get((market, bucket_of(probability)))
        if entry is None:
            return probability
        predicted, observed, n = entry
        if n <= 0 or predicted <= 0:
            return probability
        # shrinkage catre "fara corectie" cand avem putine observatii
        strength = n / (n + PRIOR_STRENGTH)
        ratio = observed / predicted
        adjusted = probability * (1.0 + strength * (ratio - 1.0))
        return float(np.clip(adjusted, 0.01, 0.99))

    def as_frame(self) -> pd.DataFrame:
        rows = [
            {"piață": market, "interval": bucket, "prezis": predicted,
             "observat": observed, "n": n,
             "corecție": (observed / predicted) if predicted else 1.0}
            for (market, bucket), (predicted, observed, n) in self.table.items()
        ]
        if not rows:
            return pd.DataFrame()
        return pd.DataFrame(rows).sort_values(["piață", "interval"])


def learn_calibration(db, *, persist: bool = True) -> Calibrator:
    """Recalculeaza calibrarea pe piete din toate picioarele decontate."""
    legs = all_legs_frame(db, settled_only=True)
    calibrator = Calibrator()
    if legs.empty:
        return calibrator

    legs = legs[legs["result"].isin([WON, LOST])].copy()
    if legs.empty:
        return calibrator

    legs["castigat"] = (legs["result"] == WON).astype(float)
    legs["interval"] = legs["probability"].map(bucket_of)

    grouped = legs.groupby(["market", "interval"]).agg(
        prezis=("probability", "mean"),
        observat=("castigat", "mean"),
        n=("castigat", "size"),
    ).reset_index()

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for row in grouped.itertuples():
        calibrator.table[(row.market, row.interval)] = (
            float(row.prezis), float(row.observat), int(row.n))

    if persist:
        with db.connect() as conn:
            for row in grouped.itertuples():
                conn.execute(
                    "INSERT INTO market_calibration(market, bucket, predicted,"
                    " observed, n, updated_at) VALUES (?,?,?,?,?,?)"
                    " ON CONFLICT(market, bucket) DO UPDATE SET"
                    " predicted=excluded.predicted, observed=excluded.observed,"
                    " n=excluded.n, updated_at=excluded.updated_at",
                    (row.market, row.interval, float(row.prezis),
                     float(row.observat), int(row.n), now),
                )
    log.info("calibrare invatata din %d picioare, %d combinatii",
             len(legs), len(grouped))
    return calibrator


def load_calibration(db) -> Calibrator:
    """Incarca ultima calibrare salvata (rapid, fara recalcul)."""
    calibrator = Calibrator()
    with db.connect() as conn:
        rows = conn.execute("SELECT * FROM market_calibration").fetchall()
    for row in rows:
        calibrator.table[(str(row["market"]), str(row["bucket"]))] = (
            float(row["predicted"]), float(row["observed"]), int(row["n"]))
    return calibrator


# ----------------------------------------------------------------------
def update_strategy_weights(db) -> pd.DataFrame:
    """Recalculeaza scorul si greutatea fiecarei strategii."""
    with db.connect() as conn:
        tickets = pd.read_sql_query(
            "SELECT * FROM tickets WHERE status IN ('castigat','pierdut')", conn)
    if tickets.empty:
        return pd.DataFrame()

    legs = all_legs_frame(db, settled_only=True)
    legs = legs[legs["result"].isin([WON, LOST])]

    rows = []
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for strategy, group in tickets.groupby("strategy"):
        staked = float(group["stake"].sum())
        returned = float(group["payout"].fillna(0).sum())
        won = int((group["status"] == "castigat").sum())
        n = len(group)
        roi = (returned - staked) / staked if staked else 0.0

        strategy_legs = legs[legs["strategy"] == strategy]
        brier = float(((strategy_legs["probability"]
                        - (strategy_legs["result"] == WON).astype(float)) ** 2
                       ).mean()) if not strategy_legs.empty else float("nan")

        # Greutate: 1.0 e neutru. Se misca proportional cu ROI, dar cu
        # shrinkage dupa numarul de bilete si limitata la [0.35, 2.0].
        if n >= MIN_TICKETS_FOR_WEIGHT:
            confidence = n / (n + 20.0)
            weight = float(np.clip(1.0 + confidence * roi, 0.35, 2.0))
        else:
            weight = 1.0

        rows.append({
            "strategy": strategy, "tickets": n, "won": won, "staked": staked,
            "returned": returned, "roi": roi, "brier": brier, "weight": weight,
            "legs_total": len(strategy_legs),
            "legs_won": int((strategy_legs["result"] == WON).sum()),
        })

    with db.connect() as conn:
        for row in rows:
            conn.execute(
                "INSERT INTO strategy_performance(strategy, tickets, won,"
                " staked, returned, legs_total, legs_won, brier_sum, weight,"
                " updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(strategy) DO UPDATE SET tickets=excluded.tickets,"
                " won=excluded.won, staked=excluded.staked,"
                " returned=excluded.returned, legs_total=excluded.legs_total,"
                " legs_won=excluded.legs_won, brier_sum=excluded.brier_sum,"
                " weight=excluded.weight, updated_at=excluded.updated_at",
                (row["strategy"], row["tickets"], row["won"], row["staked"],
                 row["returned"], row["legs_total"], row["legs_won"],
                 0.0 if pd.isna(row["brier"]) else row["brier"],
                 row["weight"], now),
            )

    frame = pd.DataFrame(rows).sort_values("roi", ascending=False)
    log.info("greutati actualizate pentru %d strategii", len(frame))
    return frame


def strategy_performance(db) -> pd.DataFrame:
    with db.connect() as conn:
        frame = pd.read_sql_query(
            "SELECT * FROM strategy_performance ORDER BY weight DESC", conn)
    if frame.empty:
        return frame
    frame["ROI %"] = np.where(
        frame["staked"] > 0,
        100 * (frame["returned"] - frame["staked"]) / frame["staked"], 0.0)
    frame["rată bilete %"] = np.where(
        frame["tickets"] > 0, 100 * frame["won"] / frame["tickets"], 0.0)
    frame["rată picioare %"] = np.where(
        frame["legs_total"] > 0, 100 * frame["legs_won"] / frame["legs_total"], 0.0)
    return frame


# ----------------------------------------------------------------------
def accuracy_report(db) -> dict[str, pd.DataFrame]:
    """Acuratetea pe piete si in timp - ce se afiseaza pe pagina de bilete."""
    legs = all_legs_frame(db, settled_only=True)
    out: dict[str, pd.DataFrame] = {}
    if legs.empty:
        return out

    decided = legs[legs["result"].isin([WON, LOST])].copy()
    if decided.empty:
        return out
    decided["castigat"] = (decided["result"] == WON).astype(float)

    by_market = decided.groupby("market").agg(
        picioare=("castigat", "size"),
        castigate=("castigat", "sum"),
        prezis=("probability", "mean"),
        observat=("castigat", "mean"),
    ).reset_index()
    by_market["diferență"] = by_market["observat"] - by_market["prezis"]
    by_market["Brier"] = [
        float(((group["probability"] - group["castigat"]) ** 2).mean())
        for _, group in decided.groupby("market")
    ]
    out["pe_piata"] = by_market.sort_values("picioare", ascending=False)

    with db.connect() as conn:
        tickets = pd.read_sql_query(
            "SELECT * FROM tickets WHERE status IN ('castigat','pierdut')", conn)
    if not tickets.empty:
        by_day = tickets.groupby("match_day").agg(
            bilete=("ticket_id", "size"),
            castigate=("status", lambda s: int((s == "castigat").sum())),
            miza=("stake", "sum"),
            incasat=("payout", "sum"),
        ).reset_index()
        by_day["profit"] = by_day["incasat"] - by_day["miza"]
        by_day["profit cumulat"] = by_day["profit"].cumsum()
        out["pe_zi"] = by_day

    # numarul de picioare anulate: semnaleaza lipsa de date, nu greseli
    voided = legs[legs["result"] == VOID]
    if not voided.empty:
        out["anulate"] = voided.groupby("market").size().reset_index(
            name="picioare anulate")
    return out


def overall_summary(db) -> dict[str, float]:
    with db.connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) n, SUM(status='castigat') w, SUM(stake) s,"
            " SUM(COALESCE(payout,0)) p FROM tickets"
            " WHERE status IN ('castigat','pierdut')").fetchone()
    n = int(row["n"] or 0)
    if n == 0:
        return {"bilete": 0, "castigate": 0, "rata %": 0.0, "profit": 0.0,
                "ROI %": 0.0}
    staked = float(row["s"] or 0)
    payout = float(row["p"] or 0)
    return {
        "bilete": n,
        "castigate": int(row["w"] or 0),
        "rata %": 100.0 * float(row["w"] or 0) / n,
        "profit": payout - staked,
        "ROI %": (100.0 * (payout - staked) / staked) if staked else 0.0,
    }
