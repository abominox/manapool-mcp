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

from .auth import CONFIG_HINT, Credential

MANAPOOL_BASE = "https://manapool.com"
SPELLBOOK_BASE = "https://backend.commanderspellbook.com"


class UpstreamError(RuntimeError):
    """A remote site failed or returned something unusable."""


class NotFoundError(UpstreamError):
    """The requested deck or page does not exist (any more)."""


class AuthError(UpstreamError):
    """ManaPool rejected the configured credentials.

    Deliberately carries neither the e-mail nor the token: this message ends up
    in a model's context and in logs.
    """


# 4xx from a REST API is deterministic: the request will fail the same way on a
# retry, so spending three attempts on it only delays the error.
class RejectedError(UpstreamError):
    """A 4xx the upstream will answer identically next time. Never retried."""


_FATAL_CLIENT_STATUS = frozenset({400, 401, 405, 409, 410, 415, 422, 451})


def _retry_after(response: httpx.Response, default: float = 1.0) -> float:
    """Seconds to wait from a Retry-After header, capped for tool-call sanity."""
    raw = response.headers.get("Retry-After")
    try:
        delay = float(raw) if raw is not None else default
    except ValueError:
        delay = default
    return max(0.5, min(delay, 10.0))


def _manapool_host(url: str) -> bool:
    """True when `url` is ManaPool's own API host.

    Parsed with httpx rather than by string matching, so userinfo tricks like
    `https://manapool.com@evil.example/` resolve to the real host.
    """
    host = (httpx.URL(url).host or "").lower()
    return host == "manapool.com" or host.endswith(".manapool.com")


def _auth_message(status: int, *, authenticated: bool) -> str:
    """A credential failure, without the URL or any credential value."""
    if authenticated:
        return (
            f"ManaPool rejected the configured credentials (HTTP {status}). "
            f"Check that the token is current and that the e-mail matches the "
            f"account that owns it. {CONFIG_HINT}"
        )
    # Anonymous callers get here from Cloudflare bot rules on HTML pages too,
    # so do not assume the endpoint wants a key.
    return (
        f"ManaPool returned HTTP {status} for this anonymous request. It may be "
        f"rate limiting or blocking the client; retry later."
    )


def _client_message(url: str, response: httpx.Response) -> str:
    """Detail from a 4xx body when it is the API's usual {status,message} envelope."""
    detail = ""
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        message = payload.get("message")
        if isinstance(message, str) and message.strip():
            detail = f": {message.strip()}"
        details = payload.get("details")
        if isinstance(details, list) and details:
            detail += " (" + "; ".join(str(item) for item in details[:5]) + ")"
    return f"{url} returned HTTP {response.status_code}{detail}"


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
        credentials: Credential | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
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
        # Auth headers are per-request, never client defaults: this client also
        # talks to Scryfall and Commander Spellbook, which must not see them.
        self.credentials = credentials
        self._auth_headers = credentials.headers() if credentials else {}
        self._client = httpx.AsyncClient(
            timeout=self.timeout,
            follow_redirects=True,
            transport=transport,
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

    def set_credentials(self, credentials: Credential | None) -> None:
        """Install or clear credentials on the live client.

        The server builds one Fetcher per process, but credentials can appear or
        be revoked between calls, so the fetcher must not treat them as a
        construction-time constant.
        """
        self.credentials = credentials
        self._auth_headers = credentials.headers() if credentials else {}

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

    async def post_json(self, url: str, payload: dict[str, Any], *, auth: bool = False) -> Any:
        raw = await self._request("POST", url, json=payload, auth=auth)
        import json

        try:
            return json.loads(raw)
        except ValueError as exc:  # pragma: no cover - defensive
            raise UpstreamError(f"{url} returned invalid JSON") from exc

    async def post_json_keyed(self, url: str, payload: dict[str, Any]) -> Any:
        """POST with ManaPool credentials. Fails loudly when none are configured."""
        if not self.credentials:
            raise AuthError(f"ManaPool API credentials are not configured. {CONFIG_HINT}")
        return await self.post_json(url, payload, auth=True)

    async def _request(
        self,
        method: str,
        url: str,
        *,
        use_cache: bool = True,
        auth: bool = False,
        **kwargs: Any,
    ) -> str:
        headers = kwargs.pop("headers", None)
        if auth:
            # The allowlist is enforced here, not only in post_json_keyed, so
            # that no caller can attach credentials to a foreign host.
            if not _manapool_host(url):
                raise AuthError(
                    f"Refusing to send ManaPool credentials to "
                    f"{httpx.URL(url).host or 'an unknown host'}."
                )
            # Keyed responses are account-scoped, so they are never memoised.
            use_cache = False
            headers = {**(headers or {}), **self._auth_headers}
        cache_key = f"{method} {url} {kwargs.get('json')!r} auth={auth}"
        now = time.monotonic()
        cached = self._cache.get(cache_key)
        if use_cache and cached is not None and now - cached[0] < self.cache_ttl:
            return cached[1]

        await self._throttle()
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                if auth:
                    # Credentials must not ride a redirect to another host, and
                    # httpx only strips Authorization on a cross-origin hop.
                    response = await self._client.request(
                        method, url, headers=headers, follow_redirects=False, **kwargs
                    )
                    if response.is_redirect:
                        location = response.headers.get("location", "?")
                        raise AuthError(
                            f"ManaPool redirected an authenticated request to {location!r}; "
                            "refusing to forward credentials."
                        )
                else:
                    response = await self._client.request(method, url, headers=headers, **kwargs)
                if response.status_code in (401, 403):
                    if auth:
                        raise AuthError(_auth_message(response.status_code, authenticated=True))
                    if response.status_code == 401:
                        raise RejectedError(_auth_message(401, authenticated=False))
                    # Anonymous 403 is usually Cloudflare bot mitigation on an
                    # HTML path and is worth a retry, so it falls through.
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
                if response.status_code in _FATAL_CLIENT_STATUS:
                    raise RejectedError(_client_message(url, response))
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
            except AuthError:
                raise
            except RejectedError:
                raise
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
