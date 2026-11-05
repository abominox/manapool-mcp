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
| `MANAPOOL_TRANSPORT` | `stdio` | or `streamable-http` |
| `MANAPOOL_HOST` / `MANAPOOL_PORT` | `127.0.0.1` / `8765` | HTTP mode |
| `MANAPOOL_CACHE_DIR` | `~/.cache/manapool-mcp` | price snapshot location |
| `MANAPOOL_PRICES_TTL` | `86400` | seconds before refreshing prices |
| `MANAPOOL_CACHE_TTL` | `600` | page cache lifetime |
| `MANAPOOL_MIN_INTERVAL` | `0.2` | seconds between upstream requests |

## Notes

- ManaPool's deck pages have no API, so deck tools parse HTML; card tools use
  the public JSON API (`/api/v1/products/singles`, `/prices/singles`). Most of
  the 53 documented paths require a key and are not wrapped.
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
