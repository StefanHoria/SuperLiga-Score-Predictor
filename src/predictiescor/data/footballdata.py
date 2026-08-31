"""Provider football-data.co.uk - rezultate istorice + cote, gratuit, fara cheie.

Fisierul https://www.football-data.co.uk/new/ROU.csv contine toate sezoanele
din SuperLiga intr-un singur CSV: data, echipe, scor final si cote 1X2
(Pinnacle / maxim / medie piata) + uneori Over-Under 2.5.

E sursa cea mai fiabila pentru *rezultate* si singura sursa gratuita de *cote*,
dar NU contine suturi, cornere sau cartonase - alea vin din API-Football,
Sofascore si FBref.
"""
from __future__ import annotations

import io
import logging
import re

import pandas as pd

from .base import DataProvider, IngestResult

log = logging.getLogger(__name__)

CSV_URL = "https://www.football-data.co.uk/new/ROU.csv"

# Mapare coloana CSV -> (bookmaker, piata, selectie) pentru tabelul `odds`.
# Fisierul ROU.csv foloseste sufixul "C" = cote de inchidere (closing odds),
# cele mai informative pentru calibrare. Pastram si variantele fara "C"
# pentru cazul in care football-data schimba formatul.
ODDS_COLUMNS: dict[str, tuple[str, str, str]] = {
    # --- cote de inchidere (formatul curent) ---
    "PSCH":   ("pinnacle", "1x2", "home"),
    "PSCD":   ("pinnacle", "1x2", "draw"),
    "PSCA":   ("pinnacle", "1x2", "away"),
    "MaxCH":  ("max",      "1x2", "home"),
    "MaxCD":  ("max",      "1x2", "draw"),
    "MaxCA":  ("max",      "1x2", "away"),
    "AvgCH":  ("avg",      "1x2", "home"),
    "AvgCD":  ("avg",      "1x2", "draw"),
    "AvgCA":  ("avg",      "1x2", "away"),
    "B365CH": ("bet365",   "1x2", "home"),
    "B365CD": ("bet365",   "1x2", "draw"),
    "B365CA": ("bet365",   "1x2", "away"),
    "BFECH":  ("betfair",  "1x2", "home"),
    "BFECD":  ("betfair",  "1x2", "draw"),
    "BFECA":  ("betfair",  "1x2", "away"),
    # --- cote de deschidere / formate mai vechi ---
    "PSH":    ("pinnacle_open", "1x2", "home"),
    "PSD":    ("pinnacle_open", "1x2", "draw"),
    "PSA":    ("pinnacle_open", "1x2", "away"),
    "MaxH":   ("max_open",      "1x2", "home"),
    "MaxD":   ("max_open",      "1x2", "draw"),
    "MaxA":   ("max_open",      "1x2", "away"),
    "AvgH":   ("avg_open",      "1x2", "home"),
    "AvgD":   ("avg_open",      "1x2", "draw"),
    "AvgA":   ("avg_open",      "1x2", "away"),
    # --- Over/Under 2.5, daca apare ---
    "P>2.5":   ("pinnacle", "ou_2.5", "over"),
    "P<2.5":   ("pinnacle", "ou_2.5", "under"),
    "PC>2.5":  ("pinnacle", "ou_2.5", "over"),
    "PC<2.5":  ("pinnacle", "ou_2.5", "under"),
    "Max>2.5": ("max",      "ou_2.5", "over"),
    "Max<2.5": ("max",      "ou_2.5", "under"),
    "Avg>2.5": ("avg",      "ou_2.5", "over"),
    "Avg<2.5": ("avg",      "ou_2.5", "under"),
    "MaxC>2.5": ("max",     "ou_2.5", "over"),
    "MaxC<2.5": ("max",     "ou_2.5", "under"),
    "AvgC>2.5": ("avg",     "ou_2.5", "over"),
    "AvgC<2.5": ("avg",     "ou_2.5", "under"),
}


def parse_season(value: object) -> int | None:
    """'2023/2024' -> 2023 ; '2024' -> 2024."""
    text = str(value).strip()
    if not text or text.lower() == "nan":
        return None
    match = re.match(r"^(\d{4})", text)
    return int(match.group(1)) if match else None


class FootballDataProvider(DataProvider):
    name = "footballdata"
    provides = ("rezultate", "cote 1X2", "cote O/U 2.5")

    @property
    def available(self) -> bool:
        return self.cfg.secrets.footballdata_enabled

    # ------------------------------------------------------------------
    def load_csv(self, *, refresh: bool = False) -> pd.DataFrame:
        """Descarca (sau ia din cache) CSV-ul cu toate sezoanele din Romania."""
        text = self.http.get_text(CSV_URL, use_cache=not refresh, ttl_hours=6)
        # CSV-ul vine cu BOM UTF-8; fara lstrip prima coloana ar fi "﻿Country"
        df = pd.read_csv(io.StringIO(text.lstrip("﻿")))
        df.columns = [str(c).strip().lstrip("﻿") for c in df.columns]

        df["season_start"] = df["Season"].map(parse_season)
        df["parsed_date"] = pd.to_datetime(
            df["Date"], format="%d/%m/%Y", errors="coerce"
        )
        missing = df["parsed_date"].isna()
        if missing.any():  # unele randuri vechi folosesc alt format
            df.loc[missing, "parsed_date"] = pd.to_datetime(
                df.loc[missing, "Date"], errors="coerce", dayfirst=True
            )
        df = df.dropna(subset=["parsed_date", "Home", "Away"])
        return df

    # ------------------------------------------------------------------
    def fetch_season(self, season: int) -> IngestResult:
        result = IngestResult(source=self.name)
        df = self.load_csv()
        rows = df[df["season_start"] == season]
        if rows.empty:
            log.info("[%s] niciun meci pentru sezonul %s", self.name, season)
            return result

        odds_rows: list[tuple] = []
        for _, row in rows.iterrows():
            home_goals = self.to_int(row.get("HG"))
            away_goals = self.to_int(row.get("AG"))
            played = home_goals is not None and away_goals is not None

            time_part = str(row.get("Time", "")).strip()
            if re.match(r"^\d{1,2}:\d{2}$", time_part):
                utc_date = f"{row['parsed_date'].date()}T{time_part}:00+00:00"
            else:
                utc_date = f"{row['parsed_date'].date()}T00:00:00+00:00"

            match_id = self.db.upsert_match(
                season=season,
                utc_date=utc_date,
                home=str(row["Home"]),
                away=str(row["Away"]),
                home_goals=home_goals,
                away_goals=away_goals,
                status="finished" if played else "scheduled",
                source=self.name,
                ext_id=f"{season}_{row['Home']}_{row['Away']}_{row['parsed_date'].date()}",
            )
            result.matches += 1

            for col, (book, market, selection) in ODDS_COLUMNS.items():
                if col not in rows.columns:
                    continue
                price = self.to_float(row.get(col))
                if price and price > 1.0:
                    odds_rows.append((match_id, book, market, selection, price))

        result.odds = self.db.upsert_odds(odds_rows)
        log.info("[%s] sezon %s: %d meciuri, %d cote",
                 self.name, season, result.matches, result.odds)
        return result

    # ------------------------------------------------------------------
    def available_seasons(self) -> list[int]:
        """Sezoanele prezente in CSV - util pentru diagnostic."""
        df = self.load_csv()
        return sorted(int(s) for s in df["season_start"].dropna().unique())
