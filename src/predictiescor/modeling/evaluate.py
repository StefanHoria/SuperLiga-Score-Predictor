"""Evaluarea modelelor: backtest walk-forward, metrici si calibrare.

Regula de aur respectata aici: modelul nu vede niciodata un meci inainte sa il
prezica. Pentru fiecare meci din perioada de test, modelul e antrenat DOAR pe
meciurile jucate inaintea lui. Altfel metricile ar fi optimiste si inutile.

Metrici:
  * **RPS** (Ranked Probability Score) - standardul in fotbal; penalizeaza mai
    putin greselile "apropiate" (a zis egal, a iesit victorie gazda) decat cele
    grave. Mai mic = mai bine.
  * **Log loss** - penalizeaza dur increderea gresita.
  * **Brier** - eroare patratica pe probabilitati.
  * **Acuratete** - cat de des rezultatul cel mai probabil s-a si intamplat.
  * **ROI** fata de cotele reale, daca exista cote in baza de date.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np
import pandas as pd

from .dixon_coles import DixonColesModel
from .elo import EloModel
from .markets import result_1x2

log = logging.getLogger(__name__)

OUTCOMES = ("gazda", "egal", "oaspete")


def actual_outcome(home_goals: int, away_goals: int) -> str:
    if home_goals > away_goals:
        return "gazda"
    if home_goals < away_goals:
        return "oaspete"
    return "egal"


# ----------------------------------------------------------------------
# Metrici
# ----------------------------------------------------------------------
def ranked_probability_score(probs: np.ndarray, actual_index: np.ndarray) -> float:
    """RPS pentru rezultate ordonate (gazda < egal < oaspete)."""
    n, k = probs.shape
    outcomes = np.zeros_like(probs)
    outcomes[np.arange(n), actual_index] = 1.0
    cum_probs = np.cumsum(probs, axis=1)[:, :-1]
    cum_actual = np.cumsum(outcomes, axis=1)[:, :-1]
    return float(np.mean(np.sum((cum_probs - cum_actual) ** 2, axis=1) / (k - 1)))


def log_loss(probs: np.ndarray, actual_index: np.ndarray) -> float:
    n = probs.shape[0]
    picked = np.clip(probs[np.arange(n), actual_index], 1e-12, 1.0)
    return float(-np.mean(np.log(picked)))


def brier_score(probs: np.ndarray, actual_index: np.ndarray) -> float:
    n, _ = probs.shape
    outcomes = np.zeros_like(probs)
    outcomes[np.arange(n), actual_index] = 1.0
    return float(np.mean(np.sum((probs - outcomes) ** 2, axis=1)))


def accuracy(probs: np.ndarray, actual_index: np.ndarray) -> float:
    return float(np.mean(np.argmax(probs, axis=1) == actual_index))


def calibration_table(probs: np.ndarray, actual_index: np.ndarray,
                      bins: int = 10) -> pd.DataFrame:
    """Compara probabilitatea prezisa cu frecventa observata, pe intervale."""
    rows = []
    flat_probs = probs.reshape(-1)
    flat_actual = np.zeros_like(probs)
    flat_actual[np.arange(probs.shape[0]), actual_index] = 1.0
    flat_actual = flat_actual.reshape(-1)

    edges = np.linspace(0.0, 1.0, bins + 1)
    for lo, hi in zip(edges[:-1], edges[1:]):
        mask = (flat_probs >= lo) & (flat_probs < hi)
        if not mask.any():
            continue
        rows.append({
            "interval": f"{lo:.0%}-{hi:.0%}",
            "prezis_mediu": float(flat_probs[mask].mean()),
            "observat": float(flat_actual[mask].mean()),
            "n": int(mask.sum()),
        })
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------
# Backtest
# ----------------------------------------------------------------------
@dataclass
class BacktestResult:
    predictions: pd.DataFrame
    metrics: dict[str, float]
    calibration: pd.DataFrame
    n_refits: int = 0
    baseline: dict[str, float] = field(default_factory=dict)

    def summary_frame(self) -> pd.DataFrame:
        rows = [{"metrica": k, "model": v} for k, v in self.metrics.items()]
        df = pd.DataFrame(rows)
        if self.baseline:
            df["reper_cote"] = [self.baseline.get(k, np.nan)
                                for k in self.metrics]
        return df


def walk_forward_backtest(
    matches: pd.DataFrame,
    *,
    model_factory: Callable[[], DixonColesModel] | None = None,
    min_train_matches: int = 120,
    refit_every: int = 10,
    odds: pd.DataFrame | None = None,
    elo_weight: float = 0.0,
    progress: Callable[[int, int], None] | None = None,
) -> BacktestResult:
    """Antreneaza si testeaza cronologic, fara nicio scurgere de informatie."""
    data = (matches.dropna(subset=["home_goals", "away_goals"])
            .sort_values("date").reset_index(drop=True))
    if len(data) <= min_train_matches + 10:
        raise ValueError(
            f"prea putine meciuri pentru backtest: {len(data)} "
            f"(minim {min_train_matches + 10})")

    factory = model_factory or (lambda: DixonColesModel())
    records: list[dict[str, Any]] = []
    model: DixonColesModel | None = None
    elo: EloModel | None = None
    n_refits = 0
    total = len(data) - min_train_matches

    for position in range(min_train_matches, len(data)):
        if model is None or (position - min_train_matches) % refit_every == 0:
            train = data.iloc[:position]
            try:
                model = factory().fit(train, reference_date=data.iloc[position]["date"])
                if elo_weight > 0:
                    elo = EloModel().fit(train)
                n_refits += 1
            except Exception as exc:  # noqa: BLE001
                log.warning("reantrenare esuata la pozitia %d: %s", position, exc)
                continue
            if progress:
                progress(position - min_train_matches, total)

        row = data.iloc[position]
        # o echipa necunoscuta modelului primeste parametri neutri, deci
        # predictia ramane valida - dar o marcam ca sa poata fi filtrata
        known = (row["home_team"] in model.attack
                 and row["away_team"] in model.attack)
        matrix = model.score_matrix(row["home_team"], row["away_team"])
        probs = result_1x2(matrix)
        lam, mu = model.expected_goals(row["home_team"], row["away_team"])

        if elo is not None and elo_weight > 0:
            # amestec log-liniar (pooling logaritmic): media geometrica
            # ponderata a celor doua opinii, renormalizata
            elo_probs = elo.win_probabilities(row["home_team"], row["away_team"])
            blended = {
                key: (max(probs[key], 1e-9) ** (1 - elo_weight)
                      * max(elo_probs[key], 1e-9) ** elo_weight)
                for key in probs
            }
            # NB: nu refolosim numele `total` - e deja numarul de meciuri
            # din bucla de progres, iar suprascrierea lui ar strica bara.
            normaliser = sum(blended.values())
            probs = {k: v / normaliser for k, v in blended.items()}

        records.append({
            "match_id": row.get("match_id"),
            "date": row["date"],
            "season": row["season"],
            "home_team": row["home_team"],
            "away_team": row["away_team"],
            "home_goals": int(row["home_goals"]),
            "away_goals": int(row["away_goals"]),
            "rezultat": actual_outcome(int(row["home_goals"]),
                                       int(row["away_goals"])),
            "p_gazda": probs["gazda"],
            "p_egal": probs["egal"],
            "p_oaspete": probs["oaspete"],
            "xg_gazda": lam,
            "xg_oaspete": mu,
            "ambele_cunoscute": known,
        })

    predictions = pd.DataFrame(records)
    probs = predictions[["p_gazda", "p_egal", "p_oaspete"]].to_numpy()
    actual_index = predictions["rezultat"].map(
        {o: i for i, o in enumerate(OUTCOMES)}).to_numpy()

    metrics = {
        "RPS": ranked_probability_score(probs, actual_index),
        "log loss": log_loss(probs, actual_index),
        "Brier": brier_score(probs, actual_index),
        "acuratete": accuracy(probs, actual_index),
        "meciuri testate": float(len(predictions)),
    }

    baseline: dict[str, float] = {}
    if odds is not None and not odds.empty:
        baseline, predictions = _compare_with_odds(predictions, odds)

    return BacktestResult(
        predictions=predictions,
        metrics=metrics,
        calibration=calibration_table(probs, actual_index),
        n_refits=n_refits,
        baseline=baseline,
    )


def _compare_with_odds(predictions: pd.DataFrame,
                       odds: pd.DataFrame) -> tuple[dict[str, float], pd.DataFrame]:
    """Adauga probabilitatile implicite din cote si calculeaza reperul pietei."""
    book = odds[(odds["market"] == "1x2")].copy()
    if book.empty:
        return {}, predictions

    # preferam cotele medii de piata; daca lipsesc, luam orice casa
    preferred = book[book["bookmaker"] == "avg"]
    if preferred.empty:
        preferred = book[book["bookmaker"] == "pinnacle"]
    if preferred.empty:
        preferred = book

    wide = (preferred.pivot_table(index="match_id", columns="selection",
                                  values="price", aggfunc="mean")
            .rename(columns={"home": "cota_gazda", "draw": "cota_egal",
                             "away": "cota_oaspete"}))
    merged = predictions.merge(wide, on="match_id", how="left")

    have_odds = merged[["cota_gazda", "cota_egal", "cota_oaspete"]].notna().all(axis=1)
    if not have_odds.any():
        return {}, merged

    subset = merged[have_odds]
    implied = 1.0 / subset[["cota_gazda", "cota_egal", "cota_oaspete"]].to_numpy()
    implied = implied / implied.sum(axis=1, keepdims=True)  # scoatem marja
    actual_index = subset["rezultat"].map(
        {o: i for i, o in enumerate(OUTCOMES)}).to_numpy()

    baseline = {
        "RPS": ranked_probability_score(implied, actual_index),
        "log loss": log_loss(implied, actual_index),
        "Brier": brier_score(implied, actual_index),
        "acuratete": accuracy(implied, actual_index),
        "meciuri testate": float(len(subset)),
    }
    for i, name in enumerate(("piata_gazda", "piata_egal", "piata_oaspete")):
        merged.loc[have_odds, name] = implied[:, i]
    return baseline, merged


def value_bets(predictions: pd.DataFrame, *, edge: float = 0.05,
               stake: float = 1.0) -> pd.DataFrame:
    """Pariurile unde modelul da o probabilitate sensibil mai mare decat piata.

    Simuleaza miza fixa si raporteaza profitul - util ca test de realitate:
    daca ROI-ul e negativ pe termen lung, modelul nu bate piata.
    """
    needed = {"piata_gazda", "cota_gazda"}
    if not needed <= set(predictions.columns):
        return pd.DataFrame()

    rows = []
    mapping = [("gazda", "p_gazda", "piata_gazda", "cota_gazda"),
               ("egal", "p_egal", "piata_egal", "cota_egal"),
               ("oaspete", "p_oaspete", "piata_oaspete", "cota_oaspete")]

    for row in predictions.itertuples():
        for selection, p_col, market_col, odds_col in mapping:
            p_model = getattr(row, p_col, np.nan)
            p_market = getattr(row, market_col, np.nan)
            price = getattr(row, odds_col, np.nan)
            if not np.isfinite(p_market) or not np.isfinite(price):
                continue
            if p_model - p_market < edge:
                continue
            won = row.rezultat == selection
            profit = stake * (price - 1.0) if won else -stake
            rows.append({
                "date": row.date, "meci": f"{row.home_team} - {row.away_team}",
                "selectie": selection, "p_model": p_model, "p_piata": p_market,
                "cota": price, "avantaj": p_model - p_market,
                "castigat": won, "profit": profit,
            })

    df = pd.DataFrame(rows)
    if not df.empty:
        df["profit_cumulat"] = df["profit"].cumsum()
    return df


def roi_summary(bets: pd.DataFrame, stake: float = 1.0) -> dict[str, float]:
    if bets.empty:
        return {"pariuri": 0, "profit": 0.0, "ROI %": 0.0, "rata_reusita %": 0.0}
    return {
        "pariuri": float(len(bets)),
        "profit": float(bets["profit"].sum()),
        "ROI %": float(100.0 * bets["profit"].sum() / (len(bets) * stake)),
        "rata_reusita %": float(100.0 * bets["castigat"].mean()),
    }
