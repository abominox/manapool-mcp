# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `search_cards`, `get_card` and `price_deck`, backed by ManaPool's public API
  (`/products/singles`, `/prices/singles`) and Scryfall for name resolution.
- `README` section mapping the 53-path ManaPool OpenAPI surface to the
  endpoints that are actually public.
- `Retry-After` handling for rate-limited upstreams.
- Disk-cached price snapshot (`~/.cache/manapool-mcp`).

### Changed

- The server now runs as a local binary installed with `uv tool install`;
  Docker packaging removed.
- Release workflow builds a wheel and sdist instead of a container image.

## [0.1.0] - 2026-11-05

### Added

- MCP server over stdio and Streamable HTTP, packaged as a Docker image.
- `search_decks`: browse ManaPool pre-built Commander listings by commander,
  bracket, theme, colours and price window.
- `get_deck`: full deck read-out with commander(s), seller, price, section
  counts and per-card quantities and prices, plus optional Scryfall type lines.
- `audit_deck`: Game Changers, mass land denial, extra turns and Commander
  Spellbook combos, with the lowest bracket the list mechanically permits.
- Resources `manapool://rules/game-changers` and `manapool://rules/brackets`.
- Offline test suite with captured page fixtures and a fake listing fetcher.

### Notes

- The official 53-card Game Changers list is current as of the
  February 2026 update.
- ManaPool's price sort is only mostly ascending, so wide price windows are
  sampled and reported as `truncated` rather than claimed complete.

[Unreleased]: https://github.com/abominox/manapool-mcp/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/abominox/manapool-mcp/releases/tag/v0.1.0
