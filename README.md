# manapool-mcp

MCP server for ManaPool's Commander decks and card prices, with a bracket
auditor that checks the list instead of trusting the site's tag.

[![CI](https://github.com/abominox/manapool-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/abominox/manapool-mcp/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-yellow.svg)](LICENSE)

## Tools

| Tool | What it does |
| --- | --- |
| `search_decks` | Browse pre-built decks by commander, bracket, theme, colours and price. |
| `get_deck` | One deck: commander(s), seller, price, sections, every card. |
| `audit_deck` | Game Changers, mass land denial, extra turns, combos → lowest bracket the list permits. |
| `search_cards` | Card search (Scryfall syntax) priced against ManaPool stock. |
| `get_card` | One printing's prices and every condition/finish listing. |
| `price_deck` | Price a decklist: cheapest printing per line, total, missing, short stock. |
| `validate_decklist` | ManaPool's Commander rules check: legality, singletons, colour identity, partners. **Needs API credentials.** |
| `optimize_cart` | Quote a decklist as one cart, including availability, shipping and buyer fees. **Needs API credentials.** |

Resources: `manapool://rules/game-changers`, `manapool://rules/brackets`.

## Install

The default is a local binary; Docker is optional.

```sh
git clone https://github.com/abominox/manapool-mcp.git
cd manapool-mcp
uv tool install --constraints constraints.txt .
opencode mcp add manapool --global -- ~/.local/bin/manapool-mcp
```

Add a longer timeout after `mcp add` — a wide price scan or the first
`price_deck` (53 MB price snapshot) exceeds the 30 s default:

```jsonc
{
  "mcp": {
    "servers": {
      "manapool": {
        "type": "local",
        "command": ["/Users/you/.local/bin/manapool-mcp"],
        "timeout": { "execution": 180000 }
      }
    }
  }
}
```

For development install editable: `uv tool install --force -e .`

### Docker (optional)

```sh
docker build -t manapool-mcp:latest .
docker run -i --rm -v manapool-cache:/cache manapool-mcp:latest
```

`-i` is required for stdio; never add `-t`. The volume keeps the price snapshot
between runs. Published images are at `ghcr.io/abominox/manapool-mcp`.

```sh
opencode mcp add manapool --global -- \
  docker run -i --rm -v manapool-cache:/cache manapool-mcp:latest
```

For HTTP mode, bind the container to `0.0.0.0` and publish on loopback only:
`-e MANAPOOL_TRANSPORT=streamable-http -e MANAPOOL_HOST=0.0.0.0 -p 127.0.0.1:8765:8765`.
It has no authentication.

## Configuration

| Variable | Default | |
| --- | --- | --- |
| `MANAPOOL_EMAIL` | unset | Account e-mail. With the token, unlocks the keyed tools. |
| `MANAPOOL_ACCESS_TOKEN` | unset | API token (`mpat_...`). `MANAPOOL_API_KEY` is accepted as an alias. |
| `MANAPOOL_TRANSPORT` | `stdio` | or `streamable-http` |
| `MANAPOOL_HOST` / `MANAPOOL_PORT` | `127.0.0.1` / `8765` | HTTP mode |
| `MANAPOOL_CACHE_DIR` | `~/.cache/manapool-mcp` | price snapshot location |
| `MANAPOOL_PRICES_TTL` | `86400` | seconds before refreshing prices |
| `MANAPOOL_CACHE_TTL` | `600` | page cache lifetime |
| `MANAPOOL_MIN_INTERVAL` | `0.2` | seconds between upstream requests |

### API credentials (optional)

Everything works anonymously except two tools. Set both variables to enable
`validate_decklist` and `optimize_cart`, and to have `audit_deck` include
ManaPool's own rules check alongside its bracket verdict:

```sh
export MANAPOOL_EMAIL="you@example.com"
export MANAPOOL_ACCESS_TOKEN="mpat_..."
```

Both headers are required by ManaPool's API. A half-configured environment
fails at startup rather than silently falling back to anonymous access. A
rejected token surfaces once, immediately, as a credentials error — it is not
retried.

Two caveats worth knowing:

- **Keyed tools are for stdio or loopback HTTP.** Streamable-HTTP mode has no
  authentication of its own, so anything that can reach the port would be
  spending your account's API quota. Bind `127.0.0.1` as above.
- **`audit_deck` without credentials is still complete for brackets.** It just
  reports what the card list permits; the authoritative legality block is only
  present when a key is configured.

## Notes

- ManaPool's deck pages have no API, so deck tools parse HTML; card tools use
  the public JSON API (`/api/v1/products/singles`, `/prices/singles`). Most of
  the 53 documented paths require a key; `/deck` and `/buyer/optimizer` are
  wrapped only when credentials are configured. `/card_info` overlaps with the
  Scryfall-backed card tools and is not wrapped; the account, seller, order and
  inventory-write endpoints are deliberately out of scope.
- The deck validator resolves cards by name and ManaPool renames some cards
  ("Memories of Nibelheim (Stroke of Midnight)"), so the alias in the
  parenthetical is stripped before sending. `cards_not_found` means "not
  resolved", not "illegal".
- ManaPool's bracket tag is generated and optimistic. `audit_deck` reports what
  the cards permit, not how the deck plays.
- Its price sort is only mostly ascending, so wide price windows are sampled and
  returned with `truncated: true`.
- Decks are rebuilt when stock changes; re-read before buying. Not affiliated
  with ManaPool or Wizards of the Coast.

## Development

```sh
make install && make check    # venv, ruff, pytest
make docker                   # optional image
```

## License

MIT — see [LICENSE](LICENSE).
