"""Efectul arbitrului asupra cartonaselor - masurat, NEAPLICAT.

Ideea e plauzibila si se aude des: unii arbitri scot cartonase mai usor decat
altii. Daca ar fi asa, piata de cartonase ar avea de castigat dintr-un ajustaj
per arbitru. Am construit estimatorul si l-am masurat. Verdictul:

    NU exista dovada suficienta in datele noastre. Codul ramane, dar nu e
    legat de predictii.

Masurat pe 31 august 2026, pe 155 de meciuri cu arbitru cunoscut SI statistici
de cartonase pentru ambele echipe, 27 de arbitri, mediana 5 meciuri per arbitru:

    model                              MAE      RMSE
    doar media ligii (reper)         1.9419    2.3791
    arbitru, fara shrinkage          1.9646    2.4387     <- mai rau
    arbitru, shrinkage k=5           1.9012    2.3483
    arbitru, shrinkage k=10          1.8972    2.3442     <- cel mai bun
    arbitru, shrinkage k=20          1.9097    2.3498

Castigul la k=10 pare real (-2.3% din eroare), dar nu rezista la verificare:

    castig MAE  +0.0447, interval de incredere 90%  [-0.0080, +0.0968]
    test de permutare  p = 0.059

Intervalul contine zero. Iar `k=10` a fost ales uitandu-ne la aceleasi
rezultate pe care le judecam, deci si p-ul de 0.059 e optimist - adevaratul e
mai slab. In plus, descompunerea variantei arata acelasi lucru mai direct:

    varianta INTRE arbitri                       1.258
    varianta asteptata doar din intamplare       1.475
    => semnal real estimat                       0.000

Varianta dintre arbitri e mai MICA decat cea pe care ar produce-o pura
intamplare. Cu 5 meciuri de arbitru, mediile per arbitru sunt aproape numai
zgomot.

De ce lasam codul aici: masuratoarea merita refacuta cand datele cresc. Cu 500
de meciuri si 15+ meciuri per arbitru, acelasi test ar avea putere reala.
Ruleaza `referee_effect()` si `evaluate_referee_effect()` din nou atunci.
"""
from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

#: cate meciuri fictive de medie a ligii adaugam fiecarui arbitru
DEFAULT_SHRINKAGE = 10.0


def referee_matches(db) -> pd.DataFrame:
    """Meciurile cu arbitru cunoscut si cartonase pentru ambele echipe."""
    sql = """
        SELECT m.match_id, m.utc_date, m.season, m.referee,
               SUM(s.yellow_cards) AS galbene,
               SUM(COALESCE(s.red_cards, 0)) AS rosii,
               COUNT(*) AS randuri
        FROM matches m
        JOIN match_team_stats s ON s.match_id = m.match_id
        WHERE m.referee IS NOT NULL AND m.referee != ''
          AND s.yellow_cards IS NOT NULL
        GROUP BY m.match_id
        HAVING randuri = 2
        ORDER BY m.utc_date
    """
    with db.connect() as conn:
        return pd.read_sql_query(sql, conn)


def referee_effect(matches: pd.DataFrame, *, column: str = "galbene",
                   shrinkage: float = DEFAULT_SHRINKAGE) -> dict[str, float]:
    """Media de cartonase a fiecarui arbitru, trasa spre media ligii.

    `shrinkage` e numarul de meciuri fictive de medie a ligii adaugate fiecarui
    arbitru. Cu putine meciuri per arbitru, fara shrinkage estimarile sunt
    aproape numai zgomot - se vede si in masuratoarea din capul fisierului,
    unde varianta fara shrinkage iese mai rea decat simpla medie a ligii.
    """
    if matches.empty:
        return {}
    league = float(matches[column].mean())
    out: dict[str, float] = {}
    for name, group in matches.groupby("referee"):
        total, n = float(group[column].sum()), len(group)
        out[str(name)] = (total + shrinkage * league) / (n + shrinkage)
    return out


def variance_decomposition(matches: pd.DataFrame,
                           *, column: str = "galbene") -> dict[str, float]:
    """Cat din diferenta dintre arbitri e semnal si cat e pura intamplare.

    Daca varianta dintre arbitri nu o depaseste pe cea pe care ar produce-o
    esantionarea, nu avem ce modela.
    """
    if matches.empty or matches["referee"].nunique() < 2:
        return {}

    values = matches[column].to_numpy(dtype=float)
    grand = float(values.mean())
    means = matches.groupby("referee")[column].mean()
    counts = matches.groupby("referee")[column].count()

    between = float((((means - grand) ** 2) * counts).sum() / counts.sum())
    within = float(matches.groupby("referee")[column]
                   .transform(lambda g: g - g.mean()).pow(2).mean())
    noise = within * float((1.0 / counts).mean())
    return {
        "varianță între arbitri": between,
        "varianță în interiorul unui arbitru": within,
        "așteptată din întâmplare": noise,
        "semnal estimat": max(0.0, between - noise),
    }


def evaluate_referee_effect(matches: pd.DataFrame, *, column: str = "galbene",
                            shrinkage: float = DEFAULT_SHRINKAGE,
                            samples: int = 5000,
                            seed: int = 20260831) -> dict[str, Any]:
    """Ajuta arbitrul la prezis cartonasele? Leave-one-out, cu interval.

    Comparam doua predictii pentru fiecare meci, amandoua estimate FARA meciul
    respectiv: media ligii, si media arbitrului trasa spre ea. Diferenta de
    eroare absoluta primeste un interval prin reesantionare.
    """
    if len(matches) < 20:
        return {"meciuri": len(matches)}

    values = matches[column].to_numpy(dtype=float)
    referees = matches["referee"].to_numpy()
    n = len(values)

    baseline = np.empty(n)
    model = np.empty(n)
    for i in range(n):
        mask = np.ones(n, dtype=bool)
        mask[i] = False
        others, other_refs = values[mask], referees[mask]
        league = float(others.mean())
        baseline[i] = league
        same = others[other_refs == referees[i]]
        model[i] = ((same.sum() + shrinkage * league) / (len(same) + shrinkage)
                    if len(same) else league)

    gain = np.abs(values - baseline) - np.abs(values - model)
    rng = np.random.default_rng(seed)
    boot = np.array([gain[rng.integers(0, n, n)].mean() for _ in range(samples)])
    lo, hi = (float(x) for x in np.quantile(boot, [0.05, 0.95]))

    return {
        "meciuri": n,
        "arbitri": int(matches["referee"].nunique()),
        "meciuri per arbitru (mediană)": float(
            matches["referee"].value_counts().median()),
        "MAE reper": float(np.abs(values - baseline).mean()),
        "MAE cu arbitru": float(np.abs(values - model).mean()),
        "câștig MAE": float(gain.mean()),
        "interval jos": lo,
        "interval sus": hi,
        "distinct de zero": bool(lo > 0 or hi < 0),
    }
