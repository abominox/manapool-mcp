"""HTTP access to ManaPool and Commander Spellbook.

ManaPool has no public API, so this fetch the same HTML a browser receives.
Requests are throttled and cached in memory to stay polite.
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any
from urllib.parse import urlencode

import httpx

MANAPOOL_BASE = "https://manapool.com"
SPELLBOOK_BASE = "https://backend.commanderspellbook.com"


class UpstreamError(RuntimeError):
    """A remote site failed or returned something unusable."""


class NotFoundError(UpstreamError):
    """The requested deck or page does not exist (any more)."""


def _retry_after(response: httpx.Response, default: float = 1.0) -> float:
    """Seconds to wait from a Retry-After header, capped for tool-call sanity."""
    raw = response.headers.get("Retry-After")
    try:
        delay = float(raw) if raw is not None else default
    except ValueError:
        delay = default
    return max(0.5, min(delay, 10.0))


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


class Fetcher:
    def __init__(
        self,
        *,
        timeout: float | None = None,
        cache_ttl: float | None = None,
        min_interval: float | None = None,
        user_agent: str | None = None,
    ) -> None:
        self.timeout = timeout if timeout is not None else _env_float("MANAPOOL_TIMEOUT", 20.0)
        self.cache_ttl = (
            cache_ttl if cache_ttl is not None else _env_float("MANAPOOL_CACHE_TTL", 600.0)
        )
        self.min_interval = (
            min_interval if min_interval is not None else _env_float("MANAPOOL_MIN_INTERVAL", 0.2)
        )
        self.user_agent = user_agent or os.environ.get(
            "MANAPOOL_USER_AGENT", "manapool-mcp/0.1 (personal deck research)"
        )
        self._client = httpx.AsyncClient(
            timeout=self.timeout,
            follow_redirects=True,
            headers={
                "User-Agent": self.user_agent,
                "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            },
        )
        self.cache_bytes = _env_int("MANAPOOL_CACHE_BYTES", 32_000_000)
        self._cache: dict[str, tuple[float, str]] = {}
        self._cache_size = 0
        self._lock = asyncio.Lock()
        self._last_request = 0.0

    async def aclose(self) -> None:
        await self._client.aclose()

    async def get_text(self, url: str, *, use_cache: bool = True) -> str:
        return await self._request("GET", url, use_cache=use_cache)

    async def get_many(self, urls: list[str], concurrency: int = 4) -> list[str]:
        """Fetch several pages concurrently.

        The throttle still spaces request *starts* by min_interval, but
        responses overlap, which matters when a scan touches dozens of pages.
        """
        semaphore = asyncio.Semaphore(max(1, concurrency))

        async def one(url: str) -> str:
            async with semaphore:
                return await self.get_text(url)

        return list(await asyncio.gather(*(one(url) for url in urls)))

    async def get_json(self, url: str) -> Any:
        import json

        raw = await self._request("GET", url)
        try:
            return json.loads(raw)
        except ValueError as exc:  # pragma: no cover - defensive
            raise UpstreamError(f"{url} returned invalid JSON") from exc

    async def post_json(self, url: str, payload: dict[str, Any]) -> Any:
        raw = await self._request("POST", url, json=payload)
        import json

        try:
            return json.loads(raw)
        except ValueError as exc:  # pragma: no cover - defensive
            raise UpstreamError(f"{url} returned invalid JSON") from exc

    async def _request(
        self, method: str, url: str, *, use_cache: bool = True, **kwargs: Any
    ) -> str:
        cache_key = f"{method} {url} {kwargs.get('json')!r}"
        now = time.monotonic()
        cached = self._cache.get(cache_key)
        if use_cache and cached is not None and now - cached[0] < self.cache_ttl:
            return cached[1]

        await self._throttle()
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = await self._client.request(method, url, **kwargs)
                if response.status_code == 404:
                    raise NotFoundError(
                        f"{url} returned 404. The deck may have been rebuilt or removed."
                    )
                if response.status_code == 429:
                    delay = _retry_after(response)
                    last_error = UpstreamError(f"{url} is rate limited (HTTP 429)")
                    if attempt < 2:
                        await asyncio.sleep(delay)
                        continue
                    raise last_error
                if response.status_code >= 500:
                    raise UpstreamError(f"{url} returned HTTP {response.status_code}")
                response.raise_for_status()
                text = response.text
                if not use_cache:
                    return text
                previous = self._cache.get(cache_key)
                if previous is not None:
                    self._cache_size -= len(previous[1])
                self._cache[cache_key] = (time.monotonic(), text)
                self._cache_size += len(text)
                if self._cache_size > self.cache_bytes or len(self._cache) > 512:
                    self._evict()
                return text
            except NotFoundError:
                raise
            except (httpx.HTTPError, UpstreamError) as exc:
                last_error = exc
                if attempt < 2:
                    await asyncio.sleep(0.4 * (2**attempt))
        raise UpstreamError(f"Could not fetch {url}: {last_error}")

    async def _throttle(self) -> None:
        async with self._lock:
            gap = time.monotonic() - self._last_request
            if gap < self.min_interval:
                await asyncio.sleep(self.min_interval - gap)
            self._last_request = time.monotonic()

    def _evict(self) -> None:
        cutoff = time.monotonic() - self.cache_ttl
        for key in [k for k, (ts, _) in self._cache.items() if ts < cutoff]:
            _, value = self._cache.pop(key)
            self._cache_size -= len(value)
        if self._cache_size <= self.cache_bytes and len(self._cache) <= 512:
            return
        for key, (_, value) in sorted(self._cache.items(), key=lambda kv: kv[1][0]):
            if self._cache_size <= self.cache_bytes and len(self._cache) <= 512:
                break
            self._cache.pop(key, None)
            self._cache_size -= len(value)


def decks_url(
    *,
    page: int = 0,
    bracket: int | None = None,
    sort: str | None = None,
    query: str | None = None,
    theme: str | None = None,
    colors: str | None = None,
) -> str:
    params: dict[str, str] = {}
    if page:
        params["page"] = str(page)
    if bracket is not None:
        params["bracket"] = str(bracket)
    if sort:
        params["sort"] = sort
    if query:
        params["q"] = query
    if theme:
        params["theme"] = theme
    if colors:
        params["colors"] = colors
    suffix = f"?{urlencode(params)}" if params else ""
    return f"{MANAPOOL_BASE}/decks{suffix}"


def deck_url(deck_id: str) -> str:
    return f"{MANAPOOL_BASE}/lists/{deck_id}"
