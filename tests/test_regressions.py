from __future__ import annotations

import uuid
from urllib.parse import parse_qs, urlparse

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from manapool_mcp import server
from manapool_mcp.parse import DeckParseError, parse_deck_page, parse_deck_tiles
from manapool_mcp.rules import MASS_LAND_DENIAL
from manapool_mcp.spellbook import _combo_cards


def _tile(index: int, price: float) -> str:
    deck_id = uuid.uuid5(uuid.NAMESPACE_URL, f"reg-{index}")
    return (
        f'<a href="/lists/{deck_id}"><h3>Deck {index}</h3>'
        '<p class="text-blue-800">Theme</p>'
        '<p class="text-gray-600">from Seller</p>'
        f"<span>Bracket 3</span><span>${price:,.2f}</span></a>"
    )


class PagesFetcher:
    """Serves explicit pages of prices, so intra-page order can be controlled."""

    def __init__(self, pages: list[list[float]]):
        self.pages = pages
        self.calls: list[str] = []

    async def get_many(self, urls: list[str], concurrency: int = 4) -> list[str]:
        return [await self.get_text(url) for url in urls]

    async def get_text(self, url: str) -> str:
        self.calls.append(url)
        page = int(parse_qs(urlparse(url).query).get("page", ["0"])[0])
        chunk = self.pages[page] if page < len(self.pages) else []
        tiles = "".join(_tile(page * 24 + i, price) for i, price in enumerate(chunk))
        total = 24 * len(self.pages)
        return f"<html><body><p>{total:,} decks</p>{tiles}</body></html>"


@pytest.mark.asyncio
async def test_page_boundaries_survive_unordered_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    # Ties arrive in arbitrary order, so a page's first/last row is not its
    # minimum/maximum. Both boundary pages here straddle the window.
    fetcher = PagesFetcher([[10.0, 12.0, 11.0], [13.0, 15.0, 14.0], [16.0, 18.0, 17.0]])
    monkeypatch.setattr(server, "_fetcher", fetcher)
    result = await server.search_decks(min_price=11.5, max_price=14.5)
    assert sorted(d.price for d in result.decks) == [12.0, 13.0, 14.0]


@pytest.mark.asyncio
async def test_min_price_alone_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    pages = [[float(p) for p in range(i * 24, i * 24 + 24)] for i in range(40)]
    fetcher = PagesFetcher(pages)
    monkeypatch.setattr(server, "_fetcher", fetcher)
    result = await server.search_decks(min_price=100)
    assert result.truncated is True
    # Budget covers sampled pages plus the bisection probes that picked a start.
    assert result.scanned_pages <= server.MAX_SCAN_PAGES + 20
    assert len(fetcher.calls) <= server.MAX_SCAN_PAGES + 20


@pytest.mark.asyncio
async def test_limit_above_one_page(monkeypatch: pytest.MonkeyPatch) -> None:
    pages = [[float(p) for p in range(i * 24, i * 24 + 24)] for i in range(10)]
    monkeypatch.setattr(server, "_fetcher", PagesFetcher(pages))
    result = await server.search_decks(limit=96)
    assert len(result.decks) == 96


def test_tile_price_with_thousands_separator() -> None:
    html = (
        f'<a href="/lists/{uuid.uuid4()}"><h3>Bling</h3>'
        '<p class="text-blue-800">Theme</p>'
        '<p class="text-gray-600">from Seller</p><span>Bracket 3</span>'
        "<span>$8,760.72</span></a>"
    )
    tiles = parse_deck_tiles(html)
    assert tiles[0].price == pytest.approx(8760.72)


