from __future__ import annotations

import pytest

from manapool_mcp import server
from manapool_mcp.prices import PriceBook
from manapool_mcp.products import cents_to_usd, products_by_scryfall_ids


class FakeFetcher:
    def __init__(self, *, products=None, scryfall_search=None, collection=None):
        self.products = products or []
        self.scryfall_search = scryfall_search or []
        self.collection = collection or {}
        self.urls: list[str] = []

    async def get_json(self, url: str):
        self.urls.append(url)
        if "/products/singles" in url:
            return {"data": self.products}
        if "/cards/search" in url:
            return {"data": self.scryfall_search, "has_more": False}
        return {}

    async def post_json(self, url: str, payload):
        self.urls.append(url)
        names = [item["name"] for item in payload["identifiers"]]
        found = [self.collection[n] for n in names if n in self.collection]
        missing = [{"name": n} for n in names if n not in self.collection]
        return {"data": found, "not_found": missing}


def test_cents_to_usd() -> None:
    assert cents_to_usd(1234) == 12.34
    assert cents_to_usd(0) is None
    assert cents_to_usd(None) is None
    assert cents_to_usd("nonsense") is None


@pytest.mark.asyncio
async def test_products_use_repeated_query_params() -> None:
    fetcher = FakeFetcher(products=[{"name": "A"}])
    await products_by_scryfall_ids(fetcher, ["id-1", "id-2"])  # type: ignore[arg-type]
    assert "scryfall_ids=id-1&scryfall_ids=id-2" in fetcher.urls[0]


@pytest.mark.asyncio
async def test_products_chunk_at_100() -> None:
    fetcher = FakeFetcher(products=[])
    await products_by_scryfall_ids(fetcher, [f"id-{i}" for i in range(250)])  # type: ignore[arg-type]
    assert len(fetcher.urls) == 3


def _book() -> PriceBook:
    rows = [
        {
            "name": "Sol Ring",
            "set_code": "SOC",
            "number": "128",
            "available_quantity": 10,
            "price_cents": 122,
            "price_cents_nm": 125,
            "price_market": 128,
            "url": "https://manapool.com/card/soc/128/sol-ring",
        },
        {
            "name": "Sol Ring",
            "set_code": "LTC",
            "number": "300",
            "available_quantity": 2,
            "price_cents": 350,
            "price_cents_nm": 400,
            "price_market": 360,
            "url": "https://manapool.com/card/ltc/300/sol-ring",
        },
        {
            "name": "Out of Stock Card",
            "set_code": "XXX",
            "number": "1",
            "available_quantity": 0,
            "price_cents": 100,
            "price_cents_nm": None,
            "price_market": 100,
            "url": "",
        },
    ]
    return PriceBook(rows, "2026-11-05T00:00:00Z")


def test_price_book_picks_cheapest_printing() -> None:
    book = _book()
    cheapest = book.cheapest("Sol Ring")
    assert cheapest is not None and cheapest.set_code == "SOC"
    assert cheapest.unit_price == 1.22


def test_price_book_ignores_out_of_stock() -> None:
    assert _book().cheapest("Out of Stock Card") is None


@pytest.mark.asyncio
async def test_price_deck_totals_missing_and_short(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_book(_fetcher):
        return _book()

    monkeypatch.setattr(server, "load_price_book", fake_book)
    monkeypatch.setattr(server, "_fetcher", FakeFetcher())

    result = await server.price_deck(
        "1 Sol Ring\n2 Unknown Card\n50 Sol Ring", commander="Sol Ring"
    )
    assert result.total_cards == 53
    assert result.missing == ["Unknown Card"]
    assert result.total_price == pytest.approx(1.22 * 51)
    assert result.commander_price == pytest.approx(1.22)
    assert "Sol Ring" in result.short  # only 10 in stock, 50 requested


@pytest.mark.asyncio
async def test_search_cards_merges_manapool_prices(monkeypatch: pytest.MonkeyPatch) -> None:
    card = {
        "id": "scry-1",
        "name": "Sol Ring",
        "set": "soc",
        "set_name": "March of the Machine Commander",
        "type_line": "Artifact",
        "cmc": 1.0,
    }
    fetcher = FakeFetcher(
        scryfall_search=[card],
        products=[
            {
                "scryfall_id": "scry-1",
                "scryfall_ids": ["scry-1"],
                "name": "Sol Ring",
                "set_code": "SOC",
                "price_cents": 122,
                "price_cents_nm": 125,
                "available_quantity": 10,
                "url": "https://manapool.com/card/soc/128/sol-ring",
            }
        ],
    )
    monkeypatch.setattr(server, "_fetcher", fetcher)

    result = await server.search_cards('!"Sol Ring"')
    assert len(result.cards) == 1
    assert result.cards[0].price_usd == 1.22
    assert result.cards[0].stock == 10


@pytest.mark.asyncio
async def test_get_card_from_manapool_url(monkeypatch: pytest.MonkeyPatch) -> None:
    card = {"id": "scry-1", "name": "Sol Ring", "set": "sld", "type_line": "Artifact"}
    fetcher = FakeFetcher(
        products=[
            {
                "scryfall_id": "scry-1",
                "name": "Sol Ring",
                "set_code": "SLD",
                "price_cents": 2300,
                "available_quantity": 3,
                "url": "https://manapool.com/card/sld/2683/sol-ring",
                "variants": [
                    {
                        "condition_id": "NM",
                        "finish_id": "FO",
                        "language_id": "EN",
                        "low_price": 2499,
                        "available_quantity": 1,
                    },
                    {
                        "condition_id": "LP",
                        "finish_id": "NF",
                        "language_id": "EN",
                        "low_price": 2300,
                        "available_quantity": 2,
                    },
                ],
            }
        ],
        collection={"Sol Ring": card},
    )

    async def fake_by_set_number(_fetcher, set_code, number):
        return card

    monkeypatch.setattr(server, "card_by_set_number", fake_by_set_number)
    monkeypatch.setattr(server, "_fetcher", fetcher)

    result = await server.get_card("https://manapool.com/card/sld/2683/sol-ring")
    assert result.name == "Sol Ring"
    assert result.market_usd is None
    assert [listing.price_usd for listing in result.listings] == [23.0, 24.99]
    assert result.listings[0].condition == "LP"
