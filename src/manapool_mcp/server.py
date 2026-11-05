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

from . import __version__, api
from .auth import CONFIG_HINT, Credential, credential_problem, load_credentials
from .client import (
    AuthError,
    Fetcher,
    NotFoundError,
    UpstreamError,
    deck_url,
    decks_url,
)
from .models import (
    CardDetails,
    CardEntry,
    CardPrice,
    CardSearchResult,
    CartQuote,
    ComboInfo,
    DeckAudit,
    DeckDetails,
    DeckPriceLine,
    DeckPricing,
    DeckSearchResult,
    DeckTile,
    DeckValidation,
    Listing,
)
from .parse import DeckParseError, deck_id_from, parse_deck_page, parse_deck_tiles, parse_total
from .prices import load_price_book
from .products import cents_to_usd, products_by_scryfall_ids
from .rules import (
    BRACKET_CRITERIA,
    EXTRA_TURNS,
    GAME_CHANGERS,
    MASS_LAND_DENIAL,
    TUTORS,
    mechanical_bracket,
)
from .scryfall import card_by_id, card_by_set_number, cards_by_name
from .scryfall import search as scryfall_search
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
        "Commander Spellbook combos against the official bracket criteria.\n"
        "When ManaPool API credentials are configured (MANAPOOL_EMAIL and "
        "MANAPOOL_ACCESS_TOKEN), audit_deck also includes ManaPool's own rules "
        "check, and validate_decklist and optimize_cart become usable. Those "
        "two need the credentials; the rest of the tools never do."
    ),
)


def _sync_credentials(fetcher: Fetcher) -> None:
    """Keep the singleton fetcher's credentials in step with the environment.

    Credentials are read per call, but the Fetcher is built once per process,
    so a key set (or revoked) after startup must still take effect. Duck-typed
    test fetchers have no credential surface and are left alone.
    """
    install = getattr(fetcher, "set_credentials", None)
    if install is None:
        return
    current = load_credentials()
    if getattr(fetcher, "credentials", None) != current:
        install(current)


async def _get_fetcher() -> Fetcher:
    global _fetcher, _fetcher_lock
    if _fetcher is not None:
        _sync_credentials(_fetcher)
        return _fetcher
    if _fetcher_lock is None:
        # Created lazily so the lock binds to the running loop, not to import time.
        _fetcher_lock = asyncio.Lock()
    async with _fetcher_lock:
        if _fetcher is None:
            _fetcher = Fetcher(credentials=load_credentials())
    return _fetcher


def _require_credentials() -> Credential:
    """The configured credential, or a readable ToolError.

    Purely a check: the fetcher picks the credential up in `_get_fetcher`.
    """
    problem = credential_problem()
    if problem:
        raise ToolError(problem)
    credential = load_credentials()
    if credential is None:
        raise ToolError(f"These tools need ManaPool API credentials. {CONFIG_HINT}")
    return credential


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
    if missing:
        resolved = await cards_by_name(fetcher, missing)
        for key, card in resolved.items():
            _types_cache[key] = (card or {}).get("type_line", "")
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

    authoritative: DeckValidation | None = None
    if load_credentials():
        # Same degradation rule as the combo check: a failed rules check must
        # not sink an otherwise useful audit, but it is reported, never hidden.
        try:
            authoritative = await api.validate_deck(
                await _get_fetcher(),
                commanders,
                _validation_lines(details.cards, commanders),
            )
        except AuthError as exc:
            notes.append(f"ManaPool validation rejected the credentials: {exc}")
        except UpstreamError as exc:
            notes.append(f"ManaPool validation unavailable: {exc}")
        else:
            if authoritative.note:
                notes.append(authoritative.note)
            notes.append(
                "Bracket and legality are separate checks: ManaPool's validator "
                "covers deck construction rules, not power level."
            )

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
        authoritative=authoritative,
    )


def _validation_lines(cards: list[CardEntry], commanders: list[str]) -> list[tuple[int, str]]:
    """(quantity, name) for every non-commander card, as POST /deck expects.

    Commander rows are taken from the card list by name; anything left over is
    the 99. Basic lands stay in: they are legal 1-ofs the validator knows.
    """
    commander_keys = {_norm(api.card_name_for_api(name)) for name in commanders}
    return [
        (card.qty, card.name)
        for card in cards
        if _norm(api.card_name_for_api(card.name)) not in commander_keys
    ]


