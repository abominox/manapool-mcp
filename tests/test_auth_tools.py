"""Tests for the credential-gated tools and the authoritative audit block."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from manapool_mcp import api, server
from manapool_mcp.client import AuthError, UpstreamError
from manapool_mcp.models import CardEntry, DeckValidation

FIXTURES = Path(__file__).parent / "fixtures"
VALIDATION_URL = "https://manapool.com/api/v1/deck"


@pytest.fixture
def list_html() -> str:
    return (FIXTURES / "list.html").read_text(encoding="utf-8")


def validation_payload(**overrides) -> dict:
    details = {
        "commander_count": 1,
        "total_card_count": 100,
        "all_cards_legal": True,
        "illegal_cards": [],
        "cards_not_found": [],
        "valid_quantities": True,
        "quantity_violations": [],
        "valid_color_identity": True,
        "color_identity_violations": [],
        "valid_partnership": True,
        "partner_violations": [],
    }
    details.update(overrides.pop("details", {}))
    payload = {"valid": True, "details": details, "buy_url": "https://manapool.com/add-deck?x"}
    payload.update(overrides)
    return payload


class FakeFetcher:
    """Answers the keyed endpoints and records what was posted."""

    def __init__(self, *, validation=None, cart=None, listing=None, error=None) -> None:
        self.validation = validation if validation is not None else validation_payload()
        self.cart = cart or {
            "cart": [{"inventory_id": "inv-1", "quantity_selected": 2}],
            "totals": {
                "subtotal_cents": 1000,
                "shipping_cents": 500,
                "buyer_fee_cents": 10,
                "gathered_fee_cents": 0,
                "total_cents": 1510,
                "seller_count": 1,
            },
        }
        self.listing = listing or {
            "inventory_items": [
                {
                    "id": "inv-1",
                    "price_cents": 500,
                    "single": {"name": "Sol Ring", "set": "LTC", "number": "268"},
                }
            ]
        }
        self.error = error
        self.posts: list[tuple[str, dict]] = []
        self.gets: list[str] = []

    async def get_text(self, url: str, *, use_cache: bool = True) -> str:
        self.gets.append(url)
        return (FIXTURES / "list.html").read_text(encoding="utf-8")

    async def get_json(self, url: str):
        self.gets.append(url)
        return self.listing

    async def post_json_keyed(self, url: str, payload: dict):
        self.posts.append((url, payload))
        if self.error:
            raise self.error
        return self.validation if url == VALIDATION_URL else self.cart


def _configure(monkeypatch, *, email="player@example.com", token="mpat_x") -> None:
    monkeypatch.setenv("MANAPOOL_EMAIL", email)
    monkeypatch.setenv("MANAPOOL_ACCESS_TOKEN", token)


def _unconfigure(monkeypatch) -> None:
    monkeypatch.delenv("MANAPOOL_EMAIL", raising=False)
    monkeypatch.delenv("MANAPOOL_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("MANAPOOL_API_KEY", raising=False)


# --------------------------------------------------------------------------
# gating
# --------------------------------------------------------------------------


async def test_validate_decklist_without_credentials_explains_the_variables(
    monkeypatch,
) -> None:
    _unconfigure(monkeypatch)
    with pytest.raises(ToolError) as caught:
        await server.validate_decklist("1 Sol Ring", "Atraxa, Praetors' Voice")
    message = str(caught.value)
    assert "MANAPOOL_EMAIL" in message and "MANAPOOL_ACCESS_TOKEN" in message


async def test_half_configured_credentials_name_the_missing_variable(monkeypatch) -> None:
    _unconfigure(monkeypatch)
    monkeypatch.setenv("MANAPOOL_EMAIL", "player@example.com")
    with pytest.raises(ToolError) as caught:
        await server.validate_decklist("1 Sol Ring", "Atraxa, Praetors' Voice")
    assert "MANAPOOL_ACCESS_TOKEN" in str(caught.value)


async def test_optimize_cart_without_credentials_is_gated(monkeypatch) -> None:
    _unconfigure(monkeypatch)
    with pytest.raises(ToolError):
        await server.optimize_cart("1 Sol Ring")


async def test_credentials_set_after_the_fetcher_was_built_are_used(monkeypatch) -> None:
    """The fetcher is a per-process singleton; env changes must still apply."""

    import httpx

    from manapool_mcp.client import Fetcher

    _unconfigure(monkeypatch)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"valid": True, "details": {}})

    fetcher = Fetcher(transport=httpx.MockTransport(handler), min_interval=0)
    monkeypatch.setattr(server, "_fetcher", fetcher)

    _configure(monkeypatch)
    await server.validate_decklist("1 Sol Ring", "Atraxa, Praetors' Voice")

    assert seen[0].headers["X-ManaPool-Access-Token"] == "mpat_x"


async def test_argument_validation_precedes_the_credential_gate(monkeypatch) -> None:
    _unconfigure(monkeypatch)
    with pytest.raises(ToolError) as caught:
        await server.optimize_cart("1 Sol Ring", condition="mint")
    assert "condition must be one of" in str(caught.value)


# --------------------------------------------------------------------------
# validate_decklist
# --------------------------------------------------------------------------


async def test_validate_decklist_sends_commanders_and_cards_separately(
    monkeypatch,
) -> None:
    _configure(monkeypatch)
    fetcher = FakeFetcher()
    monkeypatch.setattr(server, "_fetcher", fetcher)

    result = await server.validate_decklist(
        "1 Sol Ring\n2 Arcane Signet\n// a comment\n", "Atraxa, Praetors' Voice"
    )

    assert result.valid is True
    url, payload = fetcher.posts[0]
    assert url == VALIDATION_URL
    assert payload["commander_names"] == ["Atraxa, Praetors' Voice"]
    assert {"name": "Sol Ring", "quantity": 1} in payload["other_cards"]
    assert {"name": "Arcane Signet", "quantity": 2} in payload["other_cards"]
    assert all("a comment" not in card["name"] for card in payload["other_cards"])


async def test_validate_decklist_strips_manapool_alias_names(monkeypatch) -> None:
    _configure(monkeypatch)
    fetcher = FakeFetcher()
    monkeypatch.setattr(server, "_fetcher", fetcher)

    await server.validate_decklist(
        "1 Memories of Nibelheim (Stroke of Midnight)\n1 Fire // Ice", "Nelly Borca"
    )

    names = [
        card["name"]
        for _, payload in [(None, fetcher.posts[0][1])]
        for card in payload["other_cards"]
    ]
    assert "Memories of Nibelheim" in names
    assert "Fire // Ice" in names, "split cards keep their separator"


async def test_validate_decklist_flags_unresolved_names_as_incomplete(monkeypatch) -> None:
    _configure(monkeypatch)
    fetcher = FakeFetcher(
        validation=validation_payload(details={"cards_not_found": ["Notacard"]}, valid=False)
    )
    monkeypatch.setattr(server, "_fetcher", fetcher)

    result = await server.validate_decklist("1 Notacard", "Atraxa, Praetors' Voice")
    assert result.valid is False
    assert result.cards_not_found == ["Notacard"]
    assert "incomplete" in (result.note or "")


async def test_validate_decklist_reports_construction_violations(monkeypatch) -> None:
    _configure(monkeypatch)
    fetcher = FakeFetcher(
        validation=validation_payload(
            valid=False,
            details={
                "illegal_cards": ["Black Lotus"],
                "valid_color_identity": False,
                "color_identity_violations": [
                    {"name": "Lightning Bolt", "card_colors": ["R"], "commander_colors": ["G"]}
                ],
                "valid_quantities": False,
                "quantity_violations": [{"name": "Sol Ring", "quantity": 3, "max_allowed": 1}],
            },
        )
    )
    monkeypatch.setattr(server, "_fetcher", fetcher)

    result = await server.validate_decklist("3 Sol Ring", "Atraxa, Praetors' Voice")
    assert result.valid is False
    assert result.illegal_cards == ["Black Lotus"]
    assert result.color_identity_violations[0].card_colors == ["R"]
    assert result.quantity_violations[0].max_allowed == 1


async def test_validate_decklist_requires_a_commander(monkeypatch) -> None:
    _configure(monkeypatch)
    monkeypatch.setattr(server, "_fetcher", FakeFetcher())
    with pytest.raises(ToolError) as caught:
        await server.validate_decklist("1 Sol Ring", "")
    assert "commander_names is required" in str(caught.value)


async def test_validate_decklist_surfaces_auth_failure_as_tool_error(monkeypatch) -> None:
    _configure(monkeypatch)
    monkeypatch.setattr(
        server, "_fetcher", FakeFetcher(error=AuthError("ManaPool rejected the credentials"))
    )
    with pytest.raises(ToolError) as caught:
        await server.validate_decklist("1 Sol Ring", "Atraxa, Praetors' Voice")
    assert "rejected" in str(caught.value)


# --------------------------------------------------------------------------
# audit_deck integration
# --------------------------------------------------------------------------


async def test_audit_deck_is_unchanged_without_credentials(monkeypatch, list_html) -> None:
    _unconfigure(monkeypatch)
    fetcher = FakeFetcher()
    monkeypatch.setattr(server, "_fetcher", fetcher)
    monkeypatch.setattr(server, "find_combos", _no_combos)

    audit = await server.audit_deck(str(uuid.uuid4()))
    assert audit.authoritative is None
    assert not any("validation" in note.lower() for note in audit.notes)
    assert not fetcher.posts, "no keyed call should happen anonymously"


async def test_audit_deck_adds_authoritative_block_when_configured(monkeypatch) -> None:
    _configure(monkeypatch)
    fetcher = FakeFetcher(
        validation=validation_payload(details={"illegal_cards": ["X"]}, valid=False)
    )
    monkeypatch.setattr(server, "_fetcher", fetcher)
    monkeypatch.setattr(server, "find_combos", _no_combos)

    audit = await server.audit_deck(str(uuid.uuid4()))

    assert isinstance(audit.authoritative, DeckValidation)
    assert audit.authoritative.valid is False
    assert audit.authoritative.illegal_cards == ["X"]
    # Bracket verdict is still local and still present.
    assert audit.mechanical_minimum_bracket in (2, 3, 4)


async def test_audit_deck_sends_the_99_and_excludes_commanders(monkeypatch) -> None:
    _configure(monkeypatch)
    fetcher = FakeFetcher()
    monkeypatch.setattr(server, "_fetcher", fetcher)
    monkeypatch.setattr(server, "find_combos", _no_combos)

    await server.audit_deck(str(uuid.uuid4()))

    _, payload = fetcher.posts[0]
    assert payload["commander_names"] == ["Nelly Borca, Impulsive Accuser"]
    sent = {card["name"] for card in payload["other_cards"]}
    assert "Nelly Borca, Impulsive Accuser" not in sent
    assert sum(card["quantity"] for card in payload["other_cards"]) == 99
    assert "Mountain" in sent, "basic lands are legal 1-ofs the validator knows"


async def test_audit_deck_degrades_when_validation_fails(monkeypatch) -> None:
    _configure(monkeypatch)
    fetcher = FakeFetcher(error=UpstreamError("boom"))
    monkeypatch.setattr(server, "_fetcher", fetcher)
    monkeypatch.setattr(server, "find_combos", _no_combos)

    audit = await server.audit_deck(str(uuid.uuid4()))
    assert audit.authoritative is None
    assert any("validation unavailable" in note.lower() for note in audit.notes)


async def test_audit_deck_degrades_when_credentials_are_rejected(monkeypatch) -> None:
    _configure(monkeypatch)
    fetcher = FakeFetcher(error=AuthError("ManaPool rejected the configured credentials"))
    monkeypatch.setattr(server, "_fetcher", fetcher)
    monkeypatch.setattr(server, "find_combos", _no_combos)

    audit = await server.audit_deck(str(uuid.uuid4()))
    assert audit.authoritative is None
    assert any("credentials" in note.lower() for note in audit.notes)


async def _no_combos(*args, **kwargs):
    return []


# --------------------------------------------------------------------------
# optimize_cart
# --------------------------------------------------------------------------


async def test_optimize_cart_quotes_totals_and_matches_listings(monkeypatch) -> None:
    _configure(monkeypatch)
    fetcher = FakeFetcher()
    monkeypatch.setattr(server, "_fetcher", fetcher)

    quote = await server.optimize_cart("2 Sol Ring", condition="nm", finish="nf")

    assert quote.total_price == 15.10
    assert quote.totals is not None and quote.totals.shipping_usd == 5.0
    assert quote.totals.buyer_fee_usd == 0.1
    assert quote.total_cards == 2
    line = quote.lines[0]
    assert line.name == "Sol Ring"
    assert line.unit_price == 5.0 and line.line_total == 10.0
    assert line.set_code == "LTC" and line.number == "268"

    _, payload = fetcher.posts[0]
    item = payload["cart"][0]
    assert item["quantity_requested"] == 2
    assert item["condition_ids"] == ["NM"]
    assert item["finish_ids"] == ["NF"]
    assert item["language_ids"] == ["EN"]


async def test_optimize_cart_omits_filters_instead_of_sending_null(monkeypatch) -> None:
    """The API types these as arrays; JSON null would be rejected."""

    _configure(monkeypatch)
    fetcher = FakeFetcher()
    monkeypatch.setattr(server, "_fetcher", fetcher)

    await server.optimize_cart("1 Sol Ring", language="")

    _, payload = fetcher.posts[0]
    item = payload["cart"][0]
    for key in ("condition_ids", "finish_ids", "language_ids"):
        assert key not in item, f"{key} must be omitted, not null"


async def test_optimize_cart_reports_unallocated_cards(monkeypatch) -> None:
    _configure(monkeypatch)
    fetcher = FakeFetcher(
        cart={"cart": [], "totals": {"total_cents": 0}},
        listing={"inventory_items": []},
    )
    monkeypatch.setattr(server, "_fetcher", fetcher)

    quote = await server.optimize_cart("1 Sol Ring")
    assert quote.missing == ["Sol Ring"]


async def test_optimize_cart_does_not_double_count_a_repeated_name(monkeypatch) -> None:
    """A card listed on two lines must not bill the same allocation twice."""

    _configure(monkeypatch)
    fetcher = FakeFetcher(
        cart={
            "cart": [{"inventory_id": "inv-1", "quantity_selected": 2}],
            "totals": {"total_cents": 1000},
        }
    )
    monkeypatch.setattr(server, "_fetcher", fetcher)

    quote = await server.optimize_cart("1 Sol Ring\n1 Sol Ring")

    assert sum(line.qty for line in quote.lines) == quote.total_cards == 2
    billed = sum(line.line_total or 0.0 for line in quote.lines)
    assert billed <= quote.total_price


async def test_optimize_cart_partial_listing_detail_does_not_fake_a_stock_gap(
    monkeypatch,
) -> None:
    """An allocated-but-unpriced item is a pricing gap, not a missing card."""

    _configure(monkeypatch)
    fetcher = FakeFetcher(
        cart={
            "cart": [
                {"inventory_id": "inv-1", "quantity_selected": 1},
                {"inventory_id": "inv-2", "quantity_selected": 1},
            ],
            "totals": {"total_cents": 5000},
        },
        # inv-2 is deliberately absent from the listing response.
        listing={
            "inventory_items": [{"id": "inv-1", "price_cents": 500, "single": {"name": "Sol Ring"}}]
        },
    )
    monkeypatch.setattr(server, "_fetcher", fetcher)

    quote = await server.optimize_cart("1 Sol Ring\n1 Black Lotus")
    assert "Black Lotus" not in quote.missing
    assert "could not be priced" in (quote.note or "")
    assert quote.total_cards == 2


async def test_optimize_cart_with_zero_total_does_not_report_a_silent_zero(
    monkeypatch,
) -> None:
    """totals present but total_cents missing must not become $0.00."""

    _configure(monkeypatch)
    fetcher = FakeFetcher(
        cart={
            "cart": [{"inventory_id": "inv-1", "quantity_selected": 1}],
            "totals": {"subtotal_cents": 500, "shipping_cents": 499},
        },
    )
    monkeypatch.setattr(server, "_fetcher", fetcher)

    quote = await server.optimize_cart("1 Sol Ring")
    assert quote.total_price == 5.0, "must fall back to the resolved line"
    assert "no usable total" in (quote.note or "")


async def test_one_unpriced_allocation_does_not_hide_a_missing_card(
    monkeypatch,
) -> None:
    """A pricing gap on one card must not suppress a real stock gap on another."""

    _configure(monkeypatch)
    fetcher = FakeFetcher(
        cart={
            # One unit comes back for two requested cards and it cannot be
            # identified: a real shortfall plus a pricing gap. The identifiable
            # card must not be blamed for both.
            "cart": [{"inventory_id": "inv-9", "quantity_selected": 1}],
            "totals": {"total_cents": 1500},
        },
        listing={"inventory_items": []},
    )
    monkeypatch.setattr(server, "_fetcher", fetcher)

    quote = await server.optimize_cart("1 Sol Ring\n1 Black Lotus")
    assert quote.missing == ["Black Lotus"], "genuinely unallocated card still reported"


async def test_optimize_cart_without_totals_does_not_report_a_silent_zero(
    monkeypatch,
) -> None:
    _configure(monkeypatch)
    fetcher = FakeFetcher(
        cart={"cart": [{"inventory_id": "inv-1", "quantity_selected": 1}]},
    )
    monkeypatch.setattr(server, "_fetcher", fetcher)

    quote = await server.optimize_cart("1 Sol Ring")
    assert quote.totals is None
    assert quote.total_price == 5.0, "falls back to the resolved lines"
    assert "no usable total" in (quote.note or "")


async def test_optimize_cart_rejects_unknown_condition(monkeypatch) -> None:
    _configure(monkeypatch)
    monkeypatch.setattr(server, "_fetcher", FakeFetcher())
    with pytest.raises(ToolError):
        await server.optimize_cart("1 Sol Ring", condition="mint")


# --------------------------------------------------------------------------
# helpers / payload shape
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Memories of Nibelheim (Stroke of Midnight)", "Memories of Nibelheim"),
        ("Fire // Ice", "Fire // Ice"),
        ("  Sol Ring  ", "Sol Ring"),
        ("Teferi's Protection", "Teferi's Protection"),
    ],
)
def test_card_name_for_api(raw: str, expected: str) -> None:
    assert api.card_name_for_api(raw) == expected


def test_validation_lines_drops_the_commander() -> None:
    cards = [
        CardEntry(qty=1, name="Nelly Borca, Impulsive Accuser"),
        CardEntry(qty=1, name="Sol Ring"),
        CardEntry(qty=1, name="Mountain"),
    ]
    lines = server._validation_lines(cards, ["Nelly Borca, Impulsive Accuser"])
    assert lines == [(1, "Sol Ring"), (1, "Mountain")]


def test_validation_lines_drops_an_alias_form_commander() -> None:
    """ManaPool rewrites some names; the alias row is still the commander."""
    cards = [
        CardEntry(qty=1, name="Memories of Nibelheim (Stroke of Midnight)"),
        CardEntry(qty=1, name="Mountain"),
    ]
    lines = server._validation_lines(cards, ["Memories of Nibelheim (Stroke of Midnight)"])
    assert lines == [(1, "Mountain")]


async def test_validate_deck_payload_matches_the_documented_shape(monkeypatch) -> None:
    """Guards against the `/deck` contract drifting (commander_names + other_cards)."""
    fetcher = FakeFetcher()
    await api.validate_deck(fetcher, ["Atraxa, Praetors' Voice"], [(1, "Sol Ring")])
    _, payload = fetcher.posts[0]
    assert set(payload) == {"commander_names", "other_cards"}
    assert set(payload["other_cards"][0]) == {"name", "quantity"}
