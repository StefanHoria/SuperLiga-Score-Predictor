"""Leaga toate modelele intr-un singur obiect, gata de folosit din dashboard.

`PredictionPipeline` incarca datele o singura data, antreneaza ce se poate
antrena cu ele si raporteaza cinstit ce nu a putut fi antrenat si de ce.
Nimic nu arunca exceptii spre interfata: daca lipsesc datele de cornere,
`secondary` ramane None si pagina afiseaza un mesaj, nu o eroare.
"""
from __future__ import annotations

import json
import logging
import pickle
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ..config import Config, get_config
from ..storage.db import Database
from .dixon_coles import DixonColesModel
from .elo import EloModel
from .markets import MatchPrediction, build_prediction, result_1x2
from .players import PlayerModel
from .secondary import SecondaryStatsModel
from .xg import ExpectedGoalsModel

log = logging.getLogger(__name__)


class _EloBlended:
    """Matricea Dixon-Coles, reponderata ca sa dea 1X2 amestecat cu Elo.

    Amestecarea doar a probabilitatilor 1X2 ar rupe coerenta: scorurile exacte
    si pietele de goluri ar ramane cele vechi, contrazicand noul 1X2. In schimb
    inmultim fiecare regiune a matricei (victorie gazda / egal / victorie
    oaspete) cu raportul dintre noua si vechea probabilitate, apoi renormalizam.
    Marginalele 1X2 devin exact cele amestecate, iar tot ce se deduce din
    matrice ramane consistent cu ele.
    """

    def __init__(self, goals, elo, weight: float) -> None:
        self.goals = goals
        self.elo = elo
        self.weight = float(min(max(weight, 0.0), 1.0))
        self.max_goals = goals.max_goals
        self.teams = goals.teams
        self.attack = goals.attack

    def expected_goals(self, home_team: str, away_team: str):
        matrix = self.score_matrix(home_team, away_team)
        size = matrix.shape[0]
        index = np.arange(size)
        return (float((matrix.sum(axis=1) * index).sum()),
                float((matrix.sum(axis=0) * index).sum()))

    def score_matrix(self, home_team: str, away_team: str):
        matrix = self.goals.score_matrix(home_team, away_team)
        base = result_1x2(matrix)
        elo_probs = self.elo.win_probabilities(home_team, away_team)

        blended = {}
        for key in ("gazda", "egal", "oaspete"):
            blended[key] = (max(base[key], 1e-9) ** (1 - self.weight)
                            * max(elo_probs[key], 1e-9) ** self.weight)
        norm = sum(blended.values())
        blended = {k: v / norm for k, v in blended.items()}

        adjusted = matrix.copy()
        size = matrix.shape[0]
        rows, cols = np.indices((size, size))
        regions = {"gazda": rows > cols, "egal": rows == cols,
                   "oaspete": rows < cols}
        for key, mask in regions.items():
            if base[key] > 1e-12:
                adjusted[mask] *= blended[key] / base[key]

        total = adjusted.sum()
        return adjusted / total if total > 0 else matrix


