from __future__ import annotations

from manapool_mcp.rules import EXTRA_TURNS, GAME_CHANGERS, MASS_LAND_DENIAL
from manapool_mcp.server import _match, _name_keys, _normalise_colors


def test_game_changer_list_has_53_unique_cards() -> None:
    assert len(GAME_CHANGERS) == 53
    assert len(set(GAME_CHANGERS)) == 53


def test_game_changer_list_includes_recent_additions() -> None:
    assert "Farewell" in GAME_CHANGERS
    assert "Biorhythm" in GAME_CHANGERS
    assert "Underworld Breach" in GAME_CHANGERS


def test_removed_cards_are_not_game_changers() -> None:
    # Removed in the October 2025 pass.
    for card in ("Expropriate", "Yuriko, the Tiger's Shadow", "Food Chain", "Trinisphere"):
        assert card not in GAME_CHANGERS


def test_name_keys_handles_manapool_alias() -> None:
    keys = _name_keys("Memories of Nibelheim (Stroke of Midnight)")
    assert "stroke of midnight" in keys
    assert "memories of nibelheim" in keys


def test_name_keys_handles_split_cards() -> None:
    keys = _name_keys("Boom // Bust")
    assert "boom" in keys and "bust" in keys


def test_match_finds_alias_variant() -> None:
    names = ["Memories of Nibelheim (Stroke of Midnight)"]
    keys = {name: _name_keys(name) for name in names}
    assert _match(names, keys, ("Farewell",)) == []
    assert _match(names, keys, ("Stroke of Midnight",)) == ["Stroke of Midnight"]


def test_match_is_case_and_punctuation_insensitive() -> None:
    names = ["TEFERI'S PROTECTION"]
    keys = {name: _name_keys(name) for name in names}
    assert _match(names, keys, ("Teferi's Protection",)) == ["Teferi's Protection"]


def test_match_reports_each_rule_once() -> None:
    names = ["Sol Ring", "Sol Ring"]
    keys = {name: _name_keys(name) for name in names}
    assert _match(names, keys, ("Crop Rotation",)) == []


def test_no_false_positive_on_substring() -> None:
    names = ["Rhystic Study of the Ancients"]
    keys = {name: _name_keys(name) for name in names}
    assert _match(names, keys, ("Rhystic Study",)) == []


def test_land_denial_list_covers_armageddon() -> None:
    names = ["Armageddon"]
    keys = {name: _name_keys(name) for name in names}
    assert _match(names, keys, MASS_LAND_DENIAL) == ["Armageddon"]


def test_extra_turns_list_covers_time_warp() -> None:
    names = ["Time Warp"]
    keys = {name: _name_keys(name) for name in names}
    assert _match(names, keys, EXTRA_TURNS) == ["Time Warp"]


def test_normalise_colors() -> None:
    assert _normalise_colors("wubrg") == "WUBRG"
    assert _normalise_colors("W,U") == "WU"
    assert _normalise_colors("") is None
    assert _normalise_colors(None) is None
