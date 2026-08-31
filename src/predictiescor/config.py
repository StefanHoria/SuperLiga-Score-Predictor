"""Incarcarea configuratiei aplicatiei (config.yaml + .env)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]

load_dotenv(PROJECT_ROOT / ".env")


def _env_bool(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class Secrets:
    """Chei si comutatoare citite din .env (niciodata din config.yaml)."""

    apifootball_key: str = ""
    apifootball_host: str = "direct"
    sofascore_enabled: bool = True
    fbref_enabled: bool = True
    footballdata_enabled: bool = True
    transfermarkt_enabled: bool = True
    lpf_enabled: bool = True
    flashscore_enabled: bool = True
    news_enabled: bool = True
    flashscore_odds: bool = True
    scrape_delay: float = 3.0
    http_cache_ttl_hours: float = 12.0

    @classmethod
    def from_env(cls) -> "Secrets":
        return cls(
            apifootball_key=os.getenv("APIFOOTBALL_KEY", "").strip(),
            apifootball_host=os.getenv("APIFOOTBALL_HOST", "direct").strip().lower(),
            sofascore_enabled=_env_bool("SOFASCORE_ENABLED", True),
            fbref_enabled=_env_bool("FBREF_ENABLED", True),
            footballdata_enabled=_env_bool("FOOTBALLDATA_ENABLED", True),
            transfermarkt_enabled=_env_bool("TRANSFERMARKT_ENABLED", True),
            lpf_enabled=_env_bool("LPF_ENABLED", True),
            flashscore_enabled=_env_bool("FLASHSCORE_ENABLED", True),
            news_enabled=_env_bool("NEWS_ENABLED", True),
            flashscore_odds=_env_bool("FLASHSCORE_ODDS", True),
            scrape_delay=_env_float("SCRAPE_DELAY", 3.0),
            http_cache_ttl_hours=_env_float("HTTP_CACHE_TTL_HOURS", 12.0),
        )

    @property
    def has_apifootball(self) -> bool:
        return bool(self.apifootball_key)


@dataclass(frozen=True)
class Config:
    raw: dict[str, Any] = field(repr=False)
    secrets: Secrets = field(default_factory=Secrets.from_env)

    # ---- sectiuni ------------------------------------------------
    @property
    def league(self) -> dict[str, Any]:
        return self.raw["league"]

    @property
    def model(self) -> dict[str, Any]:
        return self.raw["model"]

    @property
    def markets(self) -> dict[str, Any]:
        return self.raw["markets"]

    @property
    def backtest(self) -> dict[str, Any]:
        return self.raw["backtest"]

    # ---- cai absolute --------------------------------------------
    def path(self, key: str) -> Path:
        p = PROJECT_ROOT / self.raw["paths"][key]
        if key != "db":
            p.mkdir(parents=True, exist_ok=True)
        else:
            p.parent.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def db_path(self) -> Path:
        return self.path("db")

    @property
    def seasons(self) -> list[int]:
        return list(self.league["seasons"])

    @property
    def current_season(self) -> int:
        return int(self.league["current_season"])


@lru_cache(maxsize=1)
def get_config(path: str | Path | None = None) -> Config:
    cfg_path = Path(path) if path else PROJECT_ROOT / "config.yaml"
    with open(cfg_path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    return Config(raw=raw, secrets=Secrets.from_env())


def reload_config() -> Config:
    """Forteaza recitirea config.yaml + .env (folosit de butonul din dashboard)."""
    get_config.cache_clear()
    load_dotenv(PROJECT_ROOT / ".env", override=True)
    return get_config()
