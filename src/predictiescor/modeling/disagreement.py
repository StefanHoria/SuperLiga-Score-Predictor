"""Cand modelul nu e de acord cu piata, cine are dreptate?

Intrebarea de fond a biletelor e "are logica de value vreun avantaj?". Pe
istoricul de bilete raspunsul nu se poate da: cateva sute de ponturi sunt
dominate de noroc. Dar exact aceeasi intrebare se poate pune pe TOATE meciurile
cu cote de inchidere - de cateva ori mai multa informatie, pentru acelasi lucru.

Trei masuratori, fiecare raspunzand la altceva:

    tabelul de dezacord  unde se abate modelul de piata, si cine pierde acolo
    duelul favoritilor   cand aleg favoriti diferiti, cine nimereste mai des
    ROI cu interval      profitul simulat, cu marja lui de eroare

Ultima e cea care conteaza, si tocmai ea lipsea: un ROI de +8% pe 60 de pariuri
nu inseamna nimic daca intervalul de incredere e [-25%, +40%]. Fara interval,
citim zgomot drept avantaj.

O nota despre bootstrap: pariurile de pe acelasi meci NU sunt independente
(daca gazda castiga, pontul pe "egal" pica automat), asa ca reesantionam
MECIURI intregi, nu pariuri individuale. Altfel intervalul iese fals de ingust.
"""
from __future__ import annotations

import logging
from typing import Any, Sequence

import numpy as np
import pandas as pd

from .evaluate import OUTCOMES, ranked_probability_score

log = logging.getLogger(__name__)

MODEL_COLUMNS = ["p_gazda", "p_egal", "p_oaspete"]
MARKET_COLUMNS = ["piata_gazda", "piata_egal", "piata_oaspete"]

#: pragurile implicite pentru marimea dezacordului (distanta de variatie totala)
DEFAULT_BINS: tuple[float, ...] = (0.0, 0.03, 0.06, 0.10, 1.0)


def _usable(predictions: pd.DataFrame) -> pd.DataFrame:
    """Doar meciurile care au si predictie, si cote de piata."""
    if predictions.empty or not set(MARKET_COLUMNS) <= set(predictions.columns):
        return pd.DataFrame()
    have = predictions[MARKET_COLUMNS].notna().all(axis=1)
    return predictions[have].copy()


def total_variation(predictions: pd.DataFrame) -> pd.Series:
    """Cat de departe e modelul de piata, pe o scara de la 0 la 1.

    Distanta de variatie totala: jumatate din suma diferentelor absolute. 0
    inseamna acord perfect; 0.10 inseamna ca modelul muta zece puncte de
    probabilitate dintr-un rezultat in altul fata de piata.
    """
    model = predictions[MODEL_COLUMNS].to_numpy(dtype=float)
    market = predictions[MARKET_COLUMNS].to_numpy(dtype=float)
    return pd.Series(0.5 * np.abs(model - market).sum(axis=1),
                     index=predictions.index)


def _rps(frame: pd.DataFrame, columns: list[str]) -> float:
    actual = frame["rezultat"].map({o: i for i, o in enumerate(OUTCOMES)})
    return ranked_probability_score(frame[columns].to_numpy(dtype=float),
                                    actual.to_numpy())


def disagreement_table(predictions: pd.DataFrame,
                       bins: Sequence[float] = DEFAULT_BINS) -> pd.DataFrame:
    """RPS-ul modelului si al pietei, pe felii de dezacord tot mai mare.

    Daca modelul are informatie in plus, ar trebui sa castige teren tocmai
    acolo unde se abate mai mult. Daca in schimb pierde cu cat se abate mai
    mult, abaterile lui sunt eroare, nu informatie - si atunci ponturile de
    value pariaza exact pe greselile modelului.
    """
    data = _usable(predictions)
    if data.empty:
        return pd.DataFrame()

    data["dezacord"] = total_variation(data)
    labels = [f"{lo:.0%}-{hi:.0%}" if hi < 1 else f"peste {lo:.0%}"
              for lo, hi in zip(bins[:-1], bins[1:])]
    data["felie"] = pd.cut(data["dezacord"], bins=list(bins), labels=labels,
                           include_lowest=True)

    rows: list[dict[str, Any]] = []
    for label in labels:
        chunk = data[data["felie"] == label]
        if len(chunk) < 2:
            continue
        model_rps = _rps(chunk, MODEL_COLUMNS)
        market_rps = _rps(chunk, MARKET_COLUMNS)
        rows.append({
            "dezacord": label,
            "meciuri": len(chunk),
            "RPS model": model_rps,
            "RPS piață": market_rps,
            # pozitiv = modelul e mai bun pe felia asta
            "avantaj model": market_rps - model_rps,
        })
    return pd.DataFrame(rows)