# --------------------------------------------------------------------------
# Card tools (public ManaPool API + Scryfall)
# --------------------------------------------------------------------------


@mcp.tool()
async def search_cards(query: str, limit: int = 15) -> CardSearchResult:
    """Search cards and price them against ManaPool stock.

    Args:
        query: Scryfall search syntax, e.g. `!"Sol Ring"`, `t:elf ci:bg`,
            `oracle:"draw a card" o:commander`, `set:dsk rarity:mythic`.
        limit: Maximum cards to return (1-50).

    ManaPool's public API has no name search, so the search runs on Scryfall
    and each result is then priced from ManaPool's /products/singles.
    """
    limit = max(1, min(int(limit), 50))
    fetcher = await _get_fetcher()
    cards = await scryfall_search(fetcher, query, limit=limit)
    if not cards:
        return CardSearchResult(query=query, total_cards=0)

    products = await products_by_scryfall_ids(
        fetcher, [card["id"] for card in cards if card.get("id")]
    )
    index = _product_index(products)
    priced: list[CardPrice] = []
    missing: list[str] = []
    for card in cards:
        record = index.get(card.get("id", ""))
        if record is None:
            missing.append(card.get("name", ""))
        priced.append(_card_price(card, record))
    return CardSearchResult(
        query=query,
        total_cards=len(cards),
        cards=priced,
        missing_from_manapool=[name for name in missing if name],
    )


@mcp.tool()
async def get_card(card: str) -> CardDetails:
    """Get one card's ManaPool prices and per-listing availability.

    Args:
        card: A card name, a Scryfall id, or a ManaPool card URL such as
            https://manapool.com/card/sld/2683/sol-ring.
    """
    fetcher = await _get_fetcher()
    resolved = await _resolve_card(fetcher, card)
    if resolved is None:
        raise ToolError(f"Could not resolve card: {card!r}")
    scryfall_id = resolved.get("id")
    products = await products_by_scryfall_ids(fetcher, [scryfall_id] if scryfall_id else [])
    record = _product_index(products).get(scryfall_id or "")

    listings: list[Listing] = []
    for variant in (record or {}).get("variants", []) or []:
        if not isinstance(variant, dict):
            continue
        listings.append(
            Listing(
                condition=variant.get("condition_id"),
                finish=variant.get("finish_id"),
                language=variant.get("language_id"),
                price_usd=cents_to_usd(variant.get("low_price")),
                quantity=int(variant.get("available_quantity") or 0),
            )
        )
    listings.sort(key=lambda item: (item.price_usd is None, item.price_usd or 0))

    return CardDetails(
        name=resolved.get("name", card),
        set_code=(record or {}).get("set_code") or resolved.get("set"),
        set_name=resolved.get("set_name"),
        scryfall_id=scryfall_id,
        type_line=resolved.get("type_line"),
        oracle_text=resolved.get("oracle_text"),
        price_usd=cents_to_usd((record or {}).get("price_cents")),
        nm_usd=cents_to_usd((record or {}).get("price_cents_nm")),
        foil_usd=cents_to_usd((record or {}).get("price_cents_foil")),
        market_usd=cents_to_usd((record or {}).get("price_market")),
        stock=int((record or {}).get("available_quantity") or 0),
        url=(record or {}).get("url"),
        listings=listings,
    )


async def _resolve_card(fetcher: Fetcher, value: str):
    value = value.strip()
    match = re.search(r"/card/([^/]+)/([^/?#]+)", value)
    if match:
        return await card_by_set_number(fetcher, match.group(1), match.group(2))
    if re.fullmatch(r"[0-9a-fA-F-]{36}", value):
        return await card_by_id(fetcher, value)
    resolved = await cards_by_name(fetcher, [value])
    return resolved.get(_norm(value))


def _product_index(products: list[dict]) -> dict[str, dict]:
    index: dict[str, dict] = {}
    for record in products:
        if not isinstance(record, dict):
            continue
        for key in [record.get("scryfall_id"), *(record.get("scryfall_ids") or [])]:
            if key:
                index[key] = record
    return index