def test_deck_page_price_with_thousands_separator() -> None:
    deck_id = str(uuid.uuid4())
    html = (
        "<html><head><title>Bling — Theme - Deck by Mana Pool Deckbuilder</title></head><body>"
        "built from Seller's inventory Price at Seller $1,234.56 Include 0 basic lands"
        "<h3>1 cards</h3><h3>Commander</h3>"
        "<div><span>1x</span><button>Bling</button><span>$2.00</span></div>"
        "</body></html>"
    )
    deck = parse_deck_page(html, deck_id)
    assert deck.price_cards == pytest.approx(1234.56)
    assert deck.total_cards == 1


def test_incomplete_deck_page_raises() -> None:
    # Header claims 100 cards but the list only yields two: markup drift must
    # fail loudly rather than produce a confident audit of a partial deck.
    html = (
        "<html><head><title>Broken — Theme - Deck by Mana Pool Deckbuilder</title></head><body>"
        "<h3>100 cards</h3><h3>Commander</h3>"
        "<div><span>1x</span><button>Broken</button><span>$1.00</span></div>"
        "<h3>Lands</h3><div><span>10x</span><button>Swamp</button><span>$0.10</span></div>"
        "</body></html>"
    )
    with pytest.raises(DeckParseError):
        parse_deck_page(html, str(uuid.uuid4()))


def test_include_types_maps_normalised_names() -> None:
    assert server._norm("Sol Ring") == "sol ring"
    # The cache is keyed by normalised name; lookup must use the same key.
    server._types_cache["sol ring"] = "Artifact"
    assert server._types_cache.get(server._norm("Sol Ring")) == "Artifact"


@pytest.mark.asyncio
async def test_get_deck_surfaces_parse_error_as_tool_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BrokenFetcher:
        async def get_text(self, url: str) -> str:
            return (
                "<html><head><title>Broken - Deck by Mana Pool Deckbuilder</title></head><body>"
                "<h3>100 cards</h3><h3>Commander</h3>"
                "<div><span>1x</span><button>Broken</button><span>$1.00</span></div>"
                "</body></html>"
            )

    monkeypatch.setattr(server, "_fetcher", BrokenFetcher())
    with pytest.raises(ToolError):
        await server.get_deck(str(uuid.uuid4()))


def test_combo_cards_reads_uses_and_requires() -> None:
    item = {
        "uses": [{"card": {"name": "Selvala, Heart of the Wilds"}}],
        "requires": [{"card": {"name": "Lilysplash Mentor"}}],
        "bracketTag": "R",
    }
    assert _combo_cards(item) == ["Selvala, Heart of the Wilds", "Lilysplash Mentor"]


def test_mass_land_denial_excludes_single_land_removal() -> None:
    for card in ("Strip Mine", "Wasteland", "Ghost Quarter", "Field of Ruin"):
        assert card not in MASS_LAND_DENIAL


def test_mass_land_denial_includes_mana_denial() -> None:
    for card in ("Winter Orb", "Blood Moon", "Back to Basics", "Armageddon"):
        assert card in MASS_LAND_DENIAL


def test_bad_colors_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server, "_fetcher", PagesFetcher([[]]))

    async def run() -> None:
        with pytest.raises(ToolError):
            await server.search_decks(colors="XYZ")

    import asyncio

    asyncio.run(run())


def test_card_row_price_with_thousands_separator() -> None:
    deck_id = str(uuid.uuid4())
    html = (
        "<html><head><title>Bling — Theme - Deck by Mana Pool Deckbuilder</title></head><body>"
        "built from Seller's inventory Include 0 basic lands"
        "<h3>1 cards</h3><h3>Commander</h3>"
        "<div><span>1x</span><button>Bling</button><span>$1,234.56</span></div>"
        "</body></html>"
    )
    deck = parse_deck_page(html, deck_id)
    assert deck.cards[0].price == pytest.approx(1234.56)


