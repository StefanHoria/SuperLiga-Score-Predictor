"""Predictii pe jucatori: marcatori probabili, assist-uri, cartonase, minute.

Logica in trei pasi:

1. **Rate la 90 de minute.** Pentru fiecare jucator calculam goluri/assist-uri/
   cartonase la 90 de minute, cu shrinkage spre media pozitiei sale. Un atacant
   cu 2 goluri in 120 de minute nu e un fenomen - e putin joc; shrinkage-ul il
   readuce la realitate.

2. **Minute asteptate.** Din ultimele aparitii estimam cate minute e probabil sa
   joace in meciul urmator.

3. **Scalare la asteptarea echipei.** Suma golurilor asteptate ale jucatorilor
   este scalata ca sa se potriveasca exact cu golurile prezise de Dixon-Coles
   pentru echipa in acel meci. Asa cele doua modele raman consistente.

Sursele de date sunt doua si se completeaza: `player_match_stats` (per meci, de
la API-Football) si `player_season_stats` (agregat pe sezon, de la
Transfermarkt, singura sursa pentru sezonul in curs).
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

#: gruparea pozitiilor raportate de surse in patru categorii
POSITION_GROUPS: dict[str, str] = {
    "G": "portar", "Goalkeeper": "portar",
    "D": "fundas", "Centre-Back": "fundas", "Left-Back": "fundas",
    "Right-Back": "fundas", "Defender": "fundas",
    "M": "mijlocas", "Defensive Midfield": "mijlocas",
    "Central Midfield": "mijlocas", "Attacking Midfield": "mijlocas",
    "Left Midfield": "mijlocas", "Right Midfield": "mijlocas",
    "Midfielder": "mijlocas",
    "F": "atacant", "Centre-Forward": "atacant", "Left Winger": "atacant",
    "Right Winger": "atacant", "Second Striker": "atacant", "Attacker": "atacant",
}


def position_group(position: str | None) -> str:
    if not position:
        return "necunoscut"
    return POSITION_GROUPS.get(str(position).strip(), "necunoscut")


@dataclass
class PlayerRates:
    """Ratele la 90 de minute ale unui jucator, dupa shrinkage."""

    player: str
    team: str
    position: str
    group: str
    minutes: float
    appearances: float
    goals_per90: float
    assists_per90: float
    cards_per90: float
    shots_per90: float = 0.0
    expected_minutes: float = 0.0


@dataclass
class PlayerModel:
    """Model de jucatori antrenat pe agregate sezoniere si/sau date pe meci."""

    min_minutes: float = 270.0
    half_life_days: float = 240.0
    shrinkage_per90: float = 4.0

    rates: dict[str, PlayerRates] = field(default_factory=dict, init=False)
    position_means: dict[str, dict[str, float]] = field(
        default_factory=dict, init=False)
    by_team: dict[str, list[str]] = field(default_factory=dict, init=False)
    fitted: bool = field(default=False, init=False)
    source: str = field(default="", init=False)
    #: jucatori indisponibili (accidentati sau suspendati), pe nume normalizat
    unavailable: set[str] = field(default_factory=set, init=False)

    @classmethod
    def from_config(cls, cfg: dict[str, Any]) -> "PlayerModel":
        pl = cfg.get("players", {})
        return cls(
            min_minutes=float(pl.get("min_minutes", 270)),
            half_life_days=float(pl.get("half_life_days", 240)),
            shrinkage_per90=float(pl.get("shrinkage_per90", 4.0)),
        )

    # ------------------------------------------------------------------
    def fit_from_season_stats(self, season_stats: pd.DataFrame) -> "PlayerModel":
        """Antreneaza din agregate sezoniere (Transfermarkt)."""
        data = season_stats.copy()
        if data.empty:
            raise ValueError("nu exista statistici sezoniere de jucatori")

        data["minutes"] = pd.to_numeric(data["minutes"], errors="coerce").fillna(0.0)
        data = data[data["minutes"] > 0]
        if data.empty:
            raise ValueError("niciun jucator cu minute jucate")

        data["grup"] = data["position"].map(position_group)
        data["cards"] = (pd.to_numeric(data.get("yellow_cards"), errors="coerce")
                         .fillna(0.0)
                         + pd.to_numeric(data.get("red_cards"), errors="coerce")
                         .fillna(0.0))
        for col in ("goals", "assists"):
            data[col] = pd.to_numeric(data[col], errors="coerce").fillna(0.0)

        self.position_means = self._position_means(data)
        self.rates.clear()
        self.by_team.clear()

        for row in data.itertuples():
            nineties = row.minutes / 90.0
            group = row.grup
            means = self.position_means.get(group, self.position_means["_toate"])
            k = self.shrinkage_per90

            rates = PlayerRates(
                player=row.player,
                team=row.team or "necunoscut",
                position=row.position or "",
                group=group,
                minutes=float(row.minutes),
                appearances=float(getattr(row, "appearances", 0) or 0),
                goals_per90=self._shrink(row.goals, nineties, means["goals"], k),
                assists_per90=self._shrink(row.assists, nineties, means["assists"], k),
                cards_per90=self._shrink(row.cards, nineties, means["cards"], k),
            )
            apps = max(1.0, rates.appearances)
            rates.expected_minutes = min(90.0, rates.minutes / apps)

            self.rates[self._key(rates.player, rates.team)] = rates
            self.by_team.setdefault(rates.team, []).append(
                self._key(rates.player, rates.team))

        self.fitted = True
        self.source = "transfermarkt (agregate sezoniere)"
        log.info("Model jucatori: %d jucatori din %d echipe",
                 len(self.rates), len(self.by_team))
        return self

    # ------------------------------------------------------------------
    def fit_from_match_stats(self, match_stats: pd.DataFrame,
                             season_stats: pd.DataFrame | None = None
                             ) -> "PlayerModel":
        """Antreneaza din aparitii pe meci - mai bun decat agregatele sezoniere.

        Doua castiguri fata de `fit_from_season_stats`:

        1. **Ponderare in timp.** Un meci de acum trei luni conteaza mai putin
           decat unul de saptamana trecuta, deci forma recenta si schimbarile
           de rol se vad imediat.
        2. **Minute asteptate corecte.** Media minutelor *din meciurile jucate*
           supraestimeaza grav un jucator folosit rar: cine joaca 90 de minute
           o data la cinci etape ar aparea cu 90 de minute asteptate. Impartim
           minutele la numarul de meciuri ale ECHIPEI, nu la aparitiile lui,
           deci rezervele primesc valori realiste.
        """
        data = match_stats.copy()
        if data.empty:
            raise ValueError("nu exista aparitii pe meci")

        data["date"] = pd.to_datetime(data["date"], utc=True, errors="coerce")
        data = data.dropna(subset=["date"])
        data["minutes"] = pd.to_numeric(data["minutes"], errors="coerce").fillna(0.0)
        data = data[data["minutes"] > 0]
        if data.empty:
            raise ValueError("nicio aparitie cu minute jucate")

        for column in ("goals", "assists", "yellow_cards", "red_cards"):
            if column not in data.columns:
                data[column] = 0.0
            data[column] = pd.to_numeric(data[column], errors="coerce").fillna(0.0)
        data["cards"] = data["yellow_cards"] + data["red_cards"]

        # LPF nu publica pozitia pe meci - o luam din fisa jucatorului
        # (`main_position`, completata de Transfermarkt).
        position = data.get("position")
        if position is None:
            position = pd.Series([None] * len(data), index=data.index)
        if "main_position" in data.columns:
            position = position.fillna(data["main_position"])
        data["position"] = position
        data["grup"] = position.map(position_group)
        if "team" not in data.columns:
            raise ValueError("lipseste coloana `team`")

        reference = data["date"].max()
        data["w"] = self._time_weights(data["date"], reference)

        # cate meciuri a jucat fiecare echipa (ponderat) - numitorul minutelor
        team_matches = (
            data.drop_duplicates(subset=["team", "match_id"])
            .groupby("team")["w"].sum().to_dict()
        )

        self.position_means = self._weighted_position_means(data)
        self.rates.clear()
        self.by_team.clear()

        # Cronologia LPF nu inregistreaza pasele decisive, deci rata de
        # assist-uri nu se poate calcula din datele pe meci. O luam din
        # totalurile de sezon (Transfermarkt), singura sursa care le are.
        assists_per90, assist_means = self._season_assist_rates(season_stats)

        # Mediile pe pozitie pentru assist-uri NU se pot lua din datele de meci
        # (LPF nu le are, deci ar iesi toate zero). Le inlocuim cu cele
        # calculate din totalurile de sezon.
        for group, means in self.position_means.items():
            means["assists"] = assist_means.get(group, assist_means.get(
                "_toate", means["assists"]))

        grouped = data.groupby(["player", "team"], dropna=False)
        for (player, team), chunk in grouped:
            weights = chunk["w"].to_numpy()
            minutes = float((chunk["minutes"] * weights).sum())
            nineties = minutes / 90.0
            if nineties <= 0:
                continue

            group = chunk["grup"].mode()
            group = str(group.iloc[0]) if not group.empty else "necunoscut"
            means = self.position_means.get(group, self.position_means["_toate"])
            k = self.shrinkage_per90

            rates = PlayerRates(
                player=str(player),
                team=str(team) if pd.notna(team) else "necunoscut",
                position=str(chunk["position"].mode().iloc[0])
                if "position" in chunk and not chunk["position"].mode().empty
                else group,
                group=group,
                minutes=float(chunk["minutes"].sum()),
                appearances=float(len(chunk)),
                goals_per90=self._shrink(
                    float((chunk["goals"] * weights).sum()), nineties,
                    means["goals"], k),
                assists_per90=assists_per90.get(
                    self._key(str(player),
                              str(team) if pd.notna(team) else "necunoscut"),
                    means["assists"]),
                cards_per90=self._shrink(
                    float((chunk["cards"] * weights).sum()), nineties,
                    means["cards"], k),
            )

            # Minute asteptate = minute ponderate / meciurile ECHIPEI, nu ale
            # jucatorului: asa intra in calcul si etapele in care n-a jucat.
            played_by_team = team_matches.get(rates.team, 0.0)
            if played_by_team > 0:
                rates.expected_minutes = float(
                    min(90.0, minutes / played_by_team))
            else:
                rates.expected_minutes = float(
                    min(90.0, chunk["minutes"].mean()))

            key = self._key(rates.player, rates.team)
            self.rates[key] = rates
            self.by_team.setdefault(rates.team, []).append(key)

        # completam cu jucatorii care apar doar in agregatele sezoniere
        if season_stats is not None and not season_stats.empty:
            self._merge_season_only(season_stats)

        self.fitted = True
        self.source = "LPF (aparitii pe meci)"
        log.info("Model jucatori din date pe meci: %d jucatori, %d echipe",
                 len(self.rates), len(self.by_team))
        return self

    def _season_assist_rates(self, season_stats: pd.DataFrame | None
                             ) -> tuple[dict[str, float], dict[str, float]]:
        """Assist-uri la 90 de minute: per jucator si medii pe pozitie."""
        if season_stats is None or season_stats.empty:
            return {}, {}
        data = season_stats.copy()
        data["minutes"] = pd.to_numeric(data["minutes"], errors="coerce").fillna(0)
        data["assists"] = pd.to_numeric(data["assists"], errors="coerce").fillna(0)
        data = data[data["minutes"] > 0]
        if data.empty:
            return {}, {}

        data["grup"] = data["position"].map(position_group)
        league = float(data["assists"].sum()
                       / max(1e-6, data["minutes"].sum() / 90.0))

        means: dict[str, float] = {"_toate": league}
        for group, chunk in data.groupby("grup"):
            nineties = max(1e-6, float(chunk["minutes"].sum()) / 90.0)
            means[str(group)] = float(chunk["assists"].sum() / nineties)

        out: dict[str, float] = {}
        for row in data.itertuples():
            nineties = float(row.minutes) / 90.0
            team = str(getattr(row, "team", "") or "necunoscut")
            prior = means.get(str(row.grup), league)
            out[self._key(str(row.player), team)] = self._shrink(
                float(row.assists), nineties, prior, self.shrinkage_per90)
        return out, means

    def _time_weights(self, dates: pd.Series, reference: pd.Timestamp
                      ) -> np.ndarray:
        if self.half_life_days <= 0:
            return np.ones(len(dates))
        xi = math.log(2.0) / self.half_life_days
        age = (reference - dates).dt.total_seconds().to_numpy() / 86400.0
        return np.exp(-xi * np.clip(age, 0.0, None))

    def _weighted_position_means(self, data: pd.DataFrame
                                 ) -> dict[str, dict[str, float]]:
        means: dict[str, dict[str, float]] = {}
        for group, chunk in data.groupby("grup"):
            weights = chunk["w"].to_numpy()
            nineties = max(1e-6, float((chunk["minutes"] * weights).sum()) / 90.0)
            means[str(group)] = {
                key: float((chunk[key] * weights).sum() / nineties)
                for key in ("goals", "assists", "cards")
            }
        weights = data["w"].to_numpy()
        nineties = max(1e-6, float((data["minutes"] * weights).sum()) / 90.0)
        means["_toate"] = {
            key: float((data[key] * weights).sum() / nineties)
            for key in ("goals", "assists", "cards")
        }
        return means

    def _merge_season_only(self, season_stats: pd.DataFrame) -> None:
        """Adauga jucatorii pentru care avem doar totaluri de sezon.

        Se intampla pentru transferuri recente sau meciuri fara formatii
        publicate: mai bine o estimare grosiera decat sa lipseasca din lot.
        """
        data = season_stats.copy()
        data["minutes"] = pd.to_numeric(data["minutes"], errors="coerce").fillna(0)
        data = data[data["minutes"] > 0]
        # indexam jucatorii deja prinsi din meciuri, ca sa nu-i dublam sub
        # alta forma a numelui ("Joao Paulo" vs "Joao Paulo Moreira Fernandes")
        from ..utils.teamnames import strip_diacritics

        def name_tokens(value: str) -> set[str]:
            # fara strip_diacritics, "Joao" si "João" ar parea oameni diferiti
            return set(strip_diacritics(str(value)).lower().split())

        known: dict[str, list[set[str]]] = {}
        for rates in self.rates.values():
            known.setdefault(rates.team, []).append(name_tokens(rates.player))

        for row in data.itertuples():
            team = str(getattr(row, "team", "") or "necunoscut")
            key = self._key(str(row.player), team)
            if key in self.rates:
                continue
            tokens = name_tokens(str(row.player))
            if any(len(tokens & existing) >= 2
                   for existing in known.get(team, [])):
                continue
            nineties = float(row.minutes) / 90.0
            group = position_group(getattr(row, "position", None))
            means = self.position_means.get(group, self.position_means["_toate"])
            k = self.shrinkage_per90
            goals = float(getattr(row, "goals", 0) or 0)
            assists = float(getattr(row, "assists", 0) or 0)
            cards = (float(getattr(row, "yellow_cards", 0) or 0)
                     + float(getattr(row, "red_cards", 0) or 0))
            apps = max(1.0, float(getattr(row, "appearances", 0) or 0))

            self.rates[key] = PlayerRates(
                player=str(row.player), team=team,
                position=str(getattr(row, "position", "") or group),
                group=group, minutes=float(row.minutes), appearances=apps,
                goals_per90=self._shrink(goals, nineties, means["goals"], k),
                assists_per90=self._shrink(assists, nineties, means["assists"], k),
                cards_per90=self._shrink(cards, nineties, means["cards"], k),
                expected_minutes=float(min(90.0, row.minutes / apps)),
            )
            self.by_team.setdefault(team, []).append(key)

    # ------------------------------------------------------------------
    @staticmethod
    def _key(player: str, team: str) -> str:
        return f"{player}||{team}"

    @staticmethod
    def _shrink(total: float, nineties: float, prior: float, k: float) -> float:
        """Rata la 90 min, trasa spre media pozitiei cu `k` reprize fictive."""
        if nineties <= 0:
            return prior
        return float((total + k * prior) / (nineties + k))

    def _position_means(self, data: pd.DataFrame) -> dict[str, dict[str, float]]:
        means: dict[str, dict[str, float]] = {}
        for group, chunk in data.groupby("grup"):
            nineties = max(1e-6, chunk["minutes"].sum() / 90.0)
            means[str(group)] = {
                "goals": float(chunk["goals"].sum() / nineties),
                "assists": float(chunk["assists"].sum() / nineties),
                "cards": float(chunk["cards"].sum() / nineties),
            }
        nineties = max(1e-6, data["minutes"].sum() / 90.0)
        means["_toate"] = {
            "goals": float(data["goals"].sum() / nineties),
            "assists": float(data["assists"].sum() / nineties),
            "cards": float(data["cards"].sum() / nineties),
        }
        return means

    # ------------------------------------------------------------------
    def set_unavailable(self, names: set[str]) -> None:
        """Marcheaza jucatorii accidentati sau suspendati.

        Ii scoatem din predictii: un atacant accidentat nu poate marca, iar
        golurile lui asteptate trebuie sa se redistribuie catre restul echipei,
        nu sa se piarda. Redistribuirea vine gratis din scalarea la asteptarea
        echipei facuta in `predict_team`.
        """
        from ..utils.teamnames import strip_diacritics
        self.unavailable = {
            strip_diacritics(str(n)).lower().strip() for n in names if n}

    def is_available(self, player: str) -> bool:
        from ..utils.teamnames import strip_diacritics
        if not self.unavailable:
            return True
        normalized = strip_diacritics(str(player)).lower().strip()
        if normalized in self.unavailable:
            return False
        # numele difera intre surse: acceptam si potrivirea pe 2+ cuvinte
        tokens = set(normalized.split())
        return not any(len(tokens & set(absent.split())) >= 2
                       for absent in self.unavailable)

    def team_players(self, team: str) -> list[PlayerRates]:
        return [self.rates[k] for k in self.by_team.get(team, [])
                if self.is_available(self.rates[k].player)]

    def predict_team(self, team: str, team_expected_goals: float,
                     *, min_minutes_filter: float | None = None) -> pd.DataFrame:
        """Predictii pentru toti jucatorii unei echipe intr-un meci.

        `team_expected_goals` vine din Dixon-Coles; scalam ratele individuale ca
        suma lor sa dea exact aceasta valoare.
        """
        if not self.fitted:
            raise RuntimeError("modelul de jucatori nu a fost antrenat")

        players = self.team_players(team)
        threshold = (self.min_minutes if min_minutes_filter is None
                     else min_minutes_filter)
        players = [p for p in players if p.minutes >= threshold] or players
        if not players:
            return pd.DataFrame()

        raw_goals = np.array([
            p.goals_per90 * p.expected_minutes / 90.0 for p in players])
        raw_assists = np.array([
            p.assists_per90 * p.expected_minutes / 90.0 for p in players])

        # scalam ca suma golurilor individuale sa fie egala cu asteptarea echipei
        scale = (team_expected_goals / raw_goals.sum()) if raw_goals.sum() > 0 else 0.0
        goals = raw_goals * scale
        # assist-urile urmeaza aceeasi scalare (nu orice gol are pasa decisiva)
        assists = raw_assists * scale * 0.75

        rows = []
        for player, lam_goal, lam_assist in zip(players, goals, assists):
            lam_card = player.cards_per90 * player.expected_minutes / 90.0
            rows.append({
                "jucator": player.player,
                "pozitie": player.position or player.group,
                "minute_asteptate": round(player.expected_minutes),
                "goluri_asteptate": round(float(lam_goal), 3),
                "marcheaza": round(1.0 - math.exp(-lam_goal), 4),
                "marcheaza_2plus": round(
                    1.0 - math.exp(-lam_goal) * (1.0 + lam_goal), 4),
                "da_assist": round(1.0 - math.exp(-lam_assist), 4),
                "gol_sau_assist": round(
                    1.0 - math.exp(-(lam_goal + lam_assist)), 4),
                "cartonas": round(1.0 - math.exp(-lam_card), 4),
                "minute_jucate_sezon": round(player.minutes),
            })
        df = pd.DataFrame(rows)
        return df.sort_values("goluri_asteptate", ascending=False).reset_index(
            drop=True)

    def predict_match(self, home_team: str, away_team: str,
                      home_goals: float, away_goals: float) -> pd.DataFrame:
        """Marcatori probabili din ambele echipe, intr-un singur tabel."""
        frames = []
        for team, expected in ((home_team, home_goals), (away_team, away_goals)):
            df = self.predict_team(team, expected)
            if not df.empty:
                df.insert(0, "echipa", team)
                frames.append(df)
        if not frames:
            return pd.DataFrame()
        return (pd.concat(frames, ignore_index=True)
                .sort_values("goluri_asteptate", ascending=False)
                .reset_index(drop=True))

    # ------------------------------------------------------------------
    def top_players(self, metric: str = "goals_per90", limit: int = 25,
                    min_minutes: float | None = None) -> pd.DataFrame:
        """Clasament de jucatori dupa o rata la 90 de minute."""
        threshold = self.min_minutes if min_minutes is None else min_minutes
        rows = [
            {
                "jucator": r.player, "echipa": r.team, "pozitie": r.position,
                "minute": round(r.minutes), "aparitii": round(r.appearances),
                "goluri/90": round(r.goals_per90, 3),
                "assist/90": round(r.assists_per90, 3),
                "cartonase/90": round(r.cards_per90, 3),
                "implicari/90": round(r.goals_per90 + r.assists_per90, 3),
            }
            for r in self.rates.values() if r.minutes >= threshold
        ]
        if not rows:
            return pd.DataFrame()
        column = {"goals_per90": "goluri/90", "assists_per90": "assist/90",
                  "cards_per90": "cartonase/90",
                  "involvement": "implicari/90"}.get(metric, "goluri/90")
        return (pd.DataFrame(rows).sort_values(column, ascending=False)
                .head(limit).reset_index(drop=True))