def _card_price(card: dict, record: dict | None) -> CardPrice:
    record = record or {}
    return CardPrice(
        name=card.get("name", ""),
        set_code=record.get("set_code") or card.get("set"),
        set_name=card.get("set_name"),
        scryfall_id=card.get("id"),
        type_line=card.get("type_line"),
        mana_value=card.get("cmc"),
        price_usd=cents_to_usd(record.get("price_cents")),
        nm_usd=cents_to_usd(record.get("price_cents_nm")),
        foil_usd=cents_to_usd(record.get("price_cents_foil")),
        market_usd=cents_to_usd(record.get("price_market")),
        stock=int(record.get("available_quantity") or 0),
        url=record.get("url"),
    )


# --------------------------------------------------------------------------
# pricing
# --------------------------------------------------------------------------


@mcp.tool()
async def price_deck(decklist: str, commander: str | None = None) -> DeckPricing:
    """Price a decklist against ManaPool's live singles inventory.

    Accepts any text list ("1 Card Name", "4x Card Name", "Card Name") and
    prices each line at the cheapest in-stock printing. Use this to budget a
    custom build; ManaPool's own pre-built decks are always a single seller's
    stock, so their cart total and this estimate differ.

    Args:
        decklist: Newline-separated list. Blank lines and "//" comments ignored.
        commander: Optional commander, priced and reported separately.
    """
    book = await load_price_book(await _get_fetcher())
    wanted: list[tuple[int, str]] = []
    for raw in decklist.splitlines():
        line = raw.split("//")[0].strip()
        if not line:
            continue
        match = re.match(r"^(\d+)\s*[xX]?\s+(.*)$", line)
        if match:
            wanted.append((int(match.group(1)), match.group(2).strip()))
        else:
            wanted.append((1, line))
    if not wanted:
        raise ToolError("decklist was empty")

    lines: list[DeckPriceLine] = []
    for qty, name in wanted:
        printing = book.cheapest(name, min_quantity=1)
        if printing is None:
            lines.append(DeckPriceLine(name=name, qty=qty, unit_price=None, line_total=None))
            continue
        lines.append(
            DeckPriceLine(
                name=name,
                qty=qty,
                unit_price=printing.unit_price,
                line_total=round(printing.unit_price * qty, 2),
                set_code=printing.set_code,
                number=printing.number,
                available=printing.available,
                short=printing.available < qty,
            )
        )

    priced = [line for line in lines if line.line_total is not None]
    missing = [line.name for line in lines if line.unit_price is None]
    short = [line.name for line in lines if line.short]
    total_cards = sum(line.qty for line in lines)
    return DeckPricing(
        as_of=book.as_of,
        total_cards=total_cards,
        total_price=round(sum(line.line_total or 0 for line in priced), 2),
        commander=commander,
        commander_price=(
            book.cheapest(commander).unit_price if commander and book.cheapest(commander) else None
        ),
        lines=lines,
        missing=missing,
        short=short,
        note=(
            "Cheapest in-stock printing for each line, not a single-seller cart. "
            "Add shipping and expect +/-10% drift as stock moves."
        ),
    )


# --------------------------------------------------------------------------
# Keyed tools (ManaPool API credentials required)
# --------------------------------------------------------------------------

DECKLIST_NOTE = (
    "Legality only. This is ManaPool's Commander construction check (colour "
    "identity, singletons, partner rules); it says nothing about bracket or "
    "power level - use audit_deck for that."
)


def _parse_decklist(decklist: str) -> list[tuple[int, str]]:
    """(quantity, name) from any common list format, comments and blanks dropped."""
    lines: list[tuple[int, str]] = []
    for raw in decklist.splitlines():
        stripped = raw.strip()
        # Whole-line comments only: "//" also separates split cards
        # ("1 Fire // Ice"), so it cannot be stripped as an inline trailer.
        if not stripped or stripped.startswith("//"):
            continue
        match = re.match(r"^(\d+)\s*[xX]?\s+(.*)$", stripped)
        qty, name = (int(match.group(1)), match.group(2).strip()) if match else (1, stripped)
        # Drop MTGO/Arena section pragmas rather than sending them as cards.
        if name.lower() in {"sideboard", "deck", "commander", "companion", "maybeboard"}:
            continue
        if name:
            lines.append((max(1, qty), name))
    return lines


