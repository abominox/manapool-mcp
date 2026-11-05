# manapool-mcp

An MCP server for [ManaPool](https://manapool.com/decks)'s pre-built Commander
decks, with a bracket auditor that checks the list instead of trusting the tag.

[![CI](https://github.com/abominox/manapool-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/abominox/manapool-mcp/actions/workflows/ci.yml)
[![Release](https://github.com/abominox/manapool-mcp/actions/workflows/release.yml/badge.svg)](https://github.com/abominox/manapool-mcp/actions/workflows/release.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](pyproject.toml)

## Why

ManaPool labels every deck with a Commander bracket. Those labels are generated
and optimistic. In a survey of 704 listings priced $60–80, only 7% contained a
single Game Changer, yet nearly all were tagged "Bracket 3". A deck can also be
tagged 3 and genuinely play like Core.

This server exposes the listings as tools and, more importantly, audits the card
list against the official criteria: Game Changers, mass land denial, extra
turns, and Commander Spellbook combos.

## Tools

| Tool | What it does |
| --- | --- |
| `search_decks` | Search or browse listings by commander, bracket, theme, colours and price window. |
| `get_deck` | One deck: commander(s), seller, price, section counts, every card with quantity and price. Optional Scryfall type lines. |
| `audit_deck` | Official bracket checks plus the lowest bracket the list mechanically permits, with reasons. |

Resources: `manapool://rules/game-changers` and `manapool://rules/brackets`.

`audit_deck` reports what the cards permit, not what the deck is. A zero-Game-
Changer deck can still be Upgraded by intent, and an accidental two-card combo
will flag higher than its pilot means. Brackets are a pre-game conversation
tool.

## Install

### Local

```sh
git clone https://github.com/abominox/manapool-mcp.git
cd manapool-mcp
uv venv --python 3.12 .venv
. .venv/bin/activate
uv pip install -e ".[dev]"
```

Register with OpenCode:

```sh
opencode mcp add manapool --global -- \
  "$PWD/.venv/bin/manapool-mcp"
```

### Docker

```sh
docker build -t manapool-mcp:latest .
docker run -i --rm manapool-mcp:latest     # stdio, for OpenCode
```

`-i` is required for stdio; do not add `-t`. Register the container:

```sh
opencode mcp add manapool --global -- \
  docker run -i --rm manapool-mcp:latest
```

Rebuild the image after code changes — `docker run` never re-pulls `:latest`,
and OpenCode keeps the MCP process open, so `/reload` or a new session is
needed to pick up a rebuild.

If Docker is not running when OpenCode starts, the server shows as
disconnected for that session. Nothing is left behind.

### HTTP

```sh
docker run --rm -p 127.0.0.1:8765:8765 \
  -e MANAPOOL_TRANSPORT=streamable-http \
  -e MANAPOOL_HOST=0.0.0.0 \
  manapool-mcp:latest
```

Then point OpenCode at `http://127.0.0.1:8765/mcp`. Bind to `0.0.0.0` inside
the container (the code default is loopback, which in a container means only the
container itself) but publish on loopback only: HTTP mode has no authentication
and the SDK's DNS-rebinding guard only applies on loopback.

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `MANAPOOL_TRANSPORT` | `stdio` | `stdio` or `streamable-http` |
| `MANAPOOL_HOST` | `127.0.0.1` | HTTP bind address |
| `MANAPOOL_PORT` | `8765` | HTTP port |
| `MANAPOOL_CACHE_TTL` | `600` | Seconds to cache fetched pages |
| `MANAPOOL_CACHE_BYTES` | `32000000` | Approximate byte cap for the page cache |
| `MANAPOOL_TIMEOUT` | `20` | Per-request timeout, seconds |
| `MANAPOOL_MIN_INTERVAL` | `0.2` | Minimum seconds between outbound request starts |
| `MANAPOOL_USER_AGENT` | `manapool-mcp/0.1 (personal deck research)` | Outbound User-Agent |

### Slow calls need a longer timeout

A price window spanning the whole catalogue takes 20–40 seconds. OpenCode's
default tool timeout is 30 seconds, so after `opencode mcp add`, add the timeout
by hand:

```jsonc
{
  "mcp": {
    "servers": {
      "manapool": {
        "type": "local",
        "command": ["docker", "run", "-i", "--rm", "manapool-mcp:latest"],
        "timeout": { "execution": 120000 }
      }
    }
  }
}
```

Searches that include a `commander` finish in a second or two.

## How the price search works

ManaPool has no price filter — only bracket, sort, page, theme, colours and a
text query. The server walks the price-sorted listing and filters locally.

That sort is only *mostly* ascending. Rebuilt decks are appended to the tail at
their new price, so the last pages can hold the cheapest listings and no
boundary can be trusted. The design follows from that:

- If the result set fits a 20-page budget, **every page is fetched** and
  `truncated` is `false`.
- Otherwise the server probes for a start page, samples across the range, pins
  the final page (where the out-of-order listings live), and always returns
  `truncated: true`.

Wide windows are samples, not complete sets. Narrow with `commander` or `theme`.

Prices come in two flavours: the browse tile includes the seller's basic lands;
the deck page's `Price at <seller>` excludes them. `search_decks` returns the
former, `get_deck` the latter.

## Design notes

- **Parsing fails loudly.** `parse_deck_page` raises `DeckParseError` when the
  card rows stop summing to the header's count. A partial list would otherwise
  produce a confidently wrong audit.
- **No API, no contract.** This reads the same HTML a browser gets. Fixtures in
  `tests/fixtures/` are captured pages; when the site changes, add a fixture and
  a test rather than loosening a regex.
- **Bracket data is dated.** `rules.py` documents its sources and the date the
  53-card Game Changers list was current (February 2026).
- **Polite by default.** Requests are throttled, retried with backoff, and
  cached in memory with both a TTL and a byte cap.

## Development

```sh
make install   # venv + editable install with dev extras
make check     # ruff + pytest
make fmt       # format and autofix
make docker    # build the image
make smoke     # drive the server over real MCP stdio (hits the network)
```

```
src/manapool_mcp/
  client.py     HTTP, throttling, retries, cache
  parse.py      listing and deck-page HTML parsing
  rules.py      Game Changers / land denial / extra turns, bracket logic
  spellbook.py  Commander Spellbook combo lookup
  server.py     MCP tools and resources
  models.py     typed tool results
tests/          61 tests, offline fixtures, fake listing fetcher
```

CI runs lint, tests on Python 3.11–3.13, a pinned-install job against
`constraints.txt`, and a Docker handshake smoke test with no network.

## Limitations

- ManaPool can change its markup at any time.
- Decks are rebuilt when a seller's stock changes, so a saved list can differ
  from what you read a moment ago. Re-read before buying.
- The combo check sends card names to `backend.commanderspellbook.com`; Scryfall
  type lookups go to `api.scryfall.com`. Nothing else leaves your machine.
- Not affiliated with ManaPool or Wizards of the Coast. Fan content.

## License

MIT — see [LICENSE](LICENSE).
