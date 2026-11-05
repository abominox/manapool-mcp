"""ManaPool's authenticated API endpoints.

Unlike the rest of the package these need a ManaPool account: an e-mail plus an
access token, sent as ``X-ManaPool-Email`` and ``X-ManaPool-Access-Token``.

Two rules follow from the API shape and from who writes the decklist:

* The deck validator resolves cards **by name**, and ManaPool sells some cards
  under a rewritten name (``"Memories of Nibelheim (Stroke of Midnight)"``).
  These helpers strip the parenthetical alias before sending, otherwise the
  validator reports a real card as not found.
* ``cards_not_found`` means "ManaPool could not resolve this", which is not the
  same as "this card is illegal". Callers must not treat it as a verdict.
"""

from __future__ import annotations

import re
from typing import Any

from .client import Fetcher, UpstreamError
from .models import (
    CartLine,
    CartQuote,
    CartTotals,
    ColorIdentityViolation,
    DeckValidation,
    QuantityViolation,
)
from .products import cents_to_usd

API_BASE = "https://manapool.com/api/v1"
VALIDATE_URL = f"{API_BASE}/deck"
OPTIMIZE_URL = f"{API_BASE}/buyer/optimizer"
LISTINGS_URL = f"{API_BASE}/inventory/listings"

MAX_CART_LINES = 2000
MAX_LISTING_IDS = 100


def card_name_for_api(name: str) -> str:
    """Strip ManaPool's "Alias (Real Card Name)" rewrite.

    Split and adventure cards ("Fire // Ice") keep their separator; only the
    trailing parenthetical is removed, and only when a real name precedes it.
    """
    match = re.search(r"\s*\(([^()]+)\)\s*$", name)
    if match and name[: match.start()].strip():
        return name[: match.start()].strip()
    return name.strip()


def _text_list(value: Any) -> list[str]:
    """Coerce a list of names, tolerating objects with a `name` key."""
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, dict) and isinstance(item.get("name"), str):
            out.append(item["name"])
    return out


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


async def validate_deck(
    fetcher: Fetcher, commanders: list[str], cards: list[tuple[int, str]]
) -> DeckValidation:
    """Ask ManaPool whether this is a legal Commander deck.

    ``cards`` is ``(quantity, name)`` for every non-commander card, basics
    included. Raises AuthError when credentials are missing or rejected, and
    UpstreamError for anything else the caller may want to degrade on.
    """
    payload = {
        "commander_names": [
            cleaned for cleaned in (card_name_for_api(name) for name in commanders) if cleaned
        ],
        "other_cards": [
            {"name": cleaned, "quantity": max(1, _int(qty, 1))}
            for qty, name in cards
            for cleaned in [card_name_for_api(name)]
            if cleaned
        ],
    }
    data = await fetcher.post_json_keyed(VALIDATE_URL, payload)
    if not isinstance(data, dict):
        raise UpstreamError("ManaPool deck validation returned an unexpected payload")

    details = data.get("details")
    details = details if isinstance(details, dict) else {}

    quantity_violations = [
        QuantityViolation(
            name=str(item.get("name", "")),
            quantity=_int(item.get("quantity")),
            max_allowed=_int(item.get("max_allowed")),
        )
        for item in details.get("quantity_violations") or []
        if isinstance(item, dict)
    ]
    identity_violations = [
        ColorIdentityViolation(
            name=str(item.get("name", "")),
            card_colors=_text_list(item.get("card_colors")),
            commander_colors=_text_list(item.get("commander_colors")),
        )
        for item in details.get("color_identity_violations") or []
        if isinstance(item, dict)
    ]

    not_found = _text_list(details.get("cards_not_found"))
    note = None
    if not_found:
        note = (
            "ManaPool could not resolve some card names, so its verdict is "
            "incomplete. Check the spelling of the cards in cards_not_found."
        )

    return DeckValidation(
        valid=bool(data.get("valid")),
        commander_count=_int(details.get("commander_count")),
        total_card_count=_int(details.get("total_card_count")),
        all_cards_legal=bool(details.get("all_cards_legal", True)),
        illegal_cards=_text_list(details.get("illegal_cards")),
        cards_not_found=not_found,
        valid_quantities=bool(details.get("valid_quantities", True)),
        quantity_violations=quantity_violations,
        valid_color_identity=bool(details.get("valid_color_identity", True)),
        color_identity_violations=identity_violations,
        valid_partnership=bool(details.get("valid_partnership", True)),
        partner_violations=_text_list(details.get("partner_violations")),
        buy_url=data.get("buy_url") if isinstance(data.get("buy_url"), str) else None,
        note=note,
    )


