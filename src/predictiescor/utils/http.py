"""Client HTTP cu cache pe disc, rate limiting si retry.

Toti providerii trec prin `HttpClient`, ca sa nu lovim niciodata de doua ori
acelasi endpoint intr-o sesiune si sa nu fim banati de FBref/Sofascore.
"""
from __future__ import annotations

import hashlib
import json
import logging
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests

log = logging.getLogger(__name__)

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "ro-RO,ro;q=0.9,en;q=0.8",
}

# Coduri la care merita sa reincercam
RETRY_STATUS = {429, 500, 502, 503, 504}


class RateLimitError(RuntimeError):
    """Providerul a raspuns 429 chiar si dupa toate reincercarile."""


@dataclass
class HttpClient:
    cache_dir: Path
    delay: float = 2.0
    ttl_hours: float = 12.0
    max_retries: int = 3
    timeout: int = 30
    headers: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_HEADERS))
    name: str = "http"

    _last_call: float = field(default=0.0, init=False, repr=False)
    _session: requests.Session | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self.cache_dir = Path(self.cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._session = requests.Session()
        self._session.headers.update(self.headers)

    # ------------------------------------------------------------------
    # cache
    # ------------------------------------------------------------------
    def _cache_key(self, url: str, params: dict | None) -> str:
        payload = url + "|" + json.dumps(params or {}, sort_keys=True)
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]
        return f"{self.name}_{digest}"

    def _cache_file(self, key: str, suffix: str) -> Path:
        return self.cache_dir / f"{key}{suffix}"

    def _read_cache(self, path: Path, ttl_hours: float | None) -> str | None:
        if not path.exists():
            return None
        ttl = self.ttl_hours if ttl_hours is None else ttl_hours
        if ttl >= 0:
            age_h = (time.time() - path.stat().st_mtime) / 3600.0
            if age_h > ttl:
                return None
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return None

    # ------------------------------------------------------------------
    # request
    # ------------------------------------------------------------------
    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_call
        wait = self.delay - elapsed
        if wait > 0:
            time.sleep(wait + random.uniform(0, 0.4))
        self._last_call = time.monotonic()

    def get_text(
        self,
        url: str,
        params: dict | None = None,
        *,
        ttl_hours: float | None = None,
        use_cache: bool = True,
        extra_headers: dict[str, str] | None = None,
    ) -> str:
        """GET care intoarce textul raspunsului, servit din cache daca e proaspat."""
        key = self._cache_key(url, params)
        cache_file = self._cache_file(key, ".txt")

        if use_cache:
            cached = self._read_cache(cache_file, ttl_hours)
            if cached is not None:
                log.debug("[%s] cache hit %s", self.name, url)
                return cached

        last_exc: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            self._throttle()
            try:
                log.info("[%s] GET %s %s", self.name, url, params or "")
                resp = self._session.get(
                    url,
                    params=params,
                    timeout=self.timeout,
                    headers=extra_headers or None,
                )
                if resp.status_code in RETRY_STATUS:
                    backoff = min(60.0, self.delay * (2 ** attempt))
                    log.warning(
                        "[%s] status %s la %s - reincerc in %.1fs (%d/%d)",
                        self.name, resp.status_code, url, backoff, attempt,
                        self.max_retries,
                    )
                    if attempt == self.max_retries and resp.status_code == 429:
                        raise RateLimitError(f"{self.name}: 429 pe {url}")
                    time.sleep(backoff)
                    continue
                resp.raise_for_status()
                text = resp.text
                cache_file.write_text(text, encoding="utf-8")
                return text
            except RateLimitError:
                raise
            except requests.RequestException as exc:
                last_exc = exc
                log.warning("[%s] eroare %s (%d/%d)", self.name, exc, attempt,
                            self.max_retries)
                time.sleep(min(30.0, self.delay * (2 ** attempt)))

        # ultima sansa: cache expirat e mai bun decat nimic
        stale = self._read_cache(cache_file, ttl_hours=-1)
        if stale is not None:
            log.warning("[%s] folosesc cache EXPIRAT pentru %s", self.name, url)
            return stale
        raise RuntimeError(f"{self.name}: GET esuat pentru {url}: {last_exc}")

    def get_json(
        self,
        url: str,
        params: dict | None = None,
        *,
        ttl_hours: float | None = None,
        use_cache: bool = True,
        extra_headers: dict[str, str] | None = None,
    ) -> Any:
        text = self.get_text(
            url, params, ttl_hours=ttl_hours, use_cache=use_cache,
            extra_headers=extra_headers,
        )
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"{self.name}: raspuns non-JSON de la {url}") from exc

    def clear_cache(self) -> int:
        """Sterge fisierele de cache ale acestui client. Intoarce cate a sters."""
        n = 0
        for f in self.cache_dir.glob(f"{self.name}_*"):
            try:
                f.unlink()
                n += 1
            except OSError:
                pass
        return n
