"""Predictii pentru statisticile secundare: cornere, cartonase, suturi, offside-uri.

Modelul e multiplicativ, in spiritul lui Dixon-Coles, dar pe statistici in loc
de goluri:

    asteptat_gazda = medie_liga * ofensiva_gazda * permisivitate_oaspete * factor_teren

`ofensiva` = cat de mult produce echipa din statistica respectiva, raportat la
medie. `permisivitate` = cat de mult ii lasa adversarului. Ambele sunt estimate
cu ponderare in timp si shrinkage bayesian spre 1 (media ligii), ca echipele cu
putine meciuri sa nu produca valori extreme.

Pentru distributie folosim Negative Binomial: cornerele si cartonasele sunt
supra-dispersate fata de Poisson (varianta > medie), iar NB prinde asta.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
from scipy.stats import nbinom, poisson

log = logging.getLogger(__name__)

#: statisticile pe care le modelam, cu eticheta lor in interfata
SUPPORTED_STATS: dict[str, str] = {
    "corners": "Cornere",
    "yellow_cards": "Cartonase galbene",
    "red_cards": "Cartonase rosii",
    "shots": "Suturi",
    "shots_on_target": "Suturi pe poarta",
    "shots_blocked": "Suturi blocate",
    "shots_inside_box": "Suturi din careu",
    "shots_outside_box": "Suturi din afara careului",
    "fouls": "Faulturi",
    "offsides": "Offside-uri",
    "possession": "Posesie (%)",
    "passes": "Pase",
    "passes_accurate": "Pase precise",
    "passes_into_box": "Pase in careu",
    "saves": "Interventii portar",
    "xg_any": "Goluri asteptate (xG)",
    # aduse de LPF (sursa oficiala), doar pentru sezonul curent
    "attacks": "Atacuri",
    "interceptions": "Interceptari",
    "duels_won": "Dueluri castigate",
    "dribbles": "Driblinguri",
    "tackles": "Deposedari",
    "free_kicks": "Lovituri libere",
}

#: statistici care nu sunt numarabile (nu au sens ca distributie de numarare)
CONTINUOUS_STATS = {"possession", "xg", "xg_any", "pass_accuracy"}


@dataclass
class StatModel:
    """Modelul pentru O singura statistica (ex. cornere)."""

    stat: str
    half_life_days: float = 300.0
    shrinkage: float = 8.0

    league_mean: float = field(default=0.0, init=False)
    home_factor: float = field(default=1.0, init=False)
    attack: dict[str, float] = field(default_factory=dict, init=False)
    concede: dict[str, float] = field(default_factory=dict, init=False)
    dispersion: float = field(default=0.0, init=False)
    n_observations: int = field(default=0, init=False)
    fitted: bool = field(default=False, init=False)

    # ------------------------------------------------------------------
    @property
    def is_continuous(self) -> bool:
        return self.stat in CONTINUOUS_STATS

    def _weights(self, dates: pd.Series, reference: pd.Timestamp) -> np.ndarray:
        if self.half_life_days <= 0:
            return np.ones(len(dates))
        xi = math.log(2.0) / self.half_life_days
        age = (reference - dates).dt.total_seconds().to_numpy() / 86400.0
        return np.exp(-xi * np.clip(age, 0.0, None))

    # ------------------------------------------------------------------
    def fit(self, team_stats: pd.DataFrame) -> "StatModel":
        """Antreneaza pe tabelul (meci, echipa) intors de Database.team_stats_df()."""
        data = team_stats.dropna(subset=[self.stat]).copy()
        if data.empty:
            raise ValueError(f"nu exista date pentru statistica '{self.stat}'")

        data["date"] = pd.to_datetime(data["date"], utc=True)
        reference = data["date"].max()
        weights = self._weights(data["date"], reference)
        values = data[self.stat].to_numpy(dtype=float)

        self.league_mean = float(np.average(values, weights=weights))
        if self.league_mean <= 0:
            raise ValueError(f"media ligii pentru '{self.stat}' este zero")

        # avantajul terenului propriu pe aceasta statistica
        home_mask = data["is_home"].to_numpy() == 1
        if home_mask.any() and (~home_mask).any():
            home_mean = np.average(values[home_mask], weights=weights[home_mask])
            away_mean = np.average(values[~home_mask], weights=weights[~home_mask])
            overall = (home_mean + away_mean) / 2.0
            self.home_factor = float(home_mean / overall) if overall > 0 else 1.0
        else:
            self.home_factor = 1.0

        # cat produce fiecare echipa, si cat ii lasa adversarului
        self.attack = self._team_ratio(data, weights, values, key="team")
        self.concede = self._team_ratio(data, weights, values, key="opponent")

        # dispersia reziduala, pentru Negative Binomial
        expected = np.array([
            self.expected(row.team, row.opponent, bool(row.is_home))
            for row in data.itertuples()
        ])
        residual_var = float(np.average((values - expected) ** 2, weights=weights))
        mean_expected = float(np.average(expected, weights=weights))
        self.dispersion = max(0.0, residual_var - mean_expected)

        self.n_observations = len(data)
        self.fitted = True
        return self

    def _team_ratio(self, data: pd.DataFrame, weights: np.ndarray,
                    values: np.ndarray, *, key: str) -> dict[str, float]:
        """Raport fata de media ligii, cu shrinkage spre 1."""
        out: dict[str, float] = {}
        frame = pd.DataFrame({
            "key": data[key].to_numpy(), "value": values, "w": weights})
        for name, group in frame.groupby("key"):
            w_sum = float(group["w"].sum())
            observed = float((group["value"] * group["w"]).sum())
            # shrinkage: adaugam `shrinkage` meciuri fictive la media ligii
            ratio = ((observed + self.shrinkage * self.league_mean)
                     / ((w_sum + self.shrinkage) * self.league_mean))
            out[str(name)] = float(ratio)
        return out

    # ------------------------------------------------------------------
    def expected(self, team: str, opponent: str, is_home: bool) -> float:
        """Valoarea asteptata a statisticii pentru `team` contra lui `opponent`."""
        attack = self.attack.get(team, 1.0)
        concede = self.concede.get(opponent, 1.0)
        venue = self.home_factor if is_home else (2.0 - self.home_factor)
        return max(1e-6, self.league_mean * attack * concede * venue)

    def expected_match(self, home_team: str, away_team: str) -> tuple[float, float]:
        home = self.expected(home_team, away_team, True)
        away = self.expected(away_team, home_team, False)
        if self.stat == "possession":
            # Posesia celor doua echipe trebuie sa insumeze exact 100%;
            # estimarile independente dau ~99, deci le renormalizam.
            total = home + away
            if total > 0:
                home, away = 100.0 * home / total, 100.0 * away / total
        return home, away

    # ------------------------------------------------------------------
    def distribution(self, mean: float, max_value: int = 30) -> np.ndarray:
        """P(statistica = k) pentru k = 0..max_value."""
        k = np.arange(max_value + 1)
        if self.dispersion <= 1e-6:
            return poisson.pmf(k, mean)
        # parametrizare NB: var = mean + mean^2 / r
        variance = mean + self.dispersion
        if variance <= mean:
            return poisson.pmf(k, mean)
        r = mean ** 2 / (variance - mean)
        p = r / (r + mean)
        return nbinom.pmf(k, r, p)

    def over_under(self, mean: float, line: float,
                   max_value: int = 30) -> dict[str, float]:
        pmf = self.distribution(mean, max_value)
        k = np.arange(len(pmf))
        total = pmf.sum()
        if total <= 0:
            return {"peste": 0.5, "sub": 0.5}
        return {"peste": float(pmf[k > line].sum() / total),
                "sub": float(pmf[k < line].sum() / total)}

    def match_total_over_under(self, home_team: str, away_team: str,
                               line: float) -> dict[str, float]:
        """Peste/sub pe TOTALUL celor doua echipe (ex. total cornere in meci)."""
        home_mean, away_mean = self.expected_match(home_team, away_team)
        home_pmf = self.distribution(home_mean)
        away_pmf = self.distribution(away_mean)
        total_pmf = np.convolve(home_pmf, away_pmf)
        k = np.arange(len(total_pmf))
        total = total_pmf.sum()
        if total <= 0:
            return {"peste": 0.5, "sub": 0.5}
        return {"peste": float(total_pmf[k > line].sum() / total),
                "sub": float(total_pmf[k < line].sum() / total)}

    def team_ratings(self) -> pd.DataFrame:
        rows = [
            {"echipa": team,
             "produce": self.attack.get(team, 1.0),
             "permite": self.concede.get(team, 1.0),
             "asteptat_acasa": self.league_mean * self.attack.get(team, 1.0)
                               * self.home_factor}
            for team in sorted(set(self.attack) | set(self.concede))
        ]
        return pd.DataFrame(rows).sort_values("produce", ascending=False)


class SecondaryStatsModel:
    """Colectie de `StatModel`, cate unul pentru fiecare statistica disponibila."""

    def __init__(self, half_life_days: float = 300.0, shrinkage: float = 8.0) -> None:
        self.half_life_days = half_life_days
        self.shrinkage = shrinkage
        self.models: dict[str, StatModel] = {}
        self.skipped: dict[str, str] = {}

    @classmethod
    def from_config(cls, cfg: dict[str, Any]) -> "SecondaryStatsModel":
        sec = cfg.get("secondary_stats", {})
        return cls(
            half_life_days=float(sec.get("half_life_days", 300)),
            shrinkage=float(sec.get("shrinkage", 8.0)),
        )

    def fit(self, team_stats: pd.DataFrame,
            stats: list[str] | None = None) -> "SecondaryStatsModel":
        stats = stats or list(SUPPORTED_STATS)
        for stat in stats:
            if stat not in team_stats.columns:
                self.skipped[stat] = "coloana lipseste"
                continue
            available = team_stats[stat].notna().sum()
            if available < 20:
                self.skipped[stat] = f"doar {available} observatii"
                continue
            try:
                model = StatModel(stat, self.half_life_days, self.shrinkage)
                self.models[stat] = model.fit(team_stats)
            except Exception as exc:  # noqa: BLE001
                self.skipped[stat] = str(exc)
        log.info("Statistici secundare antrenate: %s | sarite: %s",
                 list(self.models), self.skipped)
        return self

    # ------------------------------------------------------------------
    def available_stats(self) -> list[str]:
        return list(self.models)

    def predict_match(self, home_team: str, away_team: str,
                      lines: dict[str, list[float]] | None = None) -> pd.DataFrame:
        """Tabel cu valorile asteptate si probabilitatile peste/sub pe totaluri."""
        lines = lines or {}
        rows = []
        for stat, model in self.models.items():
            home_mean, away_mean = model.expected_match(home_team, away_team)
            row: dict[str, Any] = {
                "statistica": SUPPORTED_STATS.get(stat, stat),
                "cod": stat,
                "gazda": round(home_mean, 2),
                "oaspete": round(away_mean, 2),
                "total": round(home_mean + away_mean, 2),
            }
            if not model.is_continuous:
                for line in lines.get(stat, []):
                    ou = model.match_total_over_under(home_team, away_team, line)
                    row[f"peste {line}"] = round(ou["peste"], 4)
            rows.append(row)
        return pd.DataFrame(rows)
