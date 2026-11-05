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
