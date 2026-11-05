from __future__ import annotations

import uuid
from urllib.parse import parse_qs, urlparse

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from manapool_mcp import server
from manapool_mcp.rules import mechanical_bracket


def _tile(index: int, price: float) -> str:
    deck_id = uuid.uuid5(uuid.NAMESPACE_URL, f"deck-{index}")
    return (
        f'<a href="/lists/{deck_id}">'
        f"<h3>Deck {index}</h3>"
        f'<p class="text-blue-800">Aristocrats</p>'
        f'<p class="text-gray-600">from Seller {index}</p>'
        f"<span>Bracket 3</span>"
        f"<span>${price:.2f}</span>"
        f"</a>"
    )


class FakeFetcher:
    """Serves synthetic ManaPool listing pages, 24 decks per page."""

    def __init__(self, prices: list[float], commander_prices: list[float] | None = None):
        self.prices = prices
        self.commander_prices = commander_prices
        self.calls: list[str] = []

    async def get_many(self, urls: list[str], concurrency: int = 4) -> list[str]:
        return [await self.get_text(url) for url in urls]

    async def get_text(self, url: str) -> str:
        self.calls.append(url)
        query = parse_qs(urlparse(url).query)
        page = int(query.get("page", ["0"])[0])
        source = self.commander_prices if query.get("q") else self.prices
        source = source or []
        chunk = source[page * 24 : (page + 1) * 24]
        tiles = "".join(_tile(page * 24 + offset, price) for offset, price in enumerate(chunk))
        return f"<html><body><p>{len(source):,} decks</p>{tiles}</body></html>"


@pytest.mark.asyncio
async def test_price_window_returns_only_matches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server, "_fetcher", FakeFetcher([float(p) for p in range(240)]))
    result = await server.search_decks(min_price=60, max_price=80)
    assert sorted(deck.price for deck in result.decks) == [float(p) for p in range(60, 81)]
    assert result.truncated is False


@pytest.mark.asyncio
async def test_price_window_samples_when_too_wide(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server, "_fetcher", FakeFetcher([float(p) for p in range(1200)]))
    monkeypatch.setattr(server, "MAX_SCAN_PAGES", 5)
    result = await server.search_decks(min_price=0, max_price=2000)
    assert result.truncated is True
    assert result.scanned_pages >= 5
    assert result.note is not None and "sampled" in result.note
    assert result.decks


@pytest.mark.asyncio
async def test_price_window_above_range_is_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server, "_fetcher", FakeFetcher([float(p) for p in range(24)]))
    result = await server.search_decks(min_price=500)
    assert result.decks == []
    assert result.note is not None and "No listings" in result.note


@pytest.mark.asyncio
async def test_forces_price_sort_for_window(monkeypatch: pytest.MonkeyPatch) -> None:
    fetcher = FakeFetcher([float(p) for p in range(48)])
    monkeypatch.setattr(server, "_fetcher", fetcher)
    await server.search_decks(min_price=10, max_price=20)
    assert all("sort=price-asc" in call for call in fetcher.calls)


@pytest.mark.asyncio
async def test_commander_window_filters_locally(monkeypatch: pytest.MonkeyPatch) -> None:
    # Small result sets are scanned in full, so intra-page disorder is handled.
    prices = [100.0, 70.0, 50.0, 75.0, 60.0]
    monkeypatch.setattr(server, "_fetcher", FakeFetcher([], commander_prices=prices))
    result = await server.search_decks(commander="Rendmaw", min_price=60, max_price=80)
    assert sorted(deck.price for deck in result.decks) == [60.0, 70.0, 75.0]
    assert result.scanned_pages == 1


@pytest.mark.asyncio
async def test_page_without_price_window_uses_site_pagination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetcher = FakeFetcher([float(p) for p in range(240)])
    monkeypatch.setattr(server, "_fetcher", fetcher)
    result = await server.search_decks(page=2)
    assert result.page == 2
    assert len(result.decks) == 24
    assert "page=2" in fetcher.calls[0]


@pytest.mark.asyncio
async def test_rejects_bad_sort(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server, "_fetcher", FakeFetcher([]))
    with pytest.raises(ToolError):
        await server.search_decks(sort="cheapest")


@pytest.mark.asyncio
async def test_rejects_inverted_price_range(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server, "_fetcher", FakeFetcher([]))
    with pytest.raises(ToolError):
        await server.search_decks(min_price=90, max_price=60)


@pytest.mark.parametrize(
    ("gcs", "mld", "combos", "expected"),
    [
        (0, False, 0, 2),
        (1, False, 0, 3),
        (3, False, 0, 3),
        (4, False, 0, 4),
        (0, True, 0, 4),
        (0, False, 1, 3),
        (2, True, 1, 4),
    ],
)
def test_mechanical_bracket(gcs: int, mld: bool, combos: int, expected: int) -> None:
    minimum, reasons = mechanical_bracket(gcs, mld, combos)
    assert minimum == expected
    assert reasons
