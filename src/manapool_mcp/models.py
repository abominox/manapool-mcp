"""Typed results returned by the MCP tools."""

from __future__ import annotations

from pydantic import BaseModel, Field


class DeckTile(BaseModel):
    id: str
    url: str
    name: str
    theme: str | None = None
    seller: str | None = None
    bracket: int | None = None
    price: float | None = Field(
        default=None,
        description="Listing price in USD, including the seller's basic lands.",
    )


class DeckSearchResult(BaseModel):
    total_listings: int | None = Field(
        default=None, description="Total listings matching the filters on ManaPool."
    )
    page: int = Field(description="Zero-based page that was requested.")
    decks: list[DeckTile]
    scanned_pages: int = Field(description="How many result pages were fetched.")
    truncated: bool = Field(
        default=False,
        description="True when the price window extends past the pages that were scanned.",
    )
    note: str | None = None


class CardEntry(BaseModel):
    qty: int
    name: str
    price: float | None = None
    type_line: str | None = Field(
        default=None, description="Scryfall type line, only present when include_types=true."
    )


class DeckDetails(BaseModel):
    id: str
    url: str
    name: str
    commander: str | None = None
    commanders: list[str] = Field(
        default_factory=list, description="Commander plus any partner/background."
    )
    theme: str | None = None
    seller: str | None = None
    price_cards: float | None = Field(
        default=None, description="Cards-only total at the seller (excludes basic lands)."
    )
    basic_lands: int = 0
    total_cards: int = 0
    sections: dict[str, int] = Field(default_factory=dict)
    cards: list[CardEntry] = Field(default_factory=list)


class ComboInfo(BaseModel):
    description: str | None = None
    cards: list[str] = Field(default_factory=list)
    bracket_tag: str | None = None


class DeckAudit(BaseModel):
    id: str
    url: str
    name: str
    commander: str | None = None
    commanders: list[str] = Field(
        default_factory=list, description="Commander plus any partner/background."
    )
    seller: str | None = None
    price_cards: float | None = None
    total_cards: int = 0
    game_changers: list[str] = Field(default_factory=list)
    game_changer_count: int = 0
    mass_land_denial: list[str] = Field(default_factory=list)
    extra_turns: list[str] = Field(default_factory=list)
    tutors: list[str] = Field(
        default_factory=list,
        description="Informational only. Tutor guidance was removed from the bracket rules in Oct 2025.",
    )
    combos: list[ComboInfo] = Field(default_factory=list)
    mechanical_minimum_bracket: int = Field(
        description="Lowest bracket the card list mechanically permits."
    )
    verdict: str
    notes: list[str] = Field(default_factory=list)


class CardPrice(BaseModel):
    name: str
    set_code: str | None = None
    set_name: str | None = None
    scryfall_id: str | None = None
    type_line: str | None = None
    mana_value: float | None = None
    price_usd: float | None = Field(
        default=None, description="Lowest available price across conditions."
    )
    nm_usd: float | None = Field(default=None, description="Near-mint price.")
    foil_usd: float | None = None
    market_usd: float | None = None
    stock: int = 0
    url: str | None = None


class CardSearchResult(BaseModel):
    query: str
    total_cards: int | None = None
    cards: list[CardPrice] = Field(default_factory=list)
    missing_from_manapool: list[str] = Field(default_factory=list)


class Listing(BaseModel):
    condition: str | None = None
    finish: str | None = None
    language: str | None = None
    price_usd: float | None = None
    quantity: int = 0


class CardDetails(BaseModel):
    name: str
    set_code: str | None = None
    set_name: str | None = None
    scryfall_id: str | None = None
    type_line: str | None = None
    oracle_text: str | None = None
    price_usd: float | None = None
    nm_usd: float | None = None
    foil_usd: float | None = None
    market_usd: float | None = None
    stock: int = 0
    url: str | None = None
    listings: list[Listing] = Field(default_factory=list)


class DeckPriceLine(BaseModel):
    name: str
    qty: int = 1
    unit_price: float | None = None
    line_total: float | None = None
    set_code: str | None = None
    number: str | None = None
    available: int = 0
    short: bool = Field(
        default=False,
        description="True when ManaPool stock is below the requested quantity.",
    )


class DeckPricing(BaseModel):
    as_of: str | None = None
    total_cards: int = 0
    total_price: float = 0.0
    commander: str | None = None
    commander_price: float | None = None
    lines: list[DeckPriceLine] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    short: list[str] = Field(default_factory=list)
    note: str | None = None
