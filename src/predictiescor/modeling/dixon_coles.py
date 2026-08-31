"""Modelul Dixon-Coles pentru numarul de goluri.

Ideea de baza (Maher 1982, rafinat de Dixon & Coles 1997):

    lambda_gazda   = exp(atac_gazda   + aparare_oaspete + avantaj_teren)
    lambda_oaspete = exp(atac_oaspete + aparare_gazda)

Golurile sunt aproximativ Poisson, dar scorurile mici (0-0, 1-0, 0-1, 1-1) apar
mai des decat prezice Poisson-ul independent. Dixon-Coles corecteaza exact acele
patru celule cu parametrul `rho`.

In plus, meciurile vechi conteaza mai putin: fiecare meci primeste ponderea
exp(-xi * zile_vechime), cu `xi` derivat din timpul de injumatatire din config.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.stats import poisson

log = logging.getLogger(__name__)


def tau(home_goals: np.ndarray, away_goals: np.ndarray,
        lam: np.ndarray, mu: np.ndarray, rho: float) -> np.ndarray:
    """Corectia Dixon-Coles pentru cele patru scoruri mici."""
    out = np.ones_like(lam, dtype=float)
    h0a0 = (home_goals == 0) & (away_goals == 0)
    h0a1 = (home_goals == 0) & (away_goals == 1)
    h1a0 = (home_goals == 1) & (away_goals == 0)
    h1a1 = (home_goals == 1) & (away_goals == 1)
    out[h0a0] = 1.0 - lam[h0a0] * mu[h0a0] * rho
    out[h0a1] = 1.0 + lam[h0a1] * rho
    out[h1a0] = 1.0 + mu[h1a0] * rho
    out[h1a1] = 1.0 - rho
    return out


@dataclass
class DixonColesModel:
    """Estimeaza forta de atac/aparare a fiecarei echipe din rezultatele trecute."""

    half_life_days: float = 240.0
    max_goals: int = 10
    l2_penalty: float = 0.02
    min_matches: int = 6
    #: "meciuri fictive" la media ligii. Cu cat o echipa are mai putine meciuri
    #: reale, cu atat parametrii ei sunt trasi mai tare spre 0 (media ligii).
    #: Fara asta, o nou-promovata cu 6 meciuri bune ajunge artificial pe primul loc.
    shrinkage_matches: float = 20.0
    #: cat de mult poate devia avantajul terenului al unei echipe de la media
    #: ligii. 0 = un singur avantaj comun (comportamentul vechi). Valori mai
    #: mari lasa echipele "fortarete" sa se diferentieze, dar cu shrinkage
    #: puternic: sunt putine meciuri pe echipa si zgomotul e mare.
    home_advantage_spread: float = 30.0

    teams: list[str] = field(default_factory=list, init=False)
    attack: dict[str, float] = field(default_factory=dict, init=False)
    defence: dict[str, float] = field(default_factory=dict, init=False)
    home_advantage: float = field(default=0.25, init=False)
    home_advantage_by_team: dict[str, float] = field(
        default_factory=dict, init=False)
    rho: float = field(default=-0.05, init=False)
    league_home_goals: float = field(default=1.5, init=False)
    league_away_goals: float = field(default=1.2, init=False)
    fitted: bool = field(default=False, init=False)
    n_matches: int = field(default=0, init=False)
    _effective_matches: dict[str, float] = field(default_factory=dict, init=False)
    log_likelihood: float = field(default=float("nan"), init=False)

    # ------------------------------------------------------------------
    @classmethod
    def from_config(cls, cfg: dict[str, Any]) -> "DixonColesModel":
        dc = cfg.get("dixon_coles", {})
        return cls(
            half_life_days=float(dc.get("half_life_days", 240)),
            max_goals=int(dc.get("max_goals", 10)),
            l2_penalty=float(dc.get("l2_penalty", 0.02)),
            min_matches=int(dc.get("min_matches", 6)),
            shrinkage_matches=float(dc.get("shrinkage_matches", 20)),
            home_advantage_spread=float(dc.get("home_advantage_spread", 30)),
        )

    # ------------------------------------------------------------------
    def _weights(self, dates: pd.Series, reference: pd.Timestamp) -> np.ndarray:
        """Pondere exponentiala: un meci de acum `half_life` zile cantareste 1/2."""
        if self.half_life_days <= 0:
            return np.ones(len(dates))
        xi = math.log(2.0) / self.half_life_days
        age_days = (reference - dates).dt.total_seconds().to_numpy() / 86400.0
        age_days = np.clip(age_days, 0.0, None)
        return np.exp(-xi * age_days)

    def fit(self, matches: pd.DataFrame,
            reference_date: pd.Timestamp | None = None) -> "DixonColesModel":
        """Antreneaza pe un DataFrame cu home_team/away_team/home_goals/away_goals/date."""
        data = matches.dropna(subset=["home_goals", "away_goals"]).copy()
        if data.empty:
            raise ValueError("nu exista meciuri jucate pentru antrenare")

        data["date"] = pd.to_datetime(data["date"], utc=True)
        reference = reference_date or data["date"].max()
        reference = pd.Timestamp(reference).tz_convert("UTC") \
            if pd.Timestamp(reference).tzinfo else pd.Timestamp(reference, tz="UTC")

        # Echipele cu prea putine meciuri raman pe media ligii (evitam overfit)
        counts = pd.concat([data["home_team"], data["away_team"]]).value_counts()
        self.teams = sorted(counts[counts >= self.min_matches].index.tolist())
        if len(self.teams) < 2:
            raise ValueError(
                f"prea putine echipe cu minim {self.min_matches} meciuri")

        data = data[data["home_team"].isin(self.teams)
                    & data["away_team"].isin(self.teams)]
        if data.empty:
            raise ValueError("niciun meci intre echipe cu destule aparitii")

        index = {team: i for i, team in enumerate(self.teams)}
        home_idx = data["home_team"].map(index).to_numpy()
        away_idx = data["away_team"].map(index).to_numpy()
        home_goals = data["home_goals"].to_numpy(dtype=int)
        away_goals = data["away_goals"].to_numpy(dtype=int)
        weights = self._weights(data["date"], reference)

        # Numar efectiv (ponderat in timp) de meciuri per echipa - il raportam
        # in tabelul de forte, ca sa se vada pe cate date se sprijina estimarea.
        n = len(self.teams)
        effective = np.zeros(n)
        np.add.at(effective, home_idx, weights)
        np.add.at(effective, away_idx, weights)
        self._effective_matches = dict(zip(self.teams, effective))

        # Regularizare ca prior gaussian centrat pe media ligii, cu precizie
        # FIXA: echivalentul a `shrinkage_matches` meciuri fictive de echipa
        # medie. Nu depinde de cate meciuri are echipa - exact asta face ca
        # efectul sa fie mic pentru echipele cu istoric lung (informatia din
        # date depaseste priorul) si mare pentru nou-promovate.
        # Varianta anterioara, care scala penalizarea cu 1/n_meciuri, era prea
        # slaba fata de log-verosimilitate si nu corecta nimic in practica.
        prior_precision = 0.5 * self.shrinkage_matches
        ridge = self.l2_penalty
        # parametri: [atac(n), aparare(n), avantaj_global, rho, avantaj_echipa(n)]
        x0 = np.concatenate([np.zeros(n), np.zeros(n), [0.25], [-0.05],
                             np.zeros(n)])

        def unpack(params: np.ndarray):
            atk = params[:n]
            # constrangere de identificabilitate: media atacurilor = 0
            atk = atk - atk.mean()
            dfn = params[n:2 * n]
            dfn = dfn - dfn.mean()
            home_extra = params[2 * n + 2:3 * n + 2]
            home_extra = home_extra - home_extra.mean()
            return (atk, dfn, float(params[2 * n]), float(params[2 * n + 1]),
                    home_extra)

        def negative_log_likelihood(params: np.ndarray) -> float:
            atk, dfn, home_adv, rho, home_extra = unpack(params)
            lam = np.exp(atk[home_idx] + dfn[away_idx] + home_adv
                         + home_extra[home_idx])
            mu = np.exp(atk[away_idx] + dfn[home_idx])
            lam = np.clip(lam, 1e-6, 25.0)
            mu = np.clip(mu, 1e-6, 25.0)

            correction = tau(home_goals, away_goals, lam, mu, rho)
            # rho invalid poate produce corectii negative -> penalizam puternic
            if np.any(correction <= 0):
                return 1e10

            ll = (np.log(correction)
                  + poisson.logpmf(home_goals, lam)
                  + poisson.logpmf(away_goals, mu))
            penalty = float(
                (prior_precision + ridge) * np.sum(atk ** 2 + dfn ** 2))
            # avantajul propriu al fiecarei echipe are prior separat, mai strans
            penalty += float(self.home_advantage_spread
                             * np.sum(home_extra ** 2))
            return -float(np.sum(weights * ll)) + penalty

        bounds = ([(-3.0, 3.0)] * (2 * n) + [(-1.0, 1.5), (-0.9, 0.9)]
                  + [(-0.8, 0.8)] * n)
        result = minimize(
            negative_log_likelihood, x0, method="L-BFGS-B", bounds=bounds,
            options={"maxiter": 800, "ftol": 1e-9},
        )

        atk, dfn, home_adv, rho, home_extra = unpack(result.x)
        self.attack = dict(zip(self.teams, atk))
        self.defence = dict(zip(self.teams, dfn))
        self.home_advantage = home_adv
        self.home_advantage_by_team = dict(zip(self.teams, home_extra))
        self.rho = float(np.clip(rho, -0.9, 0.9))
        self.fitted = True
        self.n_matches = len(data)
        self.log_likelihood = -float(result.fun)
        self.league_home_goals = float(np.average(home_goals, weights=weights))
        self.league_away_goals = float(np.average(away_goals, weights=weights))

        log.info(
            "Dixon-Coles antrenat pe %d meciuri, %d echipe | avantaj teren=%.3f "
            "rho=%.3f logL=%.1f",
            self.n_matches, len(self.teams), self.home_advantage, self.rho,
            self.log_likelihood,
        )
        return self

    # ------------------------------------------------------------------
    def expected_goals(self, home_team: str, away_team: str) -> tuple[float, float]:
        """Golurile asteptate pentru fiecare echipa (lambda, mu)."""
        if not self.fitted:
            raise RuntimeError("modelul nu a fost antrenat")
        # echipele necunoscute primesc parametri neutri (media ligii)
        atk_h = self.attack.get(home_team, 0.0)
        def_h = self.defence.get(home_team, 0.0)
        atk_a = self.attack.get(away_team, 0.0)
        def_a = self.defence.get(away_team, 0.0)
        home_extra = self.home_advantage_by_team.get(home_team, 0.0)
        lam = math.exp(atk_h + def_a + self.home_advantage + home_extra)
        mu = math.exp(atk_a + def_h)
        return min(lam, 8.0), min(mu, 8.0)

    def score_matrix(self, home_team: str, away_team: str) -> np.ndarray:
        """Matricea P(scor gazda = i, scor oaspete = j), normalizata."""
        lam, mu = self.expected_goals(home_team, away_team)
        size = self.max_goals + 1
        goals = np.arange(size)

        home_probs = poisson.pmf(goals, lam)
        away_probs = poisson.pmf(goals, mu)
        matrix = np.outer(home_probs, away_probs)

        # corectia Dixon-Coles pe cele patru celule cu scor mic
        matrix[0, 0] *= 1.0 - lam * mu * self.rho
        matrix[0, 1] *= 1.0 + lam * self.rho
        matrix[1, 0] *= 1.0 + mu * self.rho
        matrix[1, 1] *= 1.0 - self.rho

        matrix = np.clip(matrix, 0.0, None)
        total = matrix.sum()
        return matrix / total if total > 0 else matrix

    # ------------------------------------------------------------------
    def team_ratings(self) -> pd.DataFrame:
        """Tabel cu forta de atac/aparare, in unitati usor de citit."""
        if not self.fitted:
            raise RuntimeError("modelul nu a fost antrenat")
        rows = []
        for team in self.teams:
            atk, dfn = self.attack[team], self.defence[team]
            rows.append({
                "echipa": team,
                "atac": atk,
                "aparare": dfn,
                # cate goluri ar marca/primi contra unui adversar mediu, in deplasare
                "goluri_marcate_asteptate": math.exp(atk),
                "goluri_primite_asteptate": math.exp(dfn),
                "forta_totala": atk - dfn,
                "avantaj_teren": (self.home_advantage
                                  + self.home_advantage_by_team.get(team, 0.0)),
                "meciuri_efective": self._effective_matches.get(team, 0.0),
            })
        df = pd.DataFrame(rows).sort_values("forta_totala", ascending=False)
        return df.reset_index(drop=True)

    def to_dict(self) -> dict[str, Any]:
        return {
            "teams": self.teams,
            "attack": self.attack,
            "defence": self.defence,
            "home_advantage": self.home_advantage,
            "home_advantage_by_team": self.home_advantage_by_team,
            "rho": self.rho,
            "half_life_days": self.half_life_days,
            "max_goals": self.max_goals,
            "n_matches": self.n_matches,
            "log_likelihood": self.log_likelihood,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "DixonColesModel":
        model = cls(
            half_life_days=payload.get("half_life_days", 240),
            max_goals=payload.get("max_goals", 10),
        )
        model.teams = payload["teams"]
        model.attack = payload["attack"]
        model.defence = payload["defence"]
        model.home_advantage = payload["home_advantage"]
        model.home_advantage_by_team = payload.get("home_advantage_by_team", {})
        model.rho = payload["rho"]
        model.n_matches = payload.get("n_matches", 0)
        model.log_likelihood = payload.get("log_likelihood", float("nan"))
        model.fitted = True
        return model
