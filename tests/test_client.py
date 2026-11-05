"""Tests for the HTTP layer.

`client.py` predates this file and had no coverage, so these double as the
specification for the retry/auth rules: what gets retried, what does not, and
what must never appear in an error message.
"""

from __future__ import annotations

import httpx
import pytest

from manapool_mcp.auth import Credential
from manapool_mcp.client import AuthError, Fetcher, NotFoundError, RejectedError, UpstreamError

TOKEN = "mpat_supersecrettoken"
EMAIL = "player@example.com"


def build(handler, *, credentials: Credential | None = None) -> tuple[Fetcher, list[httpx.Request]]:
    """A Fetcher wired to a mock transport, plus the requests it made."""
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    fetcher = Fetcher(
        credentials=credentials,
        transport=httpx.MockTransport(record),
        cache_ttl=600,
        min_interval=0,
    )
    return fetcher, seen


async def test_401_with_credentials_raises_auth_error_once() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"status": 401, "message": "User not found"})

    fetcher, seen = build(handler, credentials=Credential(email=EMAIL, token=TOKEN))
    with pytest.raises(AuthError) as caught:
        await fetcher.post_json_keyed("https://manapool.com/api/v1/deck", {"a": 1})

    assert len(seen) == 1, "a rejected key must not be retried"
    message = str(caught.value)
    assert TOKEN not in message and EMAIL not in message
    assert "MANAPOOL_ACCESS_TOKEN" in message


