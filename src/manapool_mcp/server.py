"""MCP server: ManaPool pre-built Commander decks + bracket auditing."""

from __future__ import annotations

import asyncio
import logging
import math
import os
import re
from contextlib import asynccontextmanager

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from . import __version__
from .client import (
    Fetcher,
    NotFoundError,
    UpstreamError,
    deck_url,
    decks_url,
)
from .models import CardEntry, ComboInfo, DeckAudit, DeckDetails, DeckSearchResult, DeckTile
from .parse import DeckParseError, deck_id_from, parse_deck_page, parse_deck_tiles, parse_total
from .rules import (
    BRACKET_CRITERIA,
    EXTRA_TURNS,
    GAME_CHANGERS,
    MASS_LAND_DENIAL,
    TUTORS,
    mechanical_bracket,
)
from .spellbook import find_combos

logging.getLogger("httpx").setLevel(logging.WARNING)

PAGE_SIZE = 24
MAX_SCAN_PAGES = 20
MAX_PAGES_PER_CALL = 4
SORTS = {"rank", "price-asc", "price-desc", "bracket"}
TRANSPORTS = {
    "stdio": "stdio",
    "http": "streamable-http",
    "streamable-http": "streamable-http",
    "streamable_http": "streamable-http",
}

_fetcher: Fetcher | None = None
_fetcher_lock: asyncio.Lock | None = None
_types_cache: dict[str, str] = {}


@asynccontextmanager
async def _lifespan(_server: MCPServer):
    try:
        yield
    finally:
        global _fetcher, _fetcher_lock
        if _fetcher is not None:
            await _fetcher.aclose()
        _fetcher = None
        _fetcher_lock = None


mcp = MCPServer(
    name="manapool",
    version=__version__,
    lifespan=_lifespan,
    instructions=(
        "ManaPool sells pre-built Commander decks assembled from a single "
        "seller's stock. Two things to know before using these tools:\n"
        "1. ManaPool's own bracket tag is generated and optimistic. Never "
        "report it as fact; call audit_deck to check the list.\n"
        "2. Prices move and decks are rebuilt when stock changes, so a saved "
        "list can differ from what you read a moment ago. Re-read before "
        "quoting a deck.\n"
        "Use search_decks to find candidates, get_deck to read one, and "
        "audit_deck to check Game Changers, mass land denial, extra turns and "
        "Commander Spellbook combos against the official bracket criteria."
    ),
)


async def _get_fetcher() -> Fetcher:
    global _fetcher, _fetcher_lock
    if _fetcher is not None:
        return _fetcher
    if _fetcher_lock is None:
        # Created lazily so the lock binds to the running loop, not to import time.
        _fetcher_lock = asyncio.Lock()
    async with _fetcher_lock:
        if _fetcher is None:
            _fetcher = Fetcher()
    return _fetcher


# --------------------------------------------------------------------------
# search_decks
# --------------------------------------------------------------------------


@mcp.tool()
async def search_decks(
    commander: str | None = None,
    bracket: int | None = None,
    min_price: float | None = None,
    max_price: float | None = None,
    theme: str | None = None,
    colors: str | None = None,
    sort: str = "rank",
    page: int = 0,
    limit: int = 24,
) -> DeckSearchResult:
    """Search ManaPool's pre-built Commander deck listings.

    Args:
        commander: Text query matched against deck/commander names, e.g.
            "Rendmaw" or "Zada". ManaPool's own search box.
        bracket: 2, 3 or 4. This is ManaPool's generated tag, not a verified
            bracket - use audit_deck before recommending anything.
        min_price: Lower bound in USD on the listing price (includes the
            seller's basic lands). ManaPool has no price filter, so this walks
            pages and filters locally.
        max_price: Upper bound in USD, same basis as min_price.
        theme: ManaPool theme label, matched exactly, e.g. "Aristocrats",
            "Spellslinger", "Landfall", "Goad", "Group Hug", "Cascade".
        colors: Colour identity letters, e.g. "WU" or "BRG".
        sort: "rank" (recommended), "price-asc", "price-desc" or "bracket".
            A price window forces "price-asc" because the scan needs it.
        page: Zero-based page of listings. When a price window is supplied
            this is an offset into the filtered matches instead.
        limit: Maximum decks to return (1-96).
    """
    if sort not in SORTS:
        raise ToolError(f"sort must be one of {sorted(SORTS)}")
    limit = max(1, min(int(limit), 96))
    if page < 0:
        raise ToolError("page must be >= 0")
    if bracket is not None and bracket not in (1, 2, 3, 4, 5):
        raise ToolError("bracket must be 1-5 (ManaPool's filter offers 2, 3 and 4)")
    if min_price is not None and max_price is not None and min_price > max_price:
        raise ToolError("min_price must be <= max_price")
    if colors:
        normalized = _normalise_colors(colors)
        if normalized is None:
            raise ToolError("colors must contain only WUBRG letters, e.g. 'WU' or 'BRG'")
        colors = normalized

    fetcher = await _get_fetcher()

    if min_price is None and max_price is None:
        return await _search_single_page(
            fetcher,
            commander=commander,
            bracket=bracket,
            theme=theme,
            colors=colors,
            sort=sort,
            page=page,
            limit=limit,
        )

    return await _search_price_window(
        fetcher,
        commander=commander,
        bracket=bracket,
        min_price=min_price,
        max_price=max_price,
        theme=theme,
        colors=colors,
        page=page,
        limit=limit,
    )


