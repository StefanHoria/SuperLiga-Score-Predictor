"""Interfata comuna a providerilor de date."""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from ..config import Config, get_config
from ..storage.db import Database
from ..utils.http import HttpClient

log = logging.getLogger(__name__)


@dataclass
class IngestResult:
    """Ce a reusit sa aduca un provider intr-o rulare."""

    source: str
    matches: int = 0
    team_stats: int = 0
    player_stats: int = 0
    odds: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.matches + self.team_stats + self.player_stats + self.odds

    @property
    def ok(self) -> bool:
        return not self.errors

    def merge(self, other: "IngestResult") -> "IngestResult":
        self.matches += other.matches
        self.team_stats += other.team_stats
        self.player_stats += other.player_stats
        self.odds += other.odds
        self.errors.extend(other.errors)
        return self

    def describe(self) -> str:
        parts = [
            f"meciuri={self.matches}",
            f"stat_echipa={self.team_stats}",
            f"stat_jucatori={self.player_stats}",
            f"cote={self.odds}",
        ]
        line = f"[{self.source}] " + " ".join(parts)
        if self.errors:
            line += f" | {len(self.errors)} erori: {self.errors[0][:120]}"
        return line


class DataProvider(ABC):
    """Contractul pe care il implementeaza fiecare sursa de date.

    Un provider stie sa scrie in baza de date, dar nu stie nimic despre
    modele sau despre dashboard.
    """

    #: identificator scurt, folosit in coloana `source` din DB
    name: str = "base"
    #: ce poate livra providerul
    provides: tuple[str, ...] = ()
    #: problema cunoscuta care il face nefolosibil (gol = merge)
    known_issue: str = ""

    def __init__(self, db: Database | None = None, cfg: Config | None = None) -> None:
        self.cfg = cfg or get_config()
        self.db = db or Database()
        self.http = HttpClient(
            cache_dir=self.cfg.path("cache"),
            delay=self.cfg.secrets.scrape_delay,
            ttl_hours=self.cfg.secrets.http_cache_ttl_hours,
            name=self.name,
        )

    # ------------------------------------------------------------------
    @property
    @abstractmethod
    def available(self) -> bool:
        """True daca providerul poate fi folosit (cheie prezenta, activat etc.)."""

    @abstractmethod
    def fetch_season(self, season: int) -> IngestResult:
        """Descarca tot ce poate providerul pentru un sezon si scrie in DB."""

    # ------------------------------------------------------------------
    def fetch_seasons(self, seasons: list[int] | None = None) -> IngestResult:
        seasons = seasons or self.cfg.seasons
        total = IngestResult(source=self.name)
        for season in seasons:
            if not self.available:
                total.errors.append(f"{self.name} indisponibil")
                break
            try:
                log.info("[%s] descarc sezonul %s", self.name, season)
                total.merge(self.fetch_season(season))
            except Exception as exc:  # noqa: BLE001 - vrem sa continuam cu restul
                msg = f"sezon {season}: {exc}"
                log.exception("[%s] %s", self.name, msg)
                total.errors.append(msg)
        self.db.log_ingest(
            self.name,
            f"sezoane {seasons}",
            total.total,
            "ok" if total.ok else "partial",
            "; ".join(total.errors),
        )
        return total

    def fetch_upcoming(self) -> IngestResult:
        """Descarca meciurile viitoare. Implicit: nimic (nu toti providerii pot)."""
        return IngestResult(source=self.name)

    # ------------------------------------------------------------------
    @staticmethod
    def to_float(value: Any) -> float | None:
        """Converteste valori de tip '52%', '13', None, '' in float sau None."""
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return float(value)
        text = str(value).strip().replace("%", "").replace(",", ".")
        if not text or text.lower() in {"none", "null", "-", "n/a"}:
            return None
        try:
            return float(text)
        except ValueError:
            return None

    @staticmethod
    def to_int(value: Any) -> int | None:
        f = DataProvider.to_float(value)
        return int(round(f)) if f is not None else None

    def status(self) -> dict[str, Any]:
        if not self.available:
            state = "oprit din .env"
        elif self.known_issue:
            state = "blocat"
        else:
            state = "activ"
        return {
            "sursa": self.name,
            "stare": state,
            "ofera": ", ".join(self.provides),
            "observatii": self.known_issue,
        }