@pytest.mark.parametrize(
    "url",
    [
        "https://api.scryfall.com/cards/collection",
        "https://backend.commanderspellbook.com/find-my-combos",
        "https://example.invalid/anything",
    ],
)
async def test_credentials_never_go_to_another_host(url: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    fetcher, seen = build(handler, credentials=Credential(email=EMAIL, token=TOKEN))
    await fetcher.get_json(url)
    await fetcher.post_json(url, {"a": 1})
    for request in seen:
        assert "X-ManaPool-Access-Token" not in request.headers
        assert "X-ManaPool-Email" not in request.headers

    # And the path that *does* attach credentials must refuse the host outright.
    with pytest.raises(AuthError):
        await fetcher.post_json(url, {"a": 1}, auth=True)
    with pytest.raises(AuthError):
        await fetcher.post_json_keyed(url, {"a": 1})
    assert all("X-ManaPool-Access-Token" not in request.headers for request in seen)


@pytest.mark.parametrize(
    "url",
    [
        "https://manapool.com@evil.example/collect",
        "https://manapool.com.evil.example/collect",
        "https://evilmanapool.com/collect",
        "https://evil.example#.manapool.com",
        "https://evil.example?.manapool.com",
    ],
)
async def test_credential_host_allowlist_resists_url_tricks(url: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("must not reach the network")

    fetcher, seen = build(handler, credentials=Credential(email=EMAIL, token=TOKEN))
    with pytest.raises(AuthError):
        await fetcher.post_json_keyed(url, {"a": 1})
    assert not seen


async def test_keyed_calls_do_carry_credentials_to_manapool() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    fetcher, seen = build(handler, credentials=Credential(email=EMAIL, token=TOKEN))
    await fetcher.post_json_keyed("https://manapool.com/api/v1/deck", {"a": 1})

    assert seen[0].headers["X-ManaPool-Access-Token"] == TOKEN
    assert seen[0].headers["X-ManaPool-Email"] == EMAIL


async def test_keyed_post_refuses_a_foreign_host() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("must not reach the network")

    fetcher, seen = build(handler, credentials=Credential(email=EMAIL, token=TOKEN))
    with pytest.raises(AuthError):
        await fetcher.post_json_keyed("https://api.scryfall.com/cards", {"a": 1})
    assert not seen


async def test_redirect_does_not_forward_credentials() -> None:
    """A 3xx must not carry the account headers to whatever it points at."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "manapool.com":
            return httpx.Response(307, headers={"location": "https://evil.example/collect"})
        return httpx.Response(200, json={"ok": True})

    fetcher, seen = build(handler, credentials=Credential(email=EMAIL, token=TOKEN))
    with pytest.raises(AuthError):
        await fetcher.post_json_keyed("https://manapool.com/api/v1/deck", {"a": 1})

    assert len(seen) == 1, "the redirect must not be followed"
    assert all(request.url.host == "manapool.com" for request in seen)


async def test_keyed_post_is_never_cached() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"valid": True})

    fetcher, seen = build(handler, credentials=Credential(email=EMAIL, token=TOKEN))
    await fetcher.post_json_keyed("https://manapool.com/api/v1/deck", {"a": 1})
    await fetcher.post_json_keyed("https://manapool.com/api/v1/deck", {"a": 1})
    assert len(seen) == 2

    # The anonymous path still caches, which is what keeps scans affordable.
    await fetcher.get_json("https://manapool.com/api/v1/products/singles")
    await fetcher.get_json("https://manapool.com/api/v1/products/singles")
    assert len(seen) == 3


async def test_keyed_call_without_credentials_fails_before_the_network() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("should not reach the network")

    fetcher, seen = build(handler)
    with pytest.raises(AuthError) as caught:
        await fetcher.post_json_keyed("https://manapool.com/api/v1/deck", {"a": 1})
    assert not seen
    assert "MANAPOOL_EMAIL" in str(caught.value)


async def test_anonymous_403_is_upstream_not_auth_and_is_retried() -> None:
    """Cloudflare bot rules answer HTML paths with 403; that is not a key problem."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="blocked")

    fetcher, seen = build(handler)
    with pytest.raises(UpstreamError) as caught:
        await fetcher.get_text("https://manapool.com/decks")
    assert not isinstance(caught.value, AuthError)
    assert len(seen) == 3, "an anonymous 403 stays retryable"


async def test_keyed_403_is_an_auth_error_and_is_not_retried() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="blocked")

    fetcher, seen = build(handler, credentials=Credential(email=EMAIL, token=TOKEN))
    with pytest.raises(AuthError):
        await fetcher.post_json_keyed("https://manapool.com/api/v1/deck", {"a": 1})
    assert len(seen) == 1


async def test_400_is_not_retried() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"status": 400, "message": "Invalid request data"})

    fetcher, seen = build(handler)
    with pytest.raises(RejectedError) as caught:
        await fetcher.post_json("https://manapool.com/api/v1/deck", {"a": 1})
    assert len(seen) == 1
    assert "Invalid request data" in str(caught.value)


async def test_429_is_still_retried() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, text="ok")

    fetcher, seen = build(handler)
    assert await fetcher.get_text("https://manapool.com/decks") == "ok"
    assert len(seen) == 2


async def test_500_is_retried_three_times() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    fetcher, seen = build(handler)
    with pytest.raises(UpstreamError):
        await fetcher.get_text("https://manapool.com/decks")
    assert len(seen) == 3


async def test_404_is_not_retried_and_is_recognisable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    fetcher, seen = build(handler)
    with pytest.raises(NotFoundError):
        await fetcher.get_text("https://manapool.com/lists/abc")
    assert len(seen) == 1


async def test_success_is_cached_for_the_anonymous_path() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>deck</html>")

    fetcher, seen = build(handler)
    assert await fetcher.get_text("https://manapool.com/lists/abc") == "<html>deck</html>"
    assert await fetcher.get_text("https://manapool.com/lists/abc") == "<html>deck</html>"
    assert len(seen) == 1


async def test_missing_credentials_property_is_none() -> None:
    fetcher, _ = build(lambda request: httpx.Response(200))
    assert fetcher.credentials is None
    assert fetcher._auth_headers == {}


def test_credential_repr_does_not_leak_the_token() -> None:
    assert TOKEN not in repr(Credential(email=EMAIL, token=TOKEN))