async def _search_single_page(
    fetcher: Fetcher,
    *,
    commander: str | None,
    bracket: int | None,
    theme: str | None,
    colors: str | None,
    sort: str,
    page: int,
    limit: int,
) -> DeckSearchResult:
    pages_needed = min(MAX_PAGES_PER_CALL, math.ceil(limit / PAGE_SIZE))
    tiles: list[DeckTile] = []
    total: int | None = None
    fetched = 0
    for offset in range(pages_needed):
        html = await fetcher.get_text(
            decks_url(
                page=page + offset,
                bracket=bracket,
                sort=sort if sort != "rank" else None,
                query=commander,
                theme=theme,
                colors=colors,
            )
        )
        if total is None:
            total = parse_total(html)
        chunk = parse_deck_tiles(html)
        if not chunk:
            break
        fetched += 1
        tiles.extend(chunk)
    return DeckSearchResult(
        total_listings=total,
        page=page,
        decks=tiles[:limit],
        scanned_pages=fetched,
        note=None if len(tiles) >= limit else "Listing ran out before the requested limit.",
    )


async def _search_price_window(
    fetcher: Fetcher,
    *,
    commander: str | None,
    bracket: int | None,
    min_price: float | None,
    max_price: float | None,
    theme: str | None,
    colors: str | None,
    page: int,
    limit: int,
) -> DeckSearchResult:
    """Filter by price locally because ManaPool has no price parameter.

    ManaPool's price sort is *mostly* ascending, but not reliably: rebuilt
    decks are appended to the tail with their new price, so the last pages can
    hold cheap listings. There is therefore no monotone predicate to binary
    search. When the result set fits the page budget it is scanned in full and
    reported as exhaustive; otherwise it is sampled and flagged truncated.
    """
    lo = min_price if min_price is not None else 0.0
    hi = max_price if max_price is not None else float("inf")

    def in_window(tile: DeckTile) -> bool:
        return tile.price is not None and lo <= tile.price <= hi

    def urls_for(indices: list[int]) -> list[str]:
        return [
            decks_url(
                page=index,
                bracket=bracket,
                sort="price-asc",
                query=commander,
                theme=theme,
                colors=colors,
            )
            for index in indices
        ]

    first_html = await fetcher.get_text(
        decks_url(bracket=bracket, sort="price-asc", query=commander, theme=theme, colors=colors)
    )
    total = parse_total(first_html)
    if total is None:
        return DeckSearchResult(
            total_listings=None,
            page=page,
            decks=[],
            scanned_pages=0,
            note="ManaPool returned no listing count; the page may have changed.",
        )
    total_pages = max(1, math.ceil(total / PAGE_SIZE))
    cache: dict[int, list[DeckTile]] = {0: parse_deck_tiles(first_html)}

    async def page_bounds(index: int) -> tuple[float, float] | None:
        if index not in cache:
            html = await fetcher.get_text(urls_for([index])[0])
            cache[index] = parse_deck_tiles(html)
        prices = [t.price for t in cache[index] if t.price is not None]
        return (min(prices), max(prices)) if prices else None

    async def ensure_pages(indices: list[int]) -> None:
        missing = [index for index in indices if index not in cache]
        for chunk_start in range(0, len(missing), 8):
            chunk = missing[chunk_start : chunk_start + 8]
            pages = await fetcher.get_many(urls_for(chunk))
            for index, html in zip(chunk, pages, strict=True):
                cache[index] = parse_deck_tiles(html)

    exhaustive = total_pages <= MAX_SCAN_PAGES
    truncated = False
    note: str | None = None

    if exhaustive:
        indices = list(range(total_pages))
        await ensure_pages(indices)
        empty_pages = [index for index in indices if not cache[index]]
        if empty_pages:
            # total_pages is derived from the listing count, so an empty page in
            # range means the markup or the count moved. Never claim completeness.
            truncated = True
            note = (
                f"{len(empty_pages)} page(s) returned no decks; the listing may "
                "have changed while scanning, so results may be incomplete."
            )
    else:
        # The sort is only mostly ascending, so bisection is a hint, not a proof.
        low, high = 0, total_pages - 1
        start_page: int | None = None
        while low <= high:
            mid = (low + high) // 2
            bounds = await page_bounds(mid)
            if bounds is not None and bounds[1] >= lo:
                start_page = mid
                high = mid - 1
            else:
                low = mid + 1
        # A missed hint must degrade to sampling, never to "nothing here".
        if start_page is None:
            start_page = 0
        span = total_pages - start_page
        stride = max(1, math.ceil(span / MAX_SCAN_PAGES))
        # Always pin the final page: rebuilt decks are appended to the tail with
        # their new price, which is exactly where out-of-order listings live.
        indices = sorted({*range(start_page, total_pages, stride), total_pages - 1})
        await ensure_pages(indices)
        truncated = True

    # Every page in the cache was fetched for this call, including bisection
    # probes, so none of that work is discarded.
    collected: list[DeckTile] = [
        tile for index in sorted(cache) for tile in cache[index] if in_window(tile)
    ]

    if truncated and note is None:
        upper = f"${hi:.0f}" if math.isfinite(hi) else "any price"
        note = (
            f"This window covers {total_pages} pages, more than the {MAX_SCAN_PAGES}-page "
            f"budget, so it sampled {len(indices)} pages across ${lo:.0f} to {upper}. "
            "Results are a sample, not the complete set; narrow with commander or "
            "theme for full coverage."
        )
    elif not collected:
        upper = f"${hi:.0f}" if math.isfinite(hi) else "any price"
        note = f"No listings between ${lo:.0f} and {upper}."

    return DeckSearchResult(
        total_listings=total,
        page=page,
        decks=collected[page * limit : page * limit + limit],
        scanned_pages=len(cache),
        truncated=truncated,
        note=note,
    )