def test_only_commander_section_rows_become_commanders() -> None:
    deck_id = str(uuid.uuid4())
    html = (
        "<html><head><title>Partners - Deck by Mana Pool Deckbuilder</title></head><body>"
        "<h3>3 cards</h3>"
        "<div><h3>Commander</h3><div>"
        "<div><span>1x</span><button>Thrasios, Triton Hero</button><span>$1.00</span></div>"
        "<div><span>1x</span><button>Kraum, Ludevic's Opus</button><span>$1.00</span></div>"
        "</div></div>"
        "<div><h3>Lands</h3><div>"
        "<div><span>1x</span><button>Island</button><span>$0.10</span></div>"
        "</div></div>"
        "</body></html>"
    )
    deck = parse_deck_page(html, deck_id)
    assert deck.commanders == ["Thrasios, Triton Hero", "Kraum, Ludevic's Opus"]


@pytest.mark.asyncio
async def test_small_result_set_is_exhaustive(monkeypatch: pytest.MonkeyPatch) -> None:
    # 8 pages is inside the budget, so every page is read and nothing is sampled.
    pages = [[float(p) for p in range(i * 24, i * 24 + 24)] for i in range(8)]
    fetcher = PagesFetcher(pages)
    monkeypatch.setattr(server, "_fetcher", fetcher)
    result = await server.search_decks(min_price=100, max_price=120)
    assert result.truncated is False
    assert sorted(d.price for d in result.decks) == [float(p) for p in range(100, 121)]
    assert result.scanned_pages == 8


@pytest.mark.asyncio
async def test_wide_result_set_is_always_flagged(monkeypatch: pytest.MonkeyPatch) -> None:
    pages = [[float(p) for p in range(i * 24, i * 24 + 24)] for i in range(60)]
    monkeypatch.setattr(server, "_fetcher", PagesFetcher(pages))
    result = await server.search_decks(min_price=100, max_price=120)
    assert result.truncated is True
    assert result.note is not None and "sample" in result.note


@pytest.mark.asyncio
async def test_failed_start_hint_never_claims_no_listings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Ascending catalogue with an out-of-order cheap tail: bisection can miss.
    pages = [[float(20 + i * 66)] * 24 for i in range(57)]
    pages += [[25.0 + i] * 24 for i in range(3)]
    fetcher = PagesFetcher(pages)
    monkeypatch.setattr(server, "_fetcher", fetcher)
    result = await server.search_decks(min_price=3900)
    assert result.truncated is True
    assert "No listings at or above" not in (result.note or "")


@pytest.mark.asyncio
async def test_sample_reaches_the_tail_page(monkeypatch: pytest.MonkeyPatch) -> None:
    pages = [[float(p) for p in range(i * 24, i * 24 + 24)] for i in range(80)]
    fetcher = PagesFetcher(pages)
    monkeypatch.setattr(server, "_fetcher", fetcher)
    await server.search_decks(min_price=0, max_price=5000)
    assert any("page=79" in call for call in fetcher.calls)


@pytest.mark.asyncio
async def test_empty_page_in_exhaustive_scan_is_flagged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pages = [[float(i)] * 24 for i in range(6)]
    pages[3] = []
    monkeypatch.setattr(server, "_fetcher", PagesFetcher(pages))
    result = await server.search_decks(min_price=0, max_price=99)
    assert result.truncated is True
    assert result.note is not None and "returned no decks" in result.note


def test_commander_fallback_skips_whitespace_siblings() -> None:
    deck_id = str(uuid.uuid4())
    html = (
        "<html><head><title>Odd - Deck by Mana Pool Deckbuilder</title></head><body>"
        "<h3>2 cards</h3><h3>Commander</h3>\n"
        "<div><div><span>1x</span><button>Real Commander</button><span>$1.00</span></div></div>"
        "<h3>Lands</h3>\n"
        "<div><div><span>1x</span><button>Forest</button><span>$0.10</span></div></div>"
        "</body></html>"
    )
    deck = parse_deck_page(html, deck_id)
    assert deck.commanders == ["Real Commander"]
    assert sum(card.qty for card in deck.cards) == 2
