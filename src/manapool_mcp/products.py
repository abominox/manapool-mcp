"""ManaPool public API client (https://manapool.com/api/v1).

Only the anonymously reachable endpoints are used here. Of the 53 paths in the
OpenAPI document at /api/docs/v1/openapi.json, most require an API key: the
deck validator (/deck), /card_info, the cart optimizer, inventory, orders and
account endpoints all return 401 without credentials. The prices exports and
/products/singles are public, and they are what this module wraps.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from .client import Fetcher, UpstreamError

API_BASE = "https://manapool.com/api/v1"
MAX_IDS_PER_CALL = 100


async def products_by_scryfall_ids(
    fetcher: Fetcher, scryfall_ids: list[str]
) -> list[dict[str, Any]]:
    """Fetch ManaPool product records for Scryfall ids, 100 per request.

    The endpoint takes repeated `scryfall_ids` parameters; a comma-separated
    list is rejected as a single invalid UUID.
    """
    ids = [value for value in dict.fromkeys(scryfall_ids) if value]
    records: list[dict[str, Any]] = []
    for start in range(0, len(ids), MAX_IDS_PER_CALL):
        chunk = ids[start : start + MAX_IDS_PER_CALL]
        query = urlencode([("scryfall_ids", value) for value in chunk])
        try:
            data = await fetcher.get_json(f"{API_BASE}/products/singles?{query}")
        except UpstreamError:
            continue
        if isinstance(data, dict):
            records.extend(record for record in data.get("data", []) if isinstance(record, dict))
    return records


def cents_to_usd(value: Any) -> float | None:
    if value in (None, "", 0):
        return None
    try:
        return round(int(value) / 100, 2)
    except (TypeError, ValueError):
        return None