# --------------------------------------------------------------------------
# get_deck
# --------------------------------------------------------------------------


@mcp.tool()
async def get_deck(deck: str, include_types: bool = False) -> DeckDetails:
    """Fetch one ManaPool deck by URL or list id.

    Args:
        deck: https://manapool.com/lists/<uuid> or the bare uuid.
        include_types: Also resolve each card's Scryfall type line. Useful for
            multi-type payoffs (e.g. Rendmaw) but costs extra requests.
    """
    try:
        deck_id = deck_id_from(deck)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc

    fetcher = await _get_fetcher()
    try:
        html = await fetcher.get_text(deck_url(deck_id))
    except NotFoundError as exc:
        raise ToolError(str(exc)) from exc

    try:
        details = parse_deck_page(html, deck_id)
    except DeckParseError as exc:
        raise ToolError(str(exc)) from exc

    if include_types:
        types = await _scryfall_types(fetcher, [card.name for card in details.cards])
        details.cards = [
            CardEntry(
                qty=card.qty,
                name=card.name,
                price=card.price,
                type_line=types.get(_norm(card.name)),
            )
            for card in details.cards
        ]
    return details


async def _scryfall_types(fetcher: Fetcher, names: list[str]) -> dict[str, str]:
    missing = [n for n in dict.fromkeys(names) if _norm(n) not in _types_cache]
    for start in range(0, len(missing), 75):
        chunk = missing[start : start + 75]
        payload = {"identifiers": [{"name": n} for n in chunk]}
        try:
            data = await fetcher.post_json("https://api.scryfall.com/cards/collection", payload)
        except UpstreamError:
            break
        if not isinstance(data, dict):
            break
        for card in data.get("data", []) or []:
            if isinstance(card, dict) and card.get("name"):
                _types_cache[_norm(card["name"])] = card.get("type_line", "")
        for card in data.get("not_found", []) or []:
            if isinstance(card, dict) and card.get("name"):
                _types_cache[_norm(card["name"])] = ""
    return {_norm(name): _types_cache.get(_norm(name), "") for name in names}


# --------------------------------------------------------------------------
# audit_deck
# --------------------------------------------------------------------------


