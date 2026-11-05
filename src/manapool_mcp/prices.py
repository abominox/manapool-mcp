"""ManaPool's public singles price export.

`GET /api/v1/prices/singles` is unauthenticated and returns every in-stock
printing (~106k rows, ~52 MB) with per-printing quantity and prices. It is a
full snapshot, so the server caches it to disk and refreshes once a day rather
than per call.
"""

from __future__ import annotations

import gzip
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

from .client import Fetcher, UpstreamError

PRICES_URL = "https://manapool.com/api/v1/prices/singles"
CACHE_NAME = "prices-singles.json.gz"
DEFAULT_TTL = 24 * 60 * 60


def _norm(value: str) -> str:
    value = value.replace("\u2019", "'").lower()
    value = re.sub(r"[^a-z0-9' ]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _alias_keys(name: str) -> list[str]:
    """Keys a card name can be matched under, including split/DFC halves."""
    alias = re.search(r"\(([^)]+)\)\s*$", name)
    base = name[: alias.start()].strip() if alias else name
    keys = [_norm(base)]
    if alias:
        keys.append(_norm(alias.group(1)))
    if " // " in base:
        keys.append(_norm(base.split(" // ")[0]))
    out: list[str] = []
    for key in keys:
        if key and key not in out:
            out.append(key)
    return out


@dataclass(frozen=True)
class Printing:
    name: str
    set_code: str
    number: str
    available: int
    price_cents: int | None
    price_cents_nm: int | None
    price_market: float | None
    url: str

    @property
    def unit_price(self) -> float:
        cents = self.price_cents if self.price_cents is not None else self.price_cents_nm
        return round((cents or 0) / 100.0, 2)


class PriceBook:
    """Lookup index over the daily price snapshot."""

    def __init__(self, rows: list[dict], as_of: str | None) -> None:
        self.as_of = as_of
        self._index: dict[str, list[Printing]] = {}
        for row in rows:
            try:
                printing = Printing(
                    name=row["name"],
                    set_code=row.get("set_code") or "",
                    number=str(row.get("number") or ""),
                    available=int(row.get("available_quantity") or 0),
                    price_cents=row.get("price_cents"),
                    price_cents_nm=row.get("price_cents_nm"),
                    price_market=row.get("price_market"),
                    url=row.get("url") or "",
                )
            except (KeyError, TypeError, ValueError):
                continue
            if printing.available <= 0:
                continue
            if printing.price_cents is None and printing.price_cents_nm is None:
                continue
            for key in _alias_keys(printing.name):
                self._index.setdefault(key, []).append(printing)

        for printings in self._index.values():
            printings.sort(key=lambda p: (p.unit_price, -p.available))

    def cheapest(self, name: str, min_quantity: int = 1) -> Printing | None:
        for key in _alias_keys(name):
            for printing in self._index.get(key, []):
                if printing.available >= min_quantity:
                    return printing
        return None

    def printings(self, name: str) -> list[Printing]:
        for key in _alias_keys(name):
            found = self._index.get(key)
            if found:
                return found
        return []

    def __len__(self) -> int:
        return len(self._index)


def cache_dir() -> Path:
    override = os.environ.get("MANAPOOL_CACHE_DIR")
    if override:
        path = Path(override).expanduser()
    else:
        base = os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")
        path = Path(base) / "manapool-mcp"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _ttl() -> float:
    try:
        return float(os.environ.get("MANAPOOL_PRICES_TTL", DEFAULT_TTL))
    except (TypeError, ValueError):
        return float(DEFAULT_TTL)


async def load_price_book(fetcher: Fetcher, *, force: bool = False) -> PriceBook:
    path = cache_dir() / CACHE_NAME
    fresh = path.exists() and (time.time() - path.stat().st_mtime) < _ttl()
    if fresh and not force:
        try:
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                payload = json.load(handle)
            return PriceBook(payload["rows"], payload.get("as_of"))
        except (OSError, ValueError, KeyError):
            pass  # fall through to a fresh download

    try:
        raw = await fetcher.get_text(PRICES_URL, use_cache=False)
        data = json.loads(raw)
        del raw
    except UpstreamError as exc:
        if path.exists():
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                payload = json.load(handle)
            book = PriceBook(payload["rows"], payload.get("as_of"))
            book.as_of = f"{book.as_of} (stale cache: {exc})"
            return book
        raise

    rows = data.get("data", [])
    if not rows:
        raise UpstreamError("ManaPool price export returned no rows")
    as_of = (data.get("meta") or {}).get("as_of")
    data = None
    try:
        with gzip.open(path, "wt", encoding="utf-8") as handle:
            json.dump({"as_of": as_of, "rows": rows}, handle)
    except OSError:
        pass  # caching is best-effort
    return PriceBook(rows, as_of)
