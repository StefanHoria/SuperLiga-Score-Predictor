"""Provider API-Football (api-sports.io) - sursa cea mai bogata.

Ofera, pentru fiecare meci: suturi, cornere, cartonase, posesie, pase, xG
(unde exista) si statistici individuale pentru fiecare jucator.

ATENTIE la cota de request-uri: planul gratuit are 100 de cereri pe zi, iar
statisticile se cer *per meci*. De aceea providerul are un buget explicit
(`max_requests`) si prioritizeaza meciurile cele mai recente care inca nu au
statistici in baza de date.
"""
from __future__ import annotations

import logging
from typing import Any, Iterable

from .base import DataProvider, IngestResult

log = logging.getLogger(__name__)

DIRECT_BASE = "https://v3.football.api-sports.io"
RAPID_BASE = "https://api-football-v1.p.rapidapi.com/v3"
RAPID_HOST = "api-football-v1.p.rapidapi.com"

# Denumirea statisticii la API-Football -> coloana din match_team_stats
STAT_MAP: dict[str, str] = {
    "Shots on Goal": "shots_on_target",
    "Shots off Goal": "shots_off_target",
    "Total Shots": "shots",
    "Blocked Shots": "shots_blocked",
    "Shots insidebox": "shots_inside_box",
    "Shots outsidebox": "shots_outside_box",
    "Fouls": "fouls",
    "Corner Kicks": "corners",
    "Offsides": "offsides",
    "Ball Possession": "possession",
    "Yellow Cards": "yellow_cards",
    "Red Cards": "red_cards",
    "Goalkeeper Saves": "saves",
    "Total passes": "passes",
    "Passes accurate": "passes_accurate",
    "Passes %": "pass_accuracy",
    "expected_goals": "xg",
    "Expected Goals": "xg",
    "goals_prevented": "big_chances",
}

FINISHED_STATUSES = {"FT", "AET", "PEN"}

#: Planul gratuit accepta 10 cereri pe minut. 6.5s intre cereri lasa o marja.
MIN_DELAY_SECONDS = 6.5

#: Chei de eroare care inseamna "reincearca mai tarziu", nu "cerere gresita"
TRANSIENT_ERROR_KEYS = {"rateLimit", "requests"}


class RateLimitedError(RuntimeError):
    """Am depasit limita de cereri pe minut sau pe zi - are sens sa asteptam."""


