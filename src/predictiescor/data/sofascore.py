"""Provider Sofascore (API public neoficial) - statistici bogate pe meci.

STARE CUNOSCUTA: Sofascore raspunde 403 la cereri din afara browserului, deci
providerul e de obicei indisponibil. Codul e complet si corect; daca ai acces
legitim (de exemplu rulezi de pe o retea care nu e blocata, sau ai propriile
cookie-uri de sesiune), il activezi din .env si merge fara alte modificari.

Nu incercam sa ocolim protectia anti-bot: la 403 providerul se opreste curat
si raporteaza motivul, iar restul aplicatiei foloseste celelalte surse.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from .base import DataProvider, IngestResult

log = logging.getLogger(__name__)

API = "https://api.sofascore.com/api/v1"

# Numele statisticii la Sofascore -> coloana din match_team_stats
STAT_MAP: dict[str, str] = {
    "Total shots": "shots",
    "Shots on target": "shots_on_target",
    "Shots off target": "shots_off_target",
    "Blocked shots": "shots_blocked",
    "Shots inside box": "shots_inside_box",
    "Shots outside box": "shots_outside_box",
    "Corner kicks": "corners",
    "Offsides": "offsides",
    "Fouls": "fouls",
    "Yellow cards": "yellow_cards",
    "Red cards": "red_cards",
    "Ball possession": "possession",
    "Passes": "passes",
    "Accurate passes": "passes_accurate",
    "Goalkeeper saves": "saves",
    "Expected goals": "xg",
    "Big chances": "big_chances",
    "Tackles": "tackles",
}


class SofascoreBlocked(RuntimeError):
    """Sofascore a refuzat cererea (403) - providerul nu poate fi folosit."""


class SofascoreProvider(DataProvider):
    name = "sofascore"
    provides = (
        "suturi", "cornere", "cartonase", "posesie", "xG", "ocazii mari",
        "formatii",
    )
    known_issue = "raspunde 403 la acces automat"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.tournament_id = int(self.cfg.league["sofascore_tournament_id"])
        self._season_ids: dict[int, int] = {}

    @property
    def available(self) -> bool:
        return self.cfg.secrets.sofascore_enabled

    @property
    def extra_headers(self) -> dict[str, str]:
        headers = {
            "Accept": "*/*",
            "Referer": "https://www.sofascore.com/",
            "Origin": "https://www.sofascore.com",
        }
        # Optional: cookie propriu de sesiune, pus de utilizator in .env
        cookie = os.getenv("SOFASCORE_COOKIE", "").strip()
        if cookie:
            headers["Cookie"] = cookie
        return headers

    # ------------------------------------------------------------------
    def _get(self, path: str, *, ttl_hours: float | None = None) -> dict:
        try:
            return self.http.get_json(
                f"{API}/{path.lstrip('/')}", ttl_hours=ttl_hours,
                extra_headers=self.extra_headers,
            )
        except Exception as exc:  # noqa: BLE001
            if "403" in str(exc) or "Forbidden" in str(exc):
                raise SofascoreBlocked(
                    "Sofascore a raspuns 403 (acces automat blocat). "
                    "Seteaza SOFASCORE_ENABLED=false in .env sau furnizeaza "
                    "SOFASCORE_COOKIE daca ai acces legitim."
                ) from exc
            raise

    def season_id(self, season: int) -> int | None:
        """Sofascore identifica sezoanele prin ID propriu, nu prin an."""
        if not self._season_ids:
            data = self._get(
                f"unique-tournament/{self.tournament_id}/seasons", ttl_hours=168)
            for s in data.get("seasons", []):
                year = str(s.get("year", ""))
                # 'year' e "25/26" sau "2025"
                if "/" in year:
                    start = 2000 + int(year.split("/")[0])
                else:
                    start = int(year[:4]) if year[:4].isdigit() else 0
                if start:
                    self._season_ids[start] = int(s["id"])
        return self._season_ids.get(season)

    # ------------------------------------------------------------------
    def fetch_season(self, season: int) -> IngestResult:
        result = IngestResult(source=self.name)
        try:
            sid = self.season_id(season)
        except SofascoreBlocked as exc:
            result.errors.append(str(exc))
            return result

        if sid is None:
            result.errors.append(f"sezonul {season} nu exista la Sofascore")
            return result

        events = self._fetch_events(sid)
        for ev in events:
            try:
                result.merge(self._ingest_event(ev, season))
            except SofascoreBlocked as exc:
                result.errors.append(str(exc))
                break
            except Exception as exc:  # noqa: BLE001
                result.errors.append(f"eveniment {ev.get('id')}: {exc}")
        return result

    def _fetch_events(self, season_id: int) -> list[dict]:
        """Paginam evenimentele trecute pana se termina."""
        events: list[dict] = []
        page = 0
        while page < 40:  # plafon de siguranta
            try:
                data = self._get(
                    f"unique-tournament/{self.tournament_id}/season/{season_id}"
                    f"/events/last/{page}",
                    ttl_hours=12,
                )
            except SofascoreBlocked:
                raise
            except Exception:
                break
            batch = data.get("events") or []
            if not batch:
                break
            events.extend(batch)
            if not data.get("hasNextPage"):
                break
            page += 1
        return events

    def _ingest_event(self, ev: dict, season: int) -> IngestResult:
        result = IngestResult(source=self.name)
        home = (ev.get("homeTeam") or {}).get("name")
        away = (ev.get("awayTeam") or {}).get("name")
        if not home or not away:
            return result

        ts = ev.get("startTimestamp")
        from datetime import datetime, timezone
        utc_date = (datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
                    if ts else "")
        status = (ev.get("status") or {}).get("type", "")

        match_id = self.db.upsert_match(
            season=season,
            utc_date=utc_date,
            home=home,
            away=away,
            home_goals=self.to_int((ev.get("homeScore") or {}).get("current")),
            away_goals=self.to_int((ev.get("awayScore") or {}).get("current")),
            ht_home_goals=self.to_int((ev.get("homeScore") or {}).get("period1")),
            ht_away_goals=self.to_int((ev.get("awayScore") or {}).get("period1")),
            status="finished" if status == "finished" else "scheduled",
            round_=str((ev.get("roundInfo") or {}).get("round") or ""),
            source=self.name, ext_id=str(ev.get("id")),
        )
        result.matches += 1

        if status == "finished":
            result.team_stats += self._ingest_statistics(ev["id"], match_id,
                                                         home, away)
        return result

    def _ingest_statistics(self, event_id: int, match_id: int,
                           home: str, away: str) -> int:
        data = self._get(f"event/{event_id}/statistics", ttl_hours=-1)
        blocks = data.get("statistics") or []
        overall = next((b for b in blocks if b.get("period") == "ALL"), None)
        if overall is None:
            return 0

        home_stats: dict[str, Any] = {}
        away_stats: dict[str, Any] = {}
        for group in overall.get("groups") or []:
            for item in group.get("statisticsItems") or []:
                col = STAT_MAP.get(item.get("name") or "")
                if not col:
                    continue
                home_stats[col] = self.to_float(
                    item.get("homeValue", item.get("home")))
                away_stats[col] = self.to_float(
                    item.get("awayValue", item.get("away")))

        written = 0
        for name, stats, is_home in ((home, home_stats, True),
                                     (away, away_stats, False)):
            if not stats:
                continue
            team_id = self.db.upsert_team(name)
            self.db.upsert_team_stats(match_id, team_id, is_home, stats,
                                      source=self.name)
            written += 1
        return written
