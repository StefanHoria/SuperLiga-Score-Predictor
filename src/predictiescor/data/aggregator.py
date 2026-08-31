"""Orchestrarea providerilor de date.

Ordinea conteaza: providerii ruleaza de la cel mai de incredere la cel mai
fragil, iar `upsert_*` din stratul de stocare completeaza doar campurile goale
(COALESCE), asa ca o sursa mai slaba nu suprascrie niciodata o sursa mai buna.
"""
from __future__ import annotations

import logging
from typing import Any, Iterable

from ..config import Config, get_config
from ..storage.db import Database
from .apifootball import ApiFootballProvider
from .base import DataProvider, IngestResult
from .fbref import FbrefProvider
from .flashscore import FlashscoreProvider
from .footballdata import FootballDataProvider
from .lpf import LpfProvider
from .sofascore import SofascoreProvider
from .transfermarkt import TransfermarktProvider

log = logging.getLogger(__name__)

#: ordinea de rulare (prima are prioritate la completarea campurilor)
PROVIDER_ORDER: list[type[DataProvider]] = [
    FootballDataProvider,   # rezultate + cote, toate sezoanele, foarte stabil
    FlashscoreProvider,     # scor live + rezultate rapide, sezonul curent
    LpfProvider,            # statistici oficiale pe meci, SEZONUL CURENT
    ApiFootballProvider,    # statistici bogate pe meci si jucator (2022-2024)
    TransfermarktProvider,  # loturi si statistici sezoniere (sezonul curent)
    SofascoreProvider,      # statistici bogate - de obicei blocat
    FbrefProvider,          # xG alternativ - de obicei blocat
]


class DataAggregator:
    """Ruleaza toti providerii disponibili si consolideaza rezultatele."""

    def __init__(self, db: Database | None = None, cfg: Config | None = None,
                 *, providers: Iterable[type[DataProvider]] | None = None) -> None:
        self.cfg = cfg or get_config()
        self.db = db or Database()
        self.providers: list[DataProvider] = [
            cls(db=self.db, cfg=self.cfg)
            for cls in (providers or PROVIDER_ORDER)
        ]

    # ------------------------------------------------------------------
    def enabled(self) -> list[DataProvider]:
        return [p for p in self.providers if p.available]

    def status(self) -> list[dict[str, Any]]:
        rows = []
        for p in self.providers:
            row = p.status()
            if isinstance(p, ApiFootballProvider) and p.available:
                row["sezoane"] = f"{p.min_season}-{p.max_season}"
                row["buget"] = f"{p.budget_left}/{p.max_requests}"
            rows.append(row)
        return rows

    # ------------------------------------------------------------------
    def refresh(self, seasons: list[int] | None = None,
                *, sources: list[str] | None = None) -> dict[str, IngestResult]:
        """Descarca datele pentru sezoanele cerute din toate sursele active."""
        seasons = seasons or self.cfg.seasons
        results: dict[str, IngestResult] = {}

        for provider in self.providers:
            if sources and provider.name not in sources:
                continue
            if not provider.available:
                log.info("[%s] indisponibil - sar peste", provider.name)
                results[provider.name] = IngestResult(
                    source=provider.name, errors=["indisponibil"])
                continue
            log.info("=== %s ===", provider.name)
            results[provider.name] = provider.fetch_seasons(seasons)
            log.info(results[provider.name].describe())

        self._post_merge_cleanup()
        return results

    def refresh_upcoming(self) -> dict[str, IngestResult]:
        """Actualizeaza doar meciurile care urmeaza (ieftin ca request-uri)."""
        results: dict[str, IngestResult] = {}
        for provider in self.providers:
            if not provider.available:
                continue
            try:
                results[provider.name] = provider.fetch_upcoming()
            except Exception as exc:  # noqa: BLE001
                results[provider.name] = IngestResult(
                    source=provider.name, errors=[str(exc)])
        return results

    # ------------------------------------------------------------------
    def _post_merge_cleanup(self) -> None:
        """Semnaleaza echipele ramase fara meciuri (variante de nume noi)."""
        orphans = self.db.orphan_teams()
        if not orphans.empty:
            log.warning(
                "Echipe fara meciuri (probabil variante de nume nemapate): %s. "
                "Foloseste Database.merge_teams() sau adauga aliasul in "
                "utils/teamnames.py.",
                ", ".join(orphans["name"].tolist()),
            )

    def coverage(self) -> dict[str, Any]:
        """Cat de complete sunt datele - afisat pe pagina de administrare."""
        matches = self.db.matches_df(finished_only=True)
        stats = self.db.team_stats_df()
        summary = self.db.summary()
        by_season: dict[int, dict[str, int]] = {}
        if not matches.empty:
            for season, group in matches.groupby("season"):
                ids = set(group["match_id"])
                with_stats = (set(stats["match_id"]) & ids) if not stats.empty else set()
                by_season[int(season)] = {
                    "meciuri": len(ids),
                    "cu_statistici": len(with_stats),
                    "procent": round(100 * len(with_stats) / max(1, len(ids))),
                }
        return {"total": summary, "pe_sezon": by_season}
