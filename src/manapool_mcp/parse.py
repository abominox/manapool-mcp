"""HTML parsing for ManaPool deck listings and deck pages.

ManaPool is a SvelteKit app that server-renders its HTML, so a plain HTTP
fetch is enough. Selectors are deliberately structural (heading text,
quantity/price spans) rather than class-name based, because the utility
classes churn between deploys.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from selectolax.lexbor import LexborHTMLParser as HTMLParser

from .models import CardEntry, DeckDetails, DeckTile


class DeckParseError(ValueError):
    """The deck page did not contain a complete, trustworthy card list."""


LIST_PATH_RE = re.compile(r"^/lists/([0-9a-fA-F-]{36})$")
TOTAL_RE = re.compile(r"([\d,]+)\s+decks")
BRACKET_RE = re.compile(r"Bracket\s+(\d)")
PRICE_RE = re.compile(r"\$\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)")
TOTAL_CARDS_RE = re.compile(r"^(\d+)\s+cards$", re.IGNORECASE)
QTY_RE = re.compile(r"^(\d+)x$")
SECTION_RE = re.compile(
    r"^(Commander|Creatures?|Artifacts?|Enchantments?|Instants?(?:/Sorceries)?|"
    r"Sorceries?|Lands?|Planeswalkers?|Battles?)\s*(?:\((\d+)\))?$",
    re.IGNORECASE,
)
CATEGORY_RE = re.compile(
    r"(\d+)\s+cards\s+((?:(?:Creature|Artifact|Instant|Enchantment|Sorcery|"
    r"Planeswalker|Battle|Land)\s+\d+\s*)+)",
    re.IGNORECASE,
)
CATEGORY_PART_RE = re.compile(
    r"(Creature|Artifact|Instant|Enchantment|Sorcery|"
    r"Planeswalker|Battle|Land)\s+(\d+)",
    re.IGNORECASE,
)


def deck_id_from(value: str) -> str:
    """Accept a bare UUID, a /lists/<id> path, or a full ManaPool URL."""
    value = value.strip()
    if not value:
        raise ValueError("Pass a ManaPool deck URL or list id.")
    if re.fullmatch(r"[0-9a-fA-F-]{36}", value):
        return value.lower()

    parsed = urlparse(value)
    if parsed.netloc and not (
        parsed.netloc == "manapool.com" or parsed.netloc.endswith(".manapool.com")
    ):
        raise ValueError(f"Not a ManaPool URL: {value!r}")
    path = parsed.path if (parsed.scheme or parsed.netloc) else value
    if not path.startswith("/"):
        path = f"/lists/{path}" if re.fullmatch(r"[0-9a-fA-F-]{36}", path) else f"/{path}"
    match = LIST_PATH_RE.match(path)
    if not match:
        raise ValueError(
            f"Not a ManaPool deck reference: {value!r}. "
            "Pass https://manapool.com/lists/<uuid> or the bare uuid."
        )
    return match.group(1).lower()


def parse_total(html: str) -> int | None:
    match = TOTAL_RE.search(_text(html))
    if not match:
        return None
    return int(match.group(1).replace(",", ""))


def parse_deck_tiles(html: str) -> list[DeckTile]:
    doc = HTMLParser(html)
    tiles: list[DeckTile] = []
    seen: set[str] = set()
    for anchor in doc.css('a[href^="/lists/"]'):
        match = LIST_PATH_RE.match(anchor.attributes.get("href", ""))
        if not match:
            continue
        deck_id = match.group(1).lower()
        if deck_id in seen:
            continue
        heading = anchor.css_first("h3")
        if heading is None:
            continue
        name = heading.text(strip=True)
        if not name:
            continue

        theme: str | None = None
        seller: str | None = None
        for paragraph in anchor.css("p"):
            text = paragraph.text(strip=True)
            if not text:
                continue
            if text.lower().startswith("from "):
                seller = text[5:].strip()
            elif theme is None:
                theme = text

        raw = anchor.html or ""
        bracket_match = BRACKET_RE.search(raw)
        price_match = PRICE_RE.search(raw)

        seen.add(deck_id)
        tiles.append(
            DeckTile(
                id=deck_id,
                url=f"https://manapool.com/lists/{deck_id}",
                name=name,
                theme=theme,
                seller=seller,
                bracket=int(bracket_match.group(1)) if bracket_match else None,
                price=_to_float(price_match.group(1)) if price_match else None,
            )
        )
    return tiles


def parse_deck_page(html: str, deck_id: str, url: str | None = None) -> DeckDetails:
    doc = HTMLParser(html)
    page_text = _text(html)

    title_match = re.search(r"<title>(.*?)</title>", html, re.S | re.I)
    title = _clean(title_match.group(1)) if title_match else ""
    head = re.split(r"\s+-\s+Deck by", title)[0].strip()
    title_parts = re.split(r"\s+\u2014\s+", head)
    name = title_parts[0].strip() or deck_id
    theme = title_parts[1].strip() if len(title_parts) > 1 else None

    seller_match = re.search(r"built from\s+(.+?)'s inventory", page_text)
    price_match = re.search(r"Price at\s+(.+?)\s*\$\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)", page_text)
    basics_match = re.search(r"Include\s+(\d+)\s+basic lands", page_text)

    total_cards: int | None = None
    for heading in doc.css("h3"):
        total_match = TOTAL_CARDS_RE.match(heading.text(strip=True))
        if total_match:
            total_cards = int(total_match.group(1))
            break
    if total_cards is None:
        fallback = re.search(r"(\d+)\s+cards", page_text)
        total_cards = int(fallback.group(1)) if fallback else 0

    sections: dict[str, int] = {}
    cards: list[CardEntry] = []
    commanders: list[str] = []

    for heading in doc.css("h3"):
        label = heading.text(strip=True)
        match = SECTION_RE.match(label)
        if not match:
            continue
        section = match.group(1)
        count = int(match.group(2)) if match.group(2) else None
        is_commander_section = section.strip().lower() == "commander"
        if count is not None and not is_commander_section:
            sections[_canonical_section(section)] = count

        container = heading.parent
        if container is None:
            continue
        # Normally the heading sits alone inside a section wrapper. If the
        # wrapper holds several headings, the rows are the next sibling instead.
        if len(container.css("h3")) > 1:
            container = _next_element(heading)
        if container is None:
            continue
        for span in container.css("span"):
            qty_match = QTY_RE.match(span.text(strip=True))
            if not qty_match or span.parent is None:
                continue
            row = span.parent
            button = row.css_first("button")
            if button is None:
                continue
            card_name = button.text(strip=True)
            if not card_name:
                continue
            price = None
            spans = row.css("span")
            if spans:
                last = spans[-1].text(strip=True)
                pm = PRICE_RE.match(last) or PRICE_RE.search(last)
                if pm and last.lstrip().startswith("$"):
                    price = _to_float(pm.group(1))
            if is_commander_section and card_name not in commanders:
                commanders.append(card_name)
            cards.append(CardEntry(qty=int(qty_match.group(1)), name=card_name, price=price))

    if not commanders:
        commanders = [cards[0].name] if cards else []
    if commanders:
        sections["Commander"] = len(commanders)

    parsed_total = sum(card.qty for card in cards)
    if total_cards and parsed_total != total_cards:
        raise DeckParseError(
            f"ManaPool deck page layout changed: parsed {parsed_total} of "
            f"{total_cards} cards. Refusing to audit an incomplete list."
        )

    return DeckDetails(
        id=deck_id,
        url=url or f"https://manapool.com/lists/{deck_id}",
        name=name,
        theme=theme,
        commander=commanders[0] if commanders else None,
        commanders=commanders,
        seller=seller_match.group(1).strip() if seller_match else None,
        price_cards=_to_float(price_match.group(2)) if price_match else None,
        basic_lands=int(basics_match.group(1)) if basics_match else 0,
        total_cards=total_cards or parsed_total,
        sections=sections,
        cards=cards,
    )


def _canonical_section(label: str) -> str:
    label = label.strip().lower()
    mapping = {
        "creature": "Creatures",
        "creatures": "Creatures",
        "artifact": "Artifacts",
        "artifacts": "Artifacts",
        "enchantment": "Enchantments",
        "enchantments": "Enchantments",
        "instant": "Instants",
        "instants": "Instants",
        "instant/sorceries": "Instants/Sorceries",
        "instants/sorceries": "Instants/Sorceries",
        "sorcery": "Sorceries",
        "sorceries": "Sorceries",
        "land": "Lands",
        "lands": "Lands",
        "planeswalker": "Planeswalkers",
        "planeswalkers": "Planeswalkers",
        "battle": "Battles",
        "battles": "Battles",
        "commander": "Commander",
    }
    return mapping.get(label, label.title())


def _text(html: str) -> str:
    doc = HTMLParser(html)
    body = doc.body or doc.root
    return _clean(body.text(separator=" ") if body else "")


def _next_element(node: object):
    """First following element sibling, skipping Svelte's text/comment nodes."""
    candidate = getattr(node, "next", None)
    while candidate is not None and getattr(candidate, "tag", "") not in {
        "div",
        "ul",
        "ol",
        "section",
        "table",
    }:
        candidate = getattr(candidate, "next", None)
    return candidate


def _to_float(value: str) -> float:
    return float(value.replace(",", ""))


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()