@mcp.tool()
async def audit_deck(deck: str) -> DeckAudit:
    """Check a ManaPool deck against the official Commander bracket criteria.

    Counts Game Changers, mass land denial, extra turns and tutors, and asks
    Commander Spellbook for combos fully present in the list. Returns the
    lowest bracket the card list mechanically permits.

    This checks the list, not intent: a zero-Game-Changer deck can still play
    like Upgraded, and an accidental two-card combo will flag higher than the
    pilot means. Report it as "what the cards permit", not as a verdict on
    power.
    """
    details = await get_deck(deck, include_types=False)
    names = [card.name for card in details.cards]
    keys_by_card = {name: _name_keys(name) for name in names}

    game_changers = _match(names, keys_by_card, GAME_CHANGERS)
    land_denial = _match(names, keys_by_card, MASS_LAND_DENIAL)
    extra_turns = _match(names, keys_by_card, EXTRA_TURNS)
    tutors = _match(names, keys_by_card, TUTORS)

    commanders = details.commanders or ([details.commander] if details.commander else [])
    combos: list[ComboInfo] = []
    combo_note: str | None = None
    if commanders:
        try:
            combos = await find_combos(
                await _get_fetcher(),
                commanders,
                [n for n in names if n not in commanders],
            )
        except UpstreamError as exc:
            combo_note = f"Combo check unavailable: {exc}"

    gc_count = len(game_changers)
    minimum, notes = mechanical_bracket(gc_count, bool(land_denial), len(combos))

    if combos:
        notes.append(
            f"{len(combos)} combo(s) fully present. A compact two-card win that "
            "lands before about turn six is bracket 4; verify the speed."
        )
    if extra_turns:
        notes.append(
            "Extra turn spells found. Legal at bracket 3 but not chained; "
            "chaining them is bracket 4."
        )
    if tutors:
        notes.append(
            "Tutors listed for information only - tutor guidance was removed "
            "from the bracket rules in October 2025."
        )
    if combo_note:
        notes.append(combo_note)

    summary = f"Mechanically at least Bracket {minimum}."
    summary += (
        " Game Changers: " + ", ".join(game_changers) + "."
        if game_changers
        else " No Game Changers."
    )

    return DeckAudit(
        id=details.id,
        url=details.url,
        name=details.name,
        commander=details.commander,
        commanders=commanders,
        seller=details.seller,
        price_cards=details.price_cards,
        total_cards=details.total_cards,
        game_changers=game_changers,
        game_changer_count=gc_count,
        mass_land_denial=land_denial,
        extra_turns=extra_turns,
        tutors=tutors,
        combos=combos,
        mechanical_minimum_bracket=minimum,
        verdict=summary,
        notes=notes,
    )


# --------------------------------------------------------------------------
# Resources
# --------------------------------------------------------------------------


@mcp.resource(
    "manapool://rules/game-changers",
    name="Game Changers",
    description="The official 53-card Game Changers list (Feb 2026 update).",
    mime_type="text/plain",
)
def game_changers_resource() -> str:
    return "\n".join(GAME_CHANGERS)


@mcp.resource(
    "manapool://rules/brackets",
    name="Commander bracket criteria",
    description="Official bracket definitions and what each one permits.",
    mime_type="text/plain",
)
def brackets_resource() -> str:
    return BRACKET_CRITERIA


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _normalise_colors(colors: str | None) -> str | None:
    if not colors:
        return None
    letters = re.sub(r"[^WUBRGwubrg]", "", colors).upper()
    return letters or None


def _norm(value: str) -> str:
    value = value.replace("\u2019", "'").lower()
    value = re.sub(r"[^a-z0-9' ]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _name_keys(name: str) -> set[str]:
    """Normalised keys for a card, covering split cards and ManaPool aliases.

    ManaPool rewrites some names as "Skinwalker (Real Card Name)", so the outer
    name, the parenthesised name, and both halves of a split card all match.
    """
    alias = re.search(r"\(([^)]+)\)\s*$", name)
    base = name[: alias.start()].strip() if alias else name
    keys = {_norm(base)}
    if alias:
        keys.add(_norm(alias.group(1)))
    if " // " in base:
        for part in base.split(" // "):
            keys.add(_norm(part))
    return {key for key in keys if key}


def _match(
    names: list[str], keys_by_card: dict[str, set[str]], rules: tuple[str, ...]
) -> list[str]:
    index: dict[str, str] = {}
    for rule in rules:
        for key in _name_keys(rule):
            index[key] = rule
    found: list[str] = []
    for name in names:
        for key in keys_by_card.get(name, set()):
            rule = index.get(key)
            if rule and rule not in found:
                found.append(rule)
                break
    return found


def main() -> None:
    raw = os.environ.get("MANAPOOL_TRANSPORT", "stdio").lower().strip()
    if raw not in TRANSPORTS:
        raise ValueError(f"MANAPOOL_TRANSPORT must be one of {sorted(TRANSPORTS)}, got {raw!r}")
    transport = TRANSPORTS[raw]
    if transport == "streamable-http":
        try:
            port = int(os.environ.get("MANAPOOL_PORT", "8765"))
        except ValueError as exc:
            raise ValueError("MANAPOOL_PORT must be an integer") from exc
        mcp.run(
            transport="streamable-http",
            host=os.environ.get("MANAPOOL_HOST", "127.0.0.1"),
            port=port,
        )
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