@dataclass
class PredictionPipeline:
    cfg: Config = field(default_factory=get_config)
    db: Database = field(default_factory=Database)

    goals: DixonColesModel | None = field(default=None, init=False)
    elo: EloModel | None = field(default=None, init=False)
    secondary: SecondaryStatsModel | None = field(default=None, init=False)
    xg: ExpectedGoalsModel | None = field(default=None, init=False)
    players: PlayerModel | None = field(default=None, init=False)

    matches: pd.DataFrame = field(default_factory=pd.DataFrame, init=False)
    warnings: list[str] = field(default_factory=list, init=False)
    trained_at: datetime | None = field(default=None, init=False)

    # ------------------------------------------------------------------
    def train(self, *, seasons: list[int] | None = None) -> "PredictionPipeline":
        self.warnings.clear()
        self.matches = self.db.matches_df(finished_only=True)
        if seasons:
            self.matches = self.matches[self.matches["season"].isin(seasons)]

        if self.matches.empty:
            self.warnings.append(
                "Nu exista meciuri jucate in baza de date. "
                "Ruleaza: python scripts/fetch_data.py")
            return self

        # --- goluri (obligatoriu) ---
        try:
            self.goals = DixonColesModel.from_config(self.cfg.model).fit(self.matches)
        except Exception as exc:  # noqa: BLE001
            self.warnings.append(f"Modelul de goluri nu a putut fi antrenat: {exc}")

        # --- Elo (informativ) ---
        try:
            self.elo = EloModel.from_config(self.cfg.model).fit(self.matches)
        except Exception as exc:  # noqa: BLE001
            self.warnings.append(f"Elo indisponibil: {exc}")

        # --- statistici secundare ---
        team_stats = self.db.team_stats_df()
        if team_stats.empty:
            self.warnings.append(
                "Nu exista statistici pe meci (cornere, cartonase, suturi). "
                "Ruleaza: python scripts/fetch_data.py --stats-only")
        else:
            # xG estimat din zonele de sut, pentru sezoanele fara xG publicat
            try:
                self.xg = ExpectedGoalsModel().fit(team_stats)
                team_stats["xg_any"] = team_stats["xg"].fillna(
                    self.xg.predict_frame(team_stats))
            except Exception as exc:  # noqa: BLE001
                self.warnings.append(f"xG estimat indisponibil: {exc}")

            try:
                self.secondary = SecondaryStatsModel.from_config(
                    self.cfg.model).fit(team_stats)
                if not self.secondary.models:
                    self.warnings.append(
                        "Statistici pe meci insuficiente pentru orice model "
                        "secundar (minim 20 de observatii per statistica).")
            except Exception as exc:  # noqa: BLE001
                self.warnings.append(f"Statistici secundare indisponibile: {exc}")

        # --- jucatori ---
        # Preferam aparitiile pe meci (ponderate in timp, cu minute realiste)
        # si cadem pe agregatele sezoniere doar daca nu avem destule.
        season_stats = self.db.player_season_stats_df()
        match_player_stats = self.db.player_stats_df()

        if len(match_player_stats) >= 200:
            try:
                self.players = PlayerModel.from_config(
                    self.cfg.model).fit_from_match_stats(
                        match_player_stats, season_stats)
            except Exception as exc:  # noqa: BLE001
                self.warnings.append(
                    f"Modelul de jucatori pe meci a esuat ({exc}); "
                    "incerc agregatele sezoniere")

        if self.players is None:
            if season_stats.empty:
                self.warnings.append(
                    "Nu exista statistici de jucatori. Ruleaza: "
                    "python scripts/fetch_data.py --sources transfermarkt")
            else:
                try:
                    self.players = PlayerModel.from_config(
                        self.cfg.model).fit_from_season_stats(season_stats)
                except Exception as exc:  # noqa: BLE001
                    self.warnings.append(
                        f"Modelul de jucatori indisponibil: {exc}")

        # Ies din predictii doua categorii, pe acelasi mecanism:
        #   * accidentatii si suspendatii - lipsesc de la meciul urmator;
        #   * cei PLECATI din club - nu mai joaca acolo deloc.
        #
        # A doua lipsea, si se vedea: modelul propunea in continuare marcatori
        # care se transferasera de luni de zile, fiindca el isi ia lotul din
        # aparitiile istorice, iar acolo chiar jucasera pentru echipa.
        if self.players is not None and self.players.fitted:
            try:
                absent = self.db.unavailable_players()
                plecati = self.db.departed_players()
                # Transfermarkt isi actualizeaza loturile cu intarziere. Cand
                # presa afirma o plecare ca fapt, o credem: costul unei
                # excluderi gresite e mic, iar al unei predictii pe un jucator
                # care nu mai e la club e vizibil si jenant.
                raportati = self.db.reported_departures()
                nume_raportate = set(raportati["player_name"])                     if not raportati.empty else set()
                if absent or plecati or nume_raportate:
                    self.players.set_unavailable(
                        absent | plecati | nume_raportate)
                    log.info("exclusi din predictii: %d indisponibili, "
                             "%d fara club, %d dati plecati de presa",
                             len(absent), len(plecati), len(nume_raportate))
            except Exception as exc:  # noqa: BLE001
                self.warnings.append(f"Lista de absenti indisponibila: {exc}")

        self.trained_at = datetime.now(timezone.utc)
        return self

    # ------------------------------------------------------------------
    @property
    def ready(self) -> bool:
        return self.goals is not None and self.goals.fitted

    @property
    def teams(self) -> list[str]:
        if self.goals and self.goals.fitted:
            return sorted(self.goals.teams)
        if not self.matches.empty:
            return sorted(set(self.matches["home_team"])
                          | set(self.matches["away_team"]))
        return []

    def current_season_teams(self) -> list[str]:
        """Echipele care au jucat in sezonul curent - relevante pentru predictii."""
        season = self.cfg.current_season
        current = self.matches[self.matches["season"] == season]
        if current.empty:
            return self.teams
        return sorted(set(current["home_team"]) | set(current["away_team"]))

    # ------------------------------------------------------------------
    def predict(self, home_team: str, away_team: str) -> MatchPrediction:
        if not self.ready:
            raise RuntimeError("modelul de goluri nu e antrenat")
        model = self.goals
        weight = float(self.cfg.model.get("elo_ensemble_weight", 0.0) or 0.0)
        if self.elo is not None and weight > 0:
            model = _EloBlended(self.goals, self.elo, weight)
        return build_prediction(
            model, home_team, away_team,
            ou_lines=self.cfg.markets["over_under_lines"],
            handicaps=self.cfg.markets["asian_handicaps"],
            top_n=self.cfg.markets["correct_score_top_n"],
        )

    def predict_secondary(self, home_team: str, away_team: str) -> pd.DataFrame:
        if self.secondary is None or not self.secondary.models:
            return pd.DataFrame()
        markets = self.cfg.markets
        lines = {
            "corners": markets.get("corners_lines", []),
            "yellow_cards": markets.get("cards_lines", []),
        }
        return self.secondary.predict_match(home_team, away_team, lines)

    def predict_players(self, home_team: str, away_team: str,
                        prediction: MatchPrediction | None = None) -> pd.DataFrame:
        if self.players is None or not self.players.fitted:
            return pd.DataFrame()
        pred = prediction or self.predict(home_team, away_team)
        return self.players.predict_match(
            home_team, away_team,
            pred.expected_home_goals, pred.expected_away_goals,
        )

    def full_report(self, home_team: str, away_team: str) -> dict[str, Any]:
        """Tot ce stie aplicatia despre un meci."""
        prediction = self.predict(home_team, away_team)
        return {
            "predictie": prediction,
            "secundare": self.predict_secondary(home_team, away_team),
            "jucatori": self.predict_players(home_team, away_team, prediction),
            "elo": (self.elo.win_probabilities(home_team, away_team)
                    if self.elo else None),
        }

    # ------------------------------------------------------------------
    def save(self, path: Path | None = None) -> Path:
        target = path or (self.cfg.path("models") / "pipeline.pkl")
        with open(target, "wb") as fh:
            pickle.dump({
                "goals": self.goals, "elo": self.elo,
                "secondary": self.secondary, "players": self.players,
                "trained_at": self.trained_at, "warnings": self.warnings,
            }, fh)
        meta = {
            "trained_at": self.trained_at.isoformat() if self.trained_at else None,
            "matches": int(len(self.matches)),
            "teams": len(self.teams),
            "warnings": self.warnings,
        }
        (target.with_suffix(".json")).write_text(
            json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
        return target

    @classmethod
    def load(cls, path: Path | None = None) -> "PredictionPipeline":
        cfg = get_config()
        target = path or (cfg.path("models") / "pipeline.pkl")
        pipeline = cls(cfg=cfg)
        with open(target, "rb") as fh:
            payload = pickle.load(fh)
        pipeline.goals = payload["goals"]
        pipeline.elo = payload["elo"]
        pipeline.secondary = payload["secondary"]
        pipeline.players = payload["players"]
        pipeline.trained_at = payload["trained_at"]
        pipeline.warnings = payload.get("warnings", [])
        pipeline.matches = pipeline.db.matches_df(finished_only=True)
        return pipeline
