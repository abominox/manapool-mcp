# manapool-mcp

An MCP server for [ManaPool](https://manapool.com)'s Commander decks and card
prices, with a bracket auditor that checks the list instead of trusting the tag.

[![CI](https://github.com/abominox/manapool-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/abominox/manapool-mcp/actions/workflows/ci.yml)
[![Release](https://github.com/abominox/manapool-mcp/actions/workflows/release.yml/badge.svg)](https://github.com/abominox/manapool-mcp/actions/workflows/release.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](pyproject.toml)

## Why

ManaPool labels every deck with a Commander bracket. Those labels are generated
and optimistic. In a survey of 704 listings priced $60–80, only 7% contained a
single Game Changer, yet nearly all were tagged "Bracket 3". A deck can also be
tagged 3 and genuinely play like Core.

This server exposes the decks as tools and audits the card list against the
official criteria: Game Changers, mass land denial, extra turns and Commander
Spellbook combos.

## Tools

| Tool | Source | What it does |
| --- | --- | --- |
| `search_decks` | ManaPool HTML | Browse pre-built decks by commander, bracket, theme, colours and price window. |
| `get_deck` | ManaPool HTML | One deck: commander(s), seller, price, section counts, every card with quantity and price. |
| `audit_deck` | HTML + Spellbook | Bracket checks and the lowest bracket the list mechanically permits, with reasons. |
| `search_cards` | Scryfall + ManaPool API | Search cards with Scryfall syntax, priced against ManaPool stock. |
| `price_deck` | ManaPool API | Price a decklist with quantities; cheapest printing per line, totals, missing and short-stock flags. |
| `get_card` | ManaPool API | One printing: low/NM/foil/market price and every condition/finish listing. |

Resources: `manapool://rules/game-changers` and `manapool://rules/brackets`.

`audit_deck` reports what the cards permit, not what the deck is. A zero-Game-
Changer deck can still be Upgraded by intent, and an accidental two-card combo
will flag higher than its pilot means. Brackets are a pre-game conversation
tool.

## Install

ManaPool's deck pages have no API, so the deck tools parse HTML. The card tools
use ManaPool's public JSON API. Both run in one local process — no Docker, no
service to keep running.

```sh
git clone https://github.com/abominox/manapool-mcp.git
cd manapool-mcp
uv tool install --constraints constraints.txt .
```

That puts `manapool-mcp` in `~/.local/bin` in its own isolated environment.
Register it with OpenCode:

```sh
opencode mcp add manapool --global -- ~/.local/bin/manapool-mcp
```

For development, install editable instead so edits take effect immediately:

```sh
uv tool install --force --constraints constraints.txt -e .
```

Upgrade after a `git pull`:

```sh
uv tool install --force --constraints constraints.txt .
```

### Slow calls need a longer timeout

A deck price window that spans the whole catalogue takes 20–40 seconds, and the
first `price_deck` call downloads a ~53 MB price snapshot. OpenCode's default
tool timeout is 30 seconds, so after `opencode mcp add` add the timeout by hand:

```jsonc
{
  "mcp": {
    "servers": {
      "manapool": {
        "type": "local",
        "command": ["/Users/you/.local/bin/manapool-mcp"],
        "timeout": { "execution": 120000 }
      }
    }
  }
}
```

Searches that include a `commander` finish in a second or two.

## What the ManaPool API exposes

ManaPool publishes an OpenAPI document at
`https://manapool.com/api/docs/v1/openapi.json` (title "Mana Pool Public API",
v0.55.0, 53 paths). Most of it is authenticated. In practice:

| Public, no credentials | Authenticated (401 without a key) |
| --- | --- |
| `GET /prices/singles` — every in-stock printing, ~106k rows, 53 MB | `POST /deck` — deck validator |
| `GET /prices/sealed` — sealed price export | `POST /card_info` — card lookup |
| `GET /prices/variants` — per-listing export, ~203 MB | `POST /buyer/optimizer` — cart optimizer |
| `GET /products/singles` — product records by Scryfall/TCGplayer id | `GET /account`, `/buyer/*`, `/seller/*`, `/inventory/*`, `/webhooks` |

This server uses `products/singles` for exact printings and variants, and
`prices/singles` (disk-cached, daily) for cheapest-across-printings. The bulk
`prices/variants` export is not wrapped: 203 MB per refresh is a batch job, not
a tool call.

There is no public name search in the API, so `search_cards` runs the query on
Scryfall and joins the results to ManaPool products by Scryfall id. Deck
browsing has no API equivalent at all, hence the HTML parser.

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `MANAPOOL_TRANSPORT` | `stdio` | `stdio` or `streamable-http` |
| `MANAPOOL_HOST` | `127.0.0.1` | HTTP bind address |
| `MANAPOOL_PORT` | `8765` | HTTP port |
| `MANAPOOL_CACHE_TTL` | `600` | Seconds to cache fetched pages |
| `MANAPOOL_CACHE_BYTES` | `32000000` | Approximate byte cap for the page cache |
| `MANAPOOL_CACHE_DIR` | `~/.cache/manapool-mcp` | Where the price snapshot is cached |
| `MANAPOOL_PRICES_TTL` | `86400` | Seconds before the price snapshot is refreshed |
| `MANAPOOL_TIMEOUT` | `20` | Per-request timeout, seconds |
| `MANAPOOL_MIN_INTERVAL` | `0.2` | Minimum seconds between outbound request starts |
| `MANAPOOL_USER_AGENT` | `manapool-mcp/0.1 (personal deck research)` | Outbound User-Agent |

HTTP mode is available for a long-running deployment, but it has no
authentication: keep it on loopback.

```sh
MANAPOOL_TRANSPORT=streamable-http MANAPOOL_PORT=8765 manapool-mcp
```

## How the deck price search works

ManaPool's deck browser has no price filter — only bracket, sort, page, theme,
colours and a text query. The server walks the price-sorted listing and filters
locally.

That sort is only *mostly* ascending. Rebuilt decks are appended to the tail at
their new price, so the last pages can hold the cheapest listings and no
boundary can be trusted. The design follows from that:

- If the result set fits a 20-page budget, **every page is fetched** and
  `truncated` is `false`.
- Otherwise the server probes for a start page, samples across the range, pins
  the final page (where the out-of-order listings live), and always returns
  `truncated: true`.

Wide windows are samples, not complete sets. Narrow with `commander` or `theme`.

Deck prices come in two flavours: the browse tile includes the seller's basic
lands; the deck page's `Price at <seller>` excludes them. `search_decks` returns
the former, `get_deck` the latter. `price_deck` is separate: it prices
individual cards from ManaPool's singles stock, not a seller's deck package.

## Design notes

- **Parsing fails loudly.** `parse_deck_page` raises `DeckParseError` when the
  card rows stop summing to the header's count. A partial list would otherwise
  produce a confidently wrong audit.
- **Rate limits are respected.** Requests are throttled, `Retry-After` is
  honoured on 429, and pages are cached in memory with a TTL and a byte cap.
- **Bracket data is dated.** `rules.py` documents its sources and the date the
  53-card Game Changers list was current (February 2026).
- **No API, no contract.** The deck pages are the same HTML a browser gets.
  Fixtures in `tests/fixtures/` are captured pages; when the site changes, add a
  fixture and a test rather than loosening a regex.

## Development

```sh
make install   # venv + editable install with dev extras
make check     # ruff + pytest
make fmt       # format and autofix
make smoke     # drive the server over real MCP stdio (hits the network)
```

```
src/manapool_mcp/
  client.py     HTTP, throttling, retries, cache
  parse.py      deck listing and deck-page HTML parsing
  rules.py      Game Changers / land denial / extra turns, bracket logic
  spellbook.py  Commander Spellbook combo lookup
  scryfall.py   name resolution and card search
  products.py   ManaPool /products/singles
  prices.py     ManaPool /prices/singles, disk-cached
  server.py     MCP tools and resources
  models.py     typed tool results
tests/          69 tests, offline fixtures, fake fetchers
```

CI runs lint, tests on Python 3.11–3.13, and a pinned-install job against
`constraints.txt`.

## Limitations

- ManaPool can change its deck-page markup at any time.
- Decks are rebuilt when a seller's stock changes, so a saved list can differ
  from what you read a moment ago. Re-read before buying.
- `price_deck` uses a daily snapshot, so prices can be up to a day stale.
- Card and combo lookups send card names to `api.scryfall.com` and
  `backend.commanderspellbook.com`. Nothing else leaves your machine.
- Not affiliated with ManaPool or Wizards of the Coast. Fan content.

## License

MIT — see [LICENSE](LICENSE).
