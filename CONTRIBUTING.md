# Contributing

## Setup

```sh
git clone https://github.com/abominox/manapool-mcp.git
cd manapool-mcp
uv venv --python 3.12 .venv
. .venv/bin/activate
uv pip install -e ".[dev]"
pytest -q
```

## Checks

`make check` runs lint and tests. CI runs the same on Python 3.11, 3.12 and
3.13, plus a pinned-install job and a Docker handshake smoke test.

```sh
make lint   # ruff check + format --check
make fmt    # ruff format + autofix
make test   # pytest
make docker # build the image
```

## What to be careful with

This project scrapes a site that has no public API and no stable contract.
Changes to the parser are the risky kind:

- **Fail loudly, never silently.** If the card rows stop summing to the header
  count, `parse_deck_page` raises `DeckParseError`. Do not weaken that into a
  warning; a partial deck produces a confidently wrong bracket audit.
- **Do not trust ManaPool's price order.** It is mostly ascending, but rebuilt
  decks are appended to the tail at their new price. Any boundary assumption
  needs a test with out-of-order pages.
- **Capture a fixture first.** `tests/fixtures/*.html` are real pages. If the
  markup changes, add a new fixture and a test rather than loosening a regex.
- **Bracket rules are cited, not invented.** `rules.py` documents its sources
  and the date the Game Changers list was current. Update both together.

## Pull requests

- Keep the diff focused; separate refactors from behaviour changes.
- Add a test for every bug fix. The regression suite exists because several
  early bugs produced plausible-looking wrong answers.
- Update `CHANGELOG.md` under `[Unreleased]`.
- CI must be green before merge.