def favourite_duel(predictions: pd.DataFrame) -> dict[str, Any]:
    """Cand modelul si piata aleg favoriti diferiti, cine nimereste mai des?

    E cel mai direct test posibil, si e imun la calibrare: nu conteaza cat de
    increzator e fiecare, ci doar pe cine pariaza.
    """
    data = _usable(predictions)
    if data.empty:
        return {"meciuri": 0}

    model_pick = data[MODEL_COLUMNS].to_numpy().argmax(axis=1)
    market_pick = data[MARKET_COLUMNS].to_numpy().argmax(axis=1)
    actual = data["rezultat"].map(
        {o: i for i, o in enumerate(OUTCOMES)}).to_numpy()

    differ = model_pick != market_pick
    if not differ.any():
        return {"meciuri": 0}

    model_right = int((model_pick[differ] == actual[differ]).sum())
    market_right = int((market_pick[differ] == actual[differ]).sum())
    n = int(differ.sum())
    return {
        "meciuri": n,
        "model a avut dreptate": model_right,
        "piața a avut dreptate": market_right,
        "niciunul": n - model_right - market_right,
        "rata model %": 100.0 * model_right / n,
        "rata piață %": 100.0 * market_right / n,
    }


# ----------------------------------------------------------------------
def bootstrap_roi(bets: pd.DataFrame, *, samples: int = 5000,
                  confidence: float = 0.90,
                  seed: int = 20260831) -> dict[str, Any]:
    """Intervalul de incredere pentru ROI, prin reesantionare pe MECIURI.

    Pariurile de pe acelasi meci se influenteaza reciproc, deci reesantionam
    meciuri intregi. Reesantionarea pe pariuri ar da un interval fals de
    ingust si ne-ar face sa credem intr-un avantaj inexistent.
    """
    if bets.empty:
        return {"pariuri": 0}

    frame = bets.reset_index(drop=True)
    if "meci" in frame.columns:
        groups = [np.atleast_1d(np.asarray(idx))
                  for idx in frame.groupby("meci").indices.values()]
    else:
        groups = [np.array([i]) for i in range(len(frame))]

    profit = frame["profit"].to_numpy(dtype=float)
    rng = np.random.default_rng(seed)
    n_groups = len(groups)

    rois = np.empty(samples)
    for i in range(samples):
        picked = rng.integers(0, n_groups, size=n_groups)
        index = np.concatenate([groups[p] for p in picked])
        rois[i] = 100.0 * profit[index].mean()

    lo = float(np.quantile(rois, (1 - confidence) / 2))
    hi = float(np.quantile(rois, 1 - (1 - confidence) / 2))
    return {
        "pariuri": int(len(frame)),
        "meciuri": n_groups,
        "ROI %": float(100.0 * profit.mean()),
        "interval jos %": lo,
        "interval sus %": hi,
        "încredere": confidence,
        # daca intervalul contine zero, avantajul nu e distinct de noroc
        "distinct de zero": bool(lo > 0 or hi < 0),
    }


def edge_sweep(predictions: pd.DataFrame,
               edges: Sequence[float] = (0.02, 0.04, 0.06, 0.08, 0.10),
               *, samples: int = 2000) -> pd.DataFrame:
    """ROI pe mai multe praguri de avantaj, fiecare cu intervalul lui.

    Un avantaj real ar trebui sa creasca odata cu pragul: cu cat cerem o
    diferenta mai mare fata de piata, cu atat pariurile ramase ar trebui sa
    fie mai bune. Un profil plat sau haotic e semn de zgomot.

    ATENTIE la interpretare: sunt mai multe praguri incercate deodata, deci
    cel mai bun dintre ele arata mereu mai bine decat merita. Nu alege pragul
    dupa tabelul asta - foloseste tabelul ca sa vezi FORMA, nu varful.
    """
    from .evaluate import value_bets

    rows: list[dict[str, Any]] = []
    for edge in edges:
        bets = value_bets(predictions, edge=edge)
        if bets.empty:
            rows.append({"prag": edge, "pariuri": 0})
            continue
        stats = bootstrap_roi(bets, samples=samples)
        rows.append({
            "prag": edge,
            "pariuri": stats["pariuri"],
            "ROI %": stats["ROI %"],
            "interval jos %": stats["interval jos %"],
            "interval sus %": stats["interval sus %"],
            "rata reușită %": 100.0 * float(bets["castigat"].mean()),
            "distinct de zero": stats["distinct de zero"],
        })
    return pd.DataFrame(rows)


def verdict(duel: dict[str, Any], sweep: pd.DataFrame) -> str:
    """O propozitie in romana despre ce arata masuratorile."""
    if not duel.get("meciuri"):
        return "Nu sunt destule meciuri cu cote pentru o concluzie."

    any_significant = (not sweep.empty and "distinct de zero" in sweep.columns
                       and bool(sweep["distinct de zero"].any()))
    better = duel["rata model %"] > duel["rata piață %"]

    if any_significant and better:
        return ("Modelul pare să aibă un avantaj real: bate piața pe "
                "dezacorduri, iar ROI-ul se distinge de zero.")
    if better:
        return ("Modelul nimerește mai des decât piața pe dezacorduri, dar "
                "ROI-ul NU se distinge de zero — deci încă poate fi noroc.")
    return ("Piața are dreptate mai des decât modelul acolo unde nu sunt de "
            "acord. Ponturile de value pariază pe erorile modelului, nu pe "
            "informație în plus.")
