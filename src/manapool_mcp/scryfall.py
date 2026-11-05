"""Scryfall lookups used to resolve names and run card searches.

ManaPool's public API indexes cards by Scryfall id, so Scryfall is the bridge
between a card name (or a search query) and a ManaPool product record.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlencode

from .client import Fetcher, NotFoundError, UpstreamError

SCRYFALL = "https://api.scryfall.com"


def _norm(value: str) -> str:
    value = value.replace("\u2019", "'").lower()
    value = re.sub(r"[^a-z0-9' ]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


async def cards_by_name(fetcher: Fetcher, names: list[str]) -> dict[str, dict[str, Any] | None]:
    """Resolve card names to Scryfall card objects, keyed by normalised name.

    Unknown names map to None. Uses the collection endpoint, 75 names a call.
    """
    unresolved = [name for name in dict.fromkeys(names) if name]
    resolved: dict[str, dict[str, Any] | None] = {}
    for start in range(0, len(unresolved), 75):
        chunk = unresolved[start : start + 75]
        payload = {"identifiers": [{"name": name} for name in chunk]}
        try:
            data = await fetcher.post_json(f"{SCRYFALL}/cards/collection", payload)
        except UpstreamError:
            break
        if not isinstance(data, dict):
            break
        for card in data.get("data", []) or []:
            if isinstance(card, dict) and card.get("name"):
                resolved[_norm(card["name"])] = card
        for missing in data.get("not_found", []) or []:
            if isinstance(missing, dict) and missing.get("name"):
                resolved[_norm(missing["name"])] = None
    for name in unresolved:
        resolved.setdefault(_norm(name), None)
    return resolved


async def search(fetcher: Fetcher, query: str, limit: int = 15) -> list[dict[str, Any]]:
    """Run a Scryfall search, newest page first, up to `limit` cards."""
    cards: list[dict[str, Any]] = []
    page = 1
    while len(cards) < limit and page <= 5:
        params = urlencode({"q": query, "page": page})
        try:
            data = await fetcher.get_json(f"{SCRYFALL}/cards/search?{params}")
        except NotFoundError:
            break
        if not isinstance(data, dict):
            break
        batch = [card for card in data.get("data", []) if isinstance(card, dict)]
        cards.extend(batch)
        if not data.get("has_more") or not batch:
            break
        page += 1
    return cards[:limit]


async def card_by_set_number(fetcher: Fetcher, set_code: str, number: str) -> dict[str, Any] | None:
    try:
        data = await fetcher.get_json(f"{SCRYFALL}/cards/{set_code}/{number}")
    except (NotFoundError, UpstreamError):
        return None
    return data if isinstance(data, dict) and data.get("name") else None


async def card_by_id(fetcher: Fetcher, scryfall_id: str) -> dict[str, Any] | None:
    try:
        data = await fetcher.get_json(f"{SCRYFALL}/cards/{scryfall_id}")
    except (NotFoundError, UpstreamError):
        return None
    return data if isinstance(data, dict) and data.get("name") else None


def normalise(value: str) -> str:
    return _norm(value)
