from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from manapool_mcp.parse import (
    deck_id_from,
    parse_deck_page,
    parse_deck_tiles,
    parse_total,
)

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def decks_html() -> str:
    return (FIXTURES / "decks.html").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def list_html() -> str:
    return (FIXTURES / "list.html").read_text(encoding="utf-8")


def test_parse_total(decks_html: str) -> None:
    assert parse_total(decks_html) == 269_203


def test_parse_tiles(decks_html: str) -> None:
    tiles = parse_deck_tiles(decks_html)
    assert len(tiles) == 24
    first = tiles[0]
    assert first.name == "Grismold, the Dreadsower"
    assert first.theme == "Aristocrats"
    assert first.seller == "GhoulsMagnetsAndMore"
    assert first.bracket == 3
    assert first.price == 103.55
    assert first.id == "d127d4d1-3b66-467d-bad3-d91be325e203"
    assert first.url.startswith("https://manapool.com/lists/")


def test_tiles_have_unique_ids(decks_html: str) -> None:
    tiles = parse_deck_tiles(decks_html)
    assert len({t.id for t in tiles}) == len(tiles)


def test_parse_deck_page(list_html: str) -> None:
    deck = parse_deck_page(list_html, "db746ea2-c1cd-4865-ae57-e40660530f24")
    assert deck.name.startswith("Nelly Borca, Impulsive Accuser")
    assert deck.commander == "Nelly Borca, Impulsive Accuser"
    assert deck.seller == "Star 3 Collectibles"
    assert deck.price_cards == 57.0
    assert deck.basic_lands == 20
    assert deck.total_cards == 100
    assert deck.sections["Creatures"] == 29
    assert deck.sections["Lands"] == 36


def test_deck_page_card_quantities_sum_to_total(list_html: str) -> None:
    deck = parse_deck_page(list_html, "db746ea2-c1cd-4865-ae57-e40660530f24")
    assert sum(card.qty for card in deck.cards) == 100
    assert any(card.name == "Sol Ring" for card in deck.cards)


def test_card_prices_parsed(list_html: str) -> None:
    deck = parse_deck_page(list_html, "db746ea2-c1cd-4865-ae57-e40660530f24")
    sol_ring = next(card for card in deck.cards if card.name == "Sol Ring")
    assert sol_ring.price == pytest.approx(1.37)


@pytest.mark.parametrize(
    "value",
    [
        "db746ea2-c1cd-4865-ae57-e40660530f24",
        "https://manapool.com/lists/db746ea2-c1cd-4865-ae57-e40660530f24",
        "/lists/db746ea2-c1cd-4865-ae57-e40660530f24",
        "https://manapool.com/lists/db746ea2-c1cd-4865-ae57-e40660530f24?ref=x",
    ],
)
def test_deck_id_from_accepts_common_forms(value: str) -> None:
    assert deck_id_from(value) == "db746ea2-c1cd-4865-ae57-e40660530f24"


@pytest.mark.parametrize(
    "value",
    [
        "",
        "not-a-deck",
        "https://manapool.com/decks",
        "https://example.com/lists/" + str(uuid.uuid4()),
    ],
)
def test_deck_id_from_rejects_junk(value: str) -> None:
    with pytest.raises(ValueError):
        deck_id_from(value)
