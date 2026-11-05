"""Commander Spellbook combo lookups."""

from __future__ import annotations

from .client import SPELLBOOK_BASE, Fetcher, UpstreamError
from .models import ComboInfo


def _combo_cards(item: dict) -> list[str]:
    """Spellbook returns cards under `uses`/`requires`, not a flat `cards` key."""
    names: list[str] = []
    for key in ("uses", "requires"):
        for entry in item.get(key, []) or []:
            card = entry.get("card") if isinstance(entry, dict) else None
            name = card.get("name") if isinstance(card, dict) else None
            if name and name not in names:
                names.append(name)
    return names


async def find_combos(fetcher: Fetcher, commanders: list[str], cards: list[str]) -> list[ComboInfo]:
    """Return combos Spellbook considers fully present in the deck."""
    if not commanders:
        return []
    payload = {
        "commanders": [{"card": name} for name in commanders],
        "main": [{"card": name} for name in cards],
    }
    try:
        data = await fetcher.post_json(f"{SPELLBOOK_BASE}/find-my-combos", payload)
    except UpstreamError:
        # The audit is still useful without combo data; callers add a note.
        raise
    results = data.get("results", {}) if isinstance(data, dict) else {}
    combos: list[ComboInfo] = []
    for item in results.get("included", []) or []:
        if not isinstance(item, dict):
            continue
        combos.append(
            ComboInfo(
                description=(item.get("description") or "").strip() or None,
                cards=_combo_cards(item),
                bracket_tag=item.get("bracketTag") or item.get("bracket_tag"),
            )
        )
    return combos