@mcp.tool()
async def validate_decklist(decklist: str, commander_names: str) -> DeckValidation:
    """Check a decklist against ManaPool's Commander rules (needs API credentials).

    Answers "is this a legal Commander deck": card legality, singleton and
    quantity limits, colour identity against the commander, and partner rules.
    It does not check bracket; use audit_deck for that.

    Args:
        decklist: Newline-separated list. Blank lines and whole-line "//"
            comments are ignored; "//" between two card names is kept, because
            it separates split cards ("Fire // Ice").
        commander_names: One per line. Commander names contain commas
            ("Atraxa, Praetors' Voice"), so commas are not treated as
            separators; put partners and backgrounds on their own lines.
    """
    lines = _parse_decklist(decklist)
    if not lines:
        raise ToolError("decklist was empty")

    names = _commander_names(commander_names)
    if not names:
        raise ToolError(
            "commander_names is required for validation: colour identity is checked "
            "against the commander, so the check cannot run without one."
        )
    lowered = {_norm(api.card_name_for_api(name)) for name in names}
    other = [
        (qty, name) for qty, name in lines if _norm(api.card_name_for_api(name)) not in lowered
    ]
    _require_credentials()

    try:
        result = await api.validate_deck(await _get_fetcher(), names, other)
    except AuthError as exc:
        raise ToolError(str(exc)) from exc
    except UpstreamError as exc:
        raise ToolError(str(exc)) from exc

    if result.note:
        result.note = f"{result.note} {DECKLIST_NOTE}"
    else:
        result.note = DECKLIST_NOTE
    return result


@mcp.tool()
async def optimize_cart(
    decklist: str,
    condition: str | None = None,
    finish: str | None = None,
    language: str = "EN",
) -> CartQuote:
    """Quote a decklist as one ManaPool cart, including shipping and fees.

    Needs API credentials. Unlike price_deck, which sums the cheapest printing
    per line from the public price snapshot, this asks ManaPool's cart optimizer
    to pick actual in-stock copies, so it reflects availability, seller count,
    shipping and buyer fees.

    Args:
        decklist: Newline-separated list ("1 Sol Ring", "4x Forest", "Sol Ring").
        condition: Worst acceptable condition: NM, LP, MP, HP or DMG.
        finish: NF (non-foil), FO or EF (etched). Omit for any.
        language: Language code, EN by default. Omit restrictions by passing "".
    """
    lines = _parse_decklist(decklist)
    if not lines:
        raise ToolError("decklist was empty")
    conditions = _one_of(condition, {"NM", "LP", "MP", "HP", "DMG"}, "condition")
    finishes = _one_of(finish, {"NF", "FO", "EF"}, "finish")
    languages = [language.upper()] if language else None
    _require_credentials()

    try:
        return await api.optimize_cart(
            await _get_fetcher(),
            lines,
            conditions=conditions,
            finishes=finishes,
            languages=languages,
        )
    except AuthError as exc:
        raise ToolError(str(exc)) from exc
    except UpstreamError as exc:
        raise ToolError(str(exc)) from exc


def _commander_names(value: str | None) -> list[str]:
    """Commander names from a newline-separated string.

    Deliberately not comma-split: Commander names contain commas ("Atraxa,
    Praetors' Voice"), so a comma is data, not a separator. Partners and
    backgrounds go on their own line.
    """
    if not value:
        return []
    return [line.strip() for line in value.splitlines() if line.strip()]


def _one_of(value: str | None, allowed: set[str], label: str) -> list[str] | None:
    if not value:
        return None
    cleaned = value.strip().upper()
    if cleaned not in allowed:
        raise ToolError(f"{label} must be one of {sorted(allowed)}, got {value!r}")
    return [cleaned]


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
    # Fail fast on a half-configured credential pair rather than letting the
    # keyed tools fail one call at a time.
    problem = credential_problem()
    if problem:
        raise SystemExit(problem)
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