async def _listings(fetcher: Fetcher, inventory_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Price and identify optimizer allocations.

    `/inventory/listings` is public, so this still works when the catalog lookups
    are degraded. Any failure just means lines come back unpriced.
    """
    ids = [value for value in dict.fromkeys(inventory_ids) if value]
    found: dict[str, dict[str, Any]] = {}
    for start in range(0, len(ids), MAX_LISTING_IDS):
        chunk = ids[start : start + MAX_LISTING_IDS]
        query = "&".join(f"id={value}" for value in chunk)
        try:
            data = await fetcher.get_json(f"{LISTINGS_URL}?{query}")
        except UpstreamError:
            continue
        if not isinstance(data, dict):
            continue
        for item in data.get("inventory_items") or []:
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                found[item["id"]] = item
    return found


async def optimize_cart(
    fetcher: Fetcher,
    lines: list[tuple[int, str]],
    *,
    conditions: list[str] | None = None,
    finishes: list[str] | None = None,
    languages: list[str] | None = None,
) -> CartQuote:
    """Quote a decklist as one ManaPool cart.

    ``lines`` is ``(quantity, name)``. Printings are not pinned: the optimizer
    keeps its freedom to substitute an interchangeable printing, because
    pinning one would defeat the point of asking for the cheapest legal copy.
    """
    # Merge repeated names first: two lines for the same card are one cart line,
    # and the optimizer's flat allocation list could not tell them apart later.
    merged: dict[str, int] = {}
    for qty, name in lines:
        cleaned = card_name_for_api(name)
        if cleaned:
            merged[cleaned] = merged.get(cleaned, 0) + max(1, _int(qty, 1))

    cart: list[dict[str, Any]] = []
    requested: list[tuple[int, str]] = []
    for cleaned, quantity in merged.items():
        item: dict[str, Any] = {"type": "mtg_single", "name": cleaned}
        item["quantity_requested"] = quantity
        # Filters are omitted, not sent as null: the schema types them as arrays.
        if languages:
            item["language_ids"] = languages
        if finishes:
            item["finish_ids"] = finishes
        if conditions:
            item["condition_ids"] = conditions
        cart.append(item)
        requested.append((quantity, cleaned))

    if not cart:
        raise UpstreamError("optimize_cart needs at least one card")
    if len(cart) > MAX_CART_LINES:
        raise UpstreamError(f"optimize_cart accepts at most {MAX_CART_LINES} lines")

    data = await fetcher.post_json_keyed(OPTIMIZE_URL, {"cart": cart})
    if not isinstance(data, dict):
        raise UpstreamError("ManaPool cart optimizer returned an unexpected payload")

    totals_raw = data.get("totals")
    totals = None
    if isinstance(totals_raw, dict):
        totals = CartTotals(
            subtotal_usd=cents_to_usd(totals_raw.get("subtotal_cents")) or 0.0,
            shipping_usd=cents_to_usd(totals_raw.get("shipping_cents")) or 0.0,
            buyer_fee_usd=cents_to_usd(totals_raw.get("buyer_fee_cents")) or 0.0,
            gather_fee_usd=cents_to_usd(totals_raw.get("gathered_fee_cents")) or 0.0,
            total_usd=cents_to_usd(totals_raw.get("total_cents")) or 0.0,
            seller_count=_int(totals_raw.get("seller_count")),
        )

    allocations: dict[str, int] = {}
    for item in data.get("cart") or []:
        if not isinstance(item, dict):
            continue
        inventory_id = item.get("inventory_id")
        if isinstance(inventory_id, str) and inventory_id:
            allocations[inventory_id] = _int(item.get("quantity_selected"))

    detail = await _listings(fetcher, list(allocations))

    # The optimizer answers with a flat list of inventory units, not with one
    # entry per requested line, so lines are reconstituted by card name.
    by_key: dict[str, list[tuple[str, dict[str, Any], int]]] = {}
    for inventory_id, allocated in allocations.items():
        record = detail.get(inventory_id) or {}
        single = record.get("single") if isinstance(record.get("single"), dict) else {}
        for key in _match_keys(str(single.get("name") or "")):
            by_key.setdefault(key, []).append((inventory_id, single, allocated))

    # An allocation with no listing detail cannot be attributed by name, which
    # is a pricing gap rather than proof that the card is out of stock.
    unpriced = {inventory_id for inventory_id in allocations if inventory_id not in detail}

    allocated_total = sum(allocations.values())
    # Units that came back but belong to no identifiable card. These explain
    # away that many unmatched lines before a genuine stock gap is declared.
    unidentified = sum(allocations[i] for i in unpriced)

    # A card can be requested on more than one line; consume each inventory
    # unit once so the same allocation is not reported (and priced) twice.
    consumed: set[str] = set()
    quoted: list[CartLine] = []
    missing: list[str] = []
    for quantity, name in requested:
        matched: list[tuple[str, dict[str, Any], int]] = []
        seen_ids: set[str] = set()
        for key in _match_keys(name):
            for entry in by_key.get(key, []):
                if entry[0] not in seen_ids and entry[0] not in consumed:
                    seen_ids.add(entry[0])
                    matched.append(entry)
        if not matched:
            # Charged against unidentified units first: those were allocated,
            # so blaming them for a stock gap would be a lie about availability.
            if unidentified >= quantity:
                unidentified -= quantity
            else:
                missing.append(name)
            quoted.append(CartLine(name=name, qty=quantity))
            continue
        for inventory_id, single, allocated in matched:
            consumed.add(inventory_id)
            record = detail.get(inventory_id) or {}
            unit = cents_to_usd(record.get("price_cents"))
            quoted.append(
                CartLine(
                    name=str(single.get("name") or name),
                    set_code=single.get("set"),
                    number=str(single.get("number")) if single.get("number") else None,
                    scryfall_id=single.get("scryfall_id"),
                    qty=allocated,
                    unit_price=unit,
                    line_total=round(unit * allocated, 2) if unit is not None else None,
                    inventory_id=inventory_id,
                )
            )

    allocated_cards = allocated_total
    derived = round(
        sum(line.line_total or 0.0 for line in quoted if line.line_total is not None), 2
    )
    # Never report a silent zero for a quoting tool. `total_cents` can be absent
    # or zero while the other components are present, so trust the API total
    # only when it is actually non-zero.
    api_total = totals.total_usd if totals is not None else None
    total_price = api_total if api_total else derived

    note = (
        "One cart from ManaPool's live stock, including its shipping and fees "
        "estimate. Prices move as stock changes; rerun before buying."
    )
    if not api_total:
        note += " ManaPool returned no usable total; total_price sums the resolved lines only."
    if unpriced:
        note += (
            f" {len(unpriced)} allocated item(s) could not be priced, so some lines are unpriced."
        )

    return CartQuote(
        total_cards=allocated_cards,
        total_price=total_price,
        lines=quoted,
        totals=totals,
        missing=missing,
        note=note,
    )


def _norm(value: str) -> str:
    value = value.replace("\u2019", "'").lower()
    value = re.sub(r"[^a-z0-9' ]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _match_keys(name: str) -> list[str]:
    """Normalised keys for a card: outer name, alias name, and split halves."""
    alias = re.search(r"\(([^)]+)\)\s*$", name)
    base = name[: alias.start()].strip() if alias else name
    keys = [_norm(base)]
    if alias:
        keys.append(_norm(alias.group(1)))
    if " // " in base:
        keys.extend(_norm(part) for part in base.split(" // "))
    out: list[str] = []
    for key in keys:
        if key and key not in out:
            out.append(key)
    return out