class ApiFootballProvider(DataProvider):
    name = "apifootball"
    provides = (
        "rezultate", "suturi", "cornere", "cartonase", "posesie", "pase",
        "xG", "statistici jucatori", "clasament", "cote",
    )

    def __init__(self, *args: Any, max_requests: int | None = None,
                 **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.max_requests = int(
            max_requests if max_requests is not None
            else self.cfg.league.get("apifootball_daily_budget", 90)
        )
        self.requests_used = 0
        self.league_id = int(self.cfg.league["apifootball_id"])
        self.min_season = int(self.cfg.league.get("apifootball_min_season", 2022))
        self.max_season = int(self.cfg.league.get("apifootball_max_season", 2024))
        # Planul gratuit limiteaza si la 10 cereri pe MINUT, nu doar 100 pe zi.
        # Sub ~6.5s intre cereri primim 429 si pierdem cereri din cota zilnica.
        self.http.delay = max(self.http.delay, MIN_DELAY_SECONDS)

    def supports_season(self, season: int) -> bool:
        """Planul gratuit acopera doar un interval de sezoane (vezi config.yaml)."""
        return self.min_season <= season <= self.max_season

    # ------------------------------------------------------------------
    @property
    def available(self) -> bool:
        return self.cfg.secrets.has_apifootball

    @property
    def base_url(self) -> str:
        return (RAPID_BASE if self.cfg.secrets.apifootball_host == "rapidapi"
                else DIRECT_BASE)

    @property
    def auth_headers(self) -> dict[str, str]:
        key = self.cfg.secrets.apifootball_key
        if self.cfg.secrets.apifootball_host == "rapidapi":
            return {"x-rapidapi-key": key, "x-rapidapi-host": RAPID_HOST}
        return {"x-apisports-key": key}

    @property
    def budget_left(self) -> int:
        return max(0, self.max_requests - self.requests_used)

    # ------------------------------------------------------------------
    def _get(self, endpoint: str, params: dict | None = None,
             *, ttl_hours: float | None = None) -> list[dict]:
        """Apel catre API cu contorizarea bugetului si validarea raspunsului."""
        if not self.available:
            raise RuntimeError("APIFOOTBALL_KEY lipseste din .env")

        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        # Cache-ul nu consuma buget: verificam intai daca raspunsul e deja local
        cached = self.http.get_json(
            url, params, ttl_hours=ttl_hours, use_cache=True,
            extra_headers=self.auth_headers,
        ) if self._is_cached(url, params, ttl_hours) else None

        if cached is None:
            if self.budget_left <= 0:
                raise RuntimeError(
                    f"buget de request-uri epuizat ({self.max_requests}); "
                    "creste max_requests sau reia maine"
                )
            self.requests_used += 1
            cached = self.http.get_json(
                url, params, ttl_hours=ttl_hours, use_cache=True,
                extra_headers=self.auth_headers,
            )

        errors = cached.get("errors")
        if errors and (isinstance(errors, dict) or isinstance(errors, list)):
            if errors:
                # Raspunsul de eroare a fost deja scris in cache de HttpClient.
                # Il stergem, altfel ar fi servit ca rezultat valid la
                # urmatoarele rulari si ar otravi baza de date.
                self._drop_cache(url, params)
                if isinstance(errors, dict) and (
                        set(errors) & TRANSIENT_ERROR_KEYS):
                    raise RateLimitedError(f"API-Football: {errors}")
                raise RuntimeError(f"API-Football: {errors}")
        return cached.get("response", []) or []

    def _drop_cache(self, url: str, params: dict | None) -> None:
        key = self.http._cache_key(url, params)  # noqa: SLF001
        path = self.http._cache_file(key, ".txt")  # noqa: SLF001
        path.unlink(missing_ok=True)

    def _is_cached(self, url: str, params: dict | None,
                   ttl_hours: float | None) -> bool:
        key = self.http._cache_key(url, params)  # noqa: SLF001 - acelasi pachet
        path = self.http._cache_file(key, ".txt")  # noqa: SLF001
        return self.http._read_cache(path, ttl_hours) is not None  # noqa: SLF001

    # ------------------------------------------------------------------
    # Meciuri
    # ------------------------------------------------------------------
    def fetch_fixtures(self, season: int) -> IngestResult:
        """Toate meciurile unui sezon: 1 singur request."""
        result = IngestResult(source=self.name)
        fixtures = self._get(
            "fixtures", {"league": self.league_id, "season": season}, ttl_hours=6
        )
        for item in fixtures:
            fx = item.get("fixture", {})
            teams = item.get("teams", {})
            goals = item.get("goals", {})
            score = item.get("score", {}) or {}
            halftime = score.get("halftime", {}) or {}
            league = item.get("league", {}) or {}
            status = (fx.get("status") or {}).get("short", "")

            home_name = (teams.get("home") or {}).get("name")
            away_name = (teams.get("away") or {}).get("name")
            if not home_name or not away_name:
                continue

            # inregistram si ID-urile externe ale echipelor
            for side in ("home", "away"):
                t = teams.get(side) or {}
                if t.get("id"):
                    self.db.upsert_team(
                        t["name"], source=self.name, ext_id=str(t["id"]),
                        logo_url=t.get("logo"),
                    )

            venue = (fx.get("venue") or {}).get("name")
            round_name = league.get("round")
            stage = self._stage_from_round(round_name)

            self.db.upsert_match(
                season=season,
                utc_date=fx.get("date") or "",
                home=home_name,
                away=away_name,
                home_goals=self.to_int(goals.get("home")),
                away_goals=self.to_int(goals.get("away")),
                ht_home_goals=self.to_int(halftime.get("home")),
                ht_away_goals=self.to_int(halftime.get("away")),
                status="finished" if status in FINISHED_STATUSES else "scheduled",
                stage=stage,
                round_=round_name,
                venue=venue,
                referee=fx.get("referee"),
                source=self.name,
                ext_id=str(fx.get("id")),
            )
            result.matches += 1
        return result

    @staticmethod
    def _stage_from_round(round_name: str | None) -> str | None:
        if not round_name:
            return None
        low = round_name.lower()
        if "relegation" in low or "play-out" in low or "playout" in low:
            return "playout"
        if "championship" in low or "play-off" in low or "playoff" in low:
            return "playoff"
        return "regular"

    # ------------------------------------------------------------------
    # Statistici de echipa pe meci
    # ------------------------------------------------------------------
    def fetch_match_statistics(self, fixture_ext_id: str,
                               match_id: int) -> int:
        """Statistici pentru un meci. Costa 1 request. Intoarce nr. de echipe scrise."""
        rows = self._get("fixtures/statistics", {"fixture": fixture_ext_id},
                         ttl_hours=-1)  # statisticile unui meci jucat nu se schimba
        written = 0
        for team_block in rows:
            team = team_block.get("team") or {}
            if not team.get("name"):
                continue
            team_id = self.db.upsert_team(
                team["name"], source=self.name, ext_id=str(team.get("id") or ""))
            stats: dict[str, Any] = {}
            for entry in team_block.get("statistics") or []:
                col = STAT_MAP.get(entry.get("type") or "")
                if col:
                    stats[col] = self.to_float(entry.get("value"))
            if not stats:
                continue
            is_home = self._is_home(match_id, team_id)
            self.db.upsert_team_stats(match_id, team_id, is_home, stats,
                                      source=self.name)
            written += 1
        return written

    def _is_home(self, match_id: int, team_id: int) -> bool:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT home_team_id FROM matches WHERE match_id=?", (match_id,)
            ).fetchone()
        return bool(row and int(row["home_team_id"]) == team_id)

    # ------------------------------------------------------------------
    # Statistici individuale
    # ------------------------------------------------------------------
    def fetch_match_players(self, fixture_ext_id: str, match_id: int) -> int:
        """Statistici pe jucator pentru un meci. Costa 1 request."""
        rows = self._get("fixtures/players", {"fixture": fixture_ext_id},
                         ttl_hours=-1)
        written = 0
        for team_block in rows:
            team = team_block.get("team") or {}
            if not team.get("name"):
                continue
            team_id = self.db.upsert_team(
                team["name"], source=self.name, ext_id=str(team.get("id") or ""))

            for entry in team_block.get("players") or []:
                p = entry.get("player") or {}
                stat_list = entry.get("statistics") or []
                if not p.get("name") or not stat_list:
                    continue
                st = stat_list[0]
                games = st.get("games") or {}
                player_id = self.db.upsert_player(
                    p["name"], team_id, source=self.name,
                    ext_id=str(p.get("id") or ""),
                    position=games.get("position"), photo_url=p.get("photo"),
                )
                self.db.upsert_player_stats(
                    match_id, player_id, team_id,
                    self._player_stats_dict(st), source=self.name,
                )
                written += 1
        return written

    def _player_stats_dict(self, st: dict) -> dict[str, Any]:
        games = st.get("games") or {}
        shots = st.get("shots") or {}
        goals = st.get("goals") or {}
        passes = st.get("passes") or {}
        tackles = st.get("tackles") or {}
        duels = st.get("duels") or {}
        dribbles = st.get("dribbles") or {}
        fouls = st.get("fouls") or {}
        cards = st.get("cards") or {}
        penalty = st.get("penalty") or {}
        return {
            "minutes": self.to_float(games.get("minutes")),
            "is_starter": 0 if games.get("substitute") else 1,
            "position": games.get("position"),
            "rating": self.to_float(games.get("rating")),
            "goals": self.to_float(goals.get("total")) or 0.0,
            "assists": self.to_float(goals.get("assists")) or 0.0,
            "shots": self.to_float(shots.get("total")),
            "shots_on_target": self.to_float(shots.get("on")),
            "passes": self.to_float(passes.get("total")),
            "key_passes": self.to_float(passes.get("key")),
            "pass_accuracy": self.to_float(passes.get("accuracy")),
            "tackles": self.to_float(tackles.get("total")),
            "interceptions": self.to_float(tackles.get("interceptions")),
            "duels_total": self.to_float(duels.get("total")),
            "duels_won": self.to_float(duels.get("won")),
            "dribbles_att": self.to_float(dribbles.get("attempts")),
            "dribbles_succ": self.to_float(dribbles.get("success")),
            "fouls_drawn": self.to_float(fouls.get("drawn")),
            "fouls_committed": self.to_float(fouls.get("committed")),
            "yellow_cards": self.to_float(cards.get("yellow")) or 0.0,
            "red_cards": self.to_float(cards.get("red")) or 0.0,
            "penalties_scored": self.to_float(penalty.get("scored")),
            "penalties_missed": self.to_float(penalty.get("missed")),
            "saves": self.to_float(goals.get("saves")),
            "goals_conceded": self.to_float(goals.get("conceded")),
        }

    # ------------------------------------------------------------------
    # Clasament
    # ------------------------------------------------------------------
    def fetch_standings(self, season: int) -> int:
        rows = self._get("standings", {"league": self.league_id, "season": season},
                         ttl_hours=12)
        written = 0
        for league_block in rows:
            groups = (league_block.get("league") or {}).get("standings") or []
            for group in groups:
                for entry in group:
                    team = entry.get("team") or {}
                    if not team.get("name"):
                        continue
                    team_id = self.db.upsert_team(
                        team["name"], source=self.name,
                        ext_id=str(team.get("id") or ""))
                    all_ = entry.get("all") or {}
                    goals = all_.get("goals") or {}
                    stage = (entry.get("group") or "regular").lower()
                    stage = ("playoff" if "champion" in stage or "play-off" in stage
                             else "playout" if "releg" in stage else "regular")
                    self.db.upsert_standing(
                        season, stage, team_id,
                        rank=entry.get("rank"), played=all_.get("played"),
                        wins=all_.get("win"), draws=all_.get("draw"),
                        losses=all_.get("lose"),
                        goals_for=goals.get("for"), goals_against=goals.get("against"),
                        points=entry.get("points"), form=entry.get("form"),
                    )
                    written += 1
        return written

    # ------------------------------------------------------------------
    # Orchestrare
    # ------------------------------------------------------------------
    def fetch_season(self, season: int, *, with_details: bool = True) -> IngestResult:
        if not self.supports_season(season):
            log.info(
                "[%s] sezonul %s nu e acoperit de planul curent (%s-%s) - sar peste",
                self.name, season, self.min_season, self.max_season,
            )
            return IngestResult(source=self.name)

        result = self.fetch_fixtures(season)
        try:
            self.fetch_standings(season)
        except Exception as exc:  # noqa: BLE001
            result.errors.append(f"clasament {season}: {exc}")

        if with_details:
            for fixture_ext_id, match_id in self._fixtures_missing_stats(season):
                if self.budget_left <= 1:
                    result.errors.append(
                        "buget epuizat - statisticile detaliate raman incomplete")
                    break
                try:
                    result.team_stats += self.fetch_match_statistics(
                        fixture_ext_id, match_id)
                    if self.budget_left > 0:
                        result.player_stats += self.fetch_match_players(
                            fixture_ext_id, match_id)
                except RateLimitedError as exc:
                    result.errors.append(f"limita de cereri atinsa: {exc}")
                    break
                except Exception as exc:  # noqa: BLE001
                    result.errors.append(f"meci {fixture_ext_id}: {exc}")
                    if "buget" in str(exc):
                        break
        return result

    def _fixtures_missing_stats(self, season: int) -> list[tuple[str, int]]:
        """Meciurile jucate fara statistici, cele mai recente intai."""
        sql = """
            SELECT e.ext_id, m.match_id
            FROM matches m
            JOIN match_external_ids e
              ON e.match_id = m.match_id AND e.source = ?
            LEFT JOIN match_team_stats s ON s.match_id = m.match_id
            WHERE m.season = ? AND m.status = 'finished' AND s.match_id IS NULL
            ORDER BY m.utc_date DESC
        """
        with self.db.connect() as conn:
            rows = conn.execute(sql, (self.name, season)).fetchall()
        return [(str(r["ext_id"]), int(r["match_id"])) for r in rows]

    # ------------------------------------------------------------------
    def fetch_upcoming(self) -> IngestResult:
        """Meciurile programate din sezonul curent (1 request)."""
        result = IngestResult(source=self.name)
        try:
            fixtures = self._get(
                "fixtures",
                {"league": self.league_id, "season": self.cfg.current_season,
                 "status": "NS"},
                ttl_hours=1,
            )
        except Exception as exc:  # noqa: BLE001
            result.errors.append(str(exc))
            return result

        for item in fixtures:
            fx = item.get("fixture", {})
            teams = item.get("teams", {})
            league = item.get("league", {}) or {}
            home = (teams.get("home") or {}).get("name")
            away = (teams.get("away") or {}).get("name")
            if not home or not away:
                continue
            self.db.upsert_match(
                season=self.cfg.current_season,
                utc_date=fx.get("date") or "",
                home=home, away=away, status="scheduled",
                stage=self._stage_from_round(league.get("round")),
                round_=league.get("round"),
                venue=(fx.get("venue") or {}).get("name"),
                source=self.name, ext_id=str(fx.get("id")),
            )
            result.matches += 1
        return result

    def check_status(self) -> dict[str, Any]:
        """Cate request-uri mai are contul azi (endpoint /status, 1 request)."""
        rows = self._get("status", ttl_hours=1)
        if isinstance(rows, dict):
            return rows
        return rows[0] if rows else {}
