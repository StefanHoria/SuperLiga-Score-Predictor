"""Provider FBref (Opta/StatsBomb) - statistici avansate si xG.

STARE CUNOSCUTA: FBref sta in spatele unei provocari Cloudflare ("Just a
moment..."), deci cererile automate primesc 403. Nu incercam sa ocolim
protectia: providerul detecteaza pagina de blocare, se opreste curat si spune
de ce. Daca FBref devine accesibil (sau rulezi dintr-un mediu neblocat),
providerul functioneaza fara modificari.

Ce ar aduce in plus fata de API-Football: xG/xGA pe echipa din alt furnizor de
date, plus statistici avansate de posesie si presiune.
"""
from __future__ import annotations

import io
import logging
import re

import pandas as pd

from ..utils.teamnames import canonical_team
from .base import DataProvider, IngestResult

log = logging.getLogger(__name__)

BASE = "https://fbref.com"

BLOCK_MARKERS = ("just a moment", "cf-browser-verification", "attention required",
                 "enable javascript and cookies")

# Coloana din tabelul "Scores & Fixtures" -> coloana din match_team_stats
SCORE_COLUMNS = {
    "xG": "xg",
    "xGA": "xga",
}


class FbrefBlocked(RuntimeError):
    """FBref a returnat pagina de verificare Cloudflare."""


class FbrefProvider(DataProvider):
    name = "fbref"
    provides = ("xG / xGA", "statistici avansate de echipa", "rezultate")
    known_issue = "protejat de Cloudflare"

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.comp_id = int(self.cfg.league["fbref_comp_id"])
        self.comp_slug = str(self.cfg.league["fbref_comp_slug"])

    @property
    def available(self) -> bool:
        return self.cfg.secrets.fbref_enabled

    # ------------------------------------------------------------------
    def _get_html(self, url: str, *, ttl_hours: float | None = None) -> str:
        html = self.http.get_text(url, ttl_hours=ttl_hours)
        low = html[:4000].lower()
        if any(marker in low for marker in BLOCK_MARKERS):
            raise FbrefBlocked(
                "FBref a returnat provocarea Cloudflare. Accesul automat este "
                "blocat momentan; seteaza FBREF_ENABLED=false in .env pentru a "
                "elimina sursa din rulari."
            )
        return html

    @staticmethod
    def _read_tables(html: str) -> list[pd.DataFrame]:
        """FBref ascunde multe tabele in comentarii HTML - le scoatem la iveala."""
        uncommented = re.sub(r"<!--|-->", "", html)
        try:
            return pd.read_html(io.StringIO(uncommented))
        except ValueError:
            return []

    def season_url(self, season: int) -> str:
        label = f"{season}-{season + 1}"
        return (f"{BASE}/en/comps/{self.comp_id}/{label}/schedule/"
                f"{label}-{self.comp_slug}-Scores-and-Fixtures")

    # ------------------------------------------------------------------
    def fetch_season(self, season: int) -> IngestResult:
        result = IngestResult(source=self.name)
        try:
            html = self._get_html(self.season_url(season), ttl_hours=24)
        except FbrefBlocked as exc:
            result.errors.append(str(exc))
            return result
        except Exception as exc:  # noqa: BLE001
            result.errors.append(f"sezon {season}: {exc}")
            return result

        tables = self._read_tables(html)
        fixtures = next(
            (t for t in tables
             if {"Home", "Away", "Score"} <= set(map(str, t.columns))),
            None,
        )
        if fixtures is None:
            result.errors.append(
                f"tabelul de meciuri lipseste pentru sezonul {season}")
            return result

        for _, row in fixtures.iterrows():
            home, away = row.get("Home"), row.get("Away")
            date = row.get("Date")
            if not isinstance(home, str) or not isinstance(away, str):
                continue
            if pd.isna(date):
                continue

            home_goals, away_goals = self._parse_score(row.get("Score"))
            match_id = self.db.upsert_match(
                season=season,
                utc_date=f"{pd.to_datetime(date).date()}T00:00:00+00:00",
                home=home, away=away,
                home_goals=home_goals, away_goals=away_goals,
                status="finished" if home_goals is not None else "scheduled",
                round_=str(row.get("Wk") or "") or None,
                venue=str(row.get("Venue") or "") or None,
                referee=str(row.get("Referee") or "") or None,
                source=self.name,
                ext_id=f"{season}_{canonical_team(home)}_{canonical_team(away)}",
            )
            result.matches += 1

            # xG este singura coloana pe care FBref o da direct in acest tabel
            for side, team_name, is_home in (("xG", home, True),
                                             ("xG.1", away, False)):
                value = self.to_float(row.get(side))
                if value is None:
                    continue
                team_id = self.db.upsert_team(team_name)
                self.db.upsert_team_stats(
                    match_id, team_id, is_home, {"xg": value}, source=self.name)
                result.team_stats += 1
        return result

    @staticmethod
    def _parse_score(score: object) -> tuple[int | None, int | None]:
        """'2–1' (en dash) -> (2, 1)."""
        if not isinstance(score, str):
            return None, None
        m = re.match(r"^\s*(\d+)\s*[–\-:]\s*(\d+)\s*$", score)
        if not m:
            return None, None
        return int(m.group(1)), int(m.group(2))
