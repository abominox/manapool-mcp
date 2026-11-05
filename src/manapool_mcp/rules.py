"""Commander bracket reference data.

Game Changers list is the official 53-card list, unchanged since the
9 February 2026 update (Farewell and Biorhythm added). Source:
magic.wizards.com Commander Brackets / format page.

Bracket rules used for the verdict:
  1 Exhibition / 2 Core : zero Game Changers
  3 Upgraded            : up to three Game Changers
  4 Optimized / 5 cEDH  : unlimited
Mass land denial is excluded from brackets 1-3.
Extra turn spells are legal at 3 but chaining them is not.
Tutor guidance was removed from the bracket rules on 21 Oct 2025, so tutors
are reported for information only and never affect the verdict.
"""

from __future__ import annotations

# Official, 53 cards as of the Feb 2026 update.
GAME_CHANGERS: tuple[str, ...] = (
    # White
    "Drannith Magistrate",
    "Enlightened Tutor",
    "Farewell",
    "Humility",
    "Serra's Sanctum",
    "Smothering Tithe",
    "Teferi's Protection",
    # Blue
    "Consecrated Sphinx",
    "Cyclonic Rift",
    "Fierce Guardianship",
    "Force of Will",
    "Gifts Ungiven",
    "Intuition",
    "Mystical Tutor",
    "Narset, Parter of Veils",
    "Rhystic Study",
    "Thassa's Oracle",
    # Black
    "Ad Nauseam",
    "Bolas's Citadel",
    "Braids, Cabal Minion",
    "Demonic Tutor",
    "Imperial Seal",
    "Necropotence",
    "Opposition Agent",
    "Orcish Bowmasters",
    "Tergrid, God of Fright",
    "Vampiric Tutor",
    # Red
    "Gamble",
    "Jeska's Will",
    "Underworld Breach",
    # Green
    "Aura Shards",
    "Biorhythm",
    "Crop Rotation",
    "Gaea's Cradle",
    "Natural Order",
    "Seedborn Muse",
    "Survival of the Fittest",
    "Worldly Tutor",
    # Multicolour
    "Coalition Victory",
    "Grand Arbiter Augustin IV",
    "Notion Thief",
    # Colourless / lands
    "Ancient Tomb",
    "Chrome Mox",
    "Field of the Dead",
    "Glacial Chasm",
    "Grim Monolith",
    "Lion's Eye Diamond",
    "Mana Vault",
    "Mishra's Workshop",
    "Mox Diamond",
    "Panoptic Mirror",
    "The One Ring",
    "The Tabernacle at Pendrell Vale",
)

MASS_LAND_DENIAL: tuple[str, ...] = (
    # Multi-land destruction
    "Armageddon",
    "Ravages of War",
    "Catastrophe",
    "Decree of Annihilation",
    "Jokulhaups",
    "Obliterate",
    "Wildfire",
    "Burning of Xinye",
    "Death Cloud",
    "Pox",
    "Smallpox",
    "Global Ruin",
    "Impending Disaster",
    "Fall of the Thran",
    "Tectonic Break",
    "Epicenter",
    "Ruination",
    "From the Ashes",
    "Sunder",
    "Boom // Bust",
    # Mana denial / land lockdown (WotC names Winter Orb and Blood Moon as
    # mass land denial even though they destroy nothing)
    "Winter Orb",
    "Static Orb",
    "Rising Waters",
    "Hokori, Dust Drinker",
    "Stasis",
    "Back to Basics",
    "Blood Moon",
    "Magus of the Moon",
    "Contamination",
    "Hall of Gemstone",
    "Root Maze",
    "Storage Matrix",
)

EXTRA_TURNS: tuple[str, ...] = (
    "Time Warp",
    "Temporal Manipulation",
    "Nexus of Fate",
    "Capture of Jingzhou",
    "Expropriate",
    "Time Stretch",
    "Walk the Aeons",
    "Part the Waterveil",
    "Temporal Trespass",
    "Beacon of Tomorrows",
    "Notorious Throng",
    "Karn's Temporal Sundering",
    "Medomai the Ageless",
    "Savor the Moment",
    "Temporal Mastery",
    "Alrund's Epiphany",
    "Sea Gate Restoration // Sea Gate, Reborn",
    "Timestream Navigator",
    "Last Night Together",
)

# Informational only - see module docstring.
TUTORS: tuple[str, ...] = (
    "Demonic Tutor",
    "Vampiric Tutor",
    "Imperial Seal",
    "Mystical Tutor",
    "Worldly Tutor",
    "Enlightened Tutor",
    "Sylvan Tutor",
    "Personal Tutor",
    "Grim Tutor",
    "Diabolic Tutor",
    "Diabolic Intent",
    "Final Parting",
    "Beseech the Queen",
    "Scheming Symmetry",
    "Wishclaw Talisman",
    "Varragoth, Bloodsky Sire",
    "Sidisi, Undead Vizier",
    "Razaketh, the Foulblooded",
    "Rune-Scarred Demon",
    "Burning-Rune Demon",
    "Fiend Artisan",
    "Entomb",
    "Buried Alive",
    "Jarad's Orders",
    "Defense of the Heart",
    "Natural Order",
    "Chord of Calling",
    "Green Sun's Zenith",
    "Finale of Devastation",
    "Eldritch Evolution",
    "Neoform",
    "Birthing Pod",
    "Fauna Shaman",
    "Survival of the Fittest",
    "Captain Sisay",
    "Yisan, the Wanderer Bard",
    "Prime Speaker Vannifar",
    "Kuldotha Forgemaster",
    "Planar Bridge",
    "Ring of Three Wishes",
    "Inventors' Fair",
    "Long-Term Plans",
    "Merchant Scroll",
    "Solve the Equation",
    "Spellseeker",
    "Tribute Mage",
    "Trinket Mage",
    "Fabricate",
)


def mechanical_bracket(
    game_changer_count: int, has_mass_land_denial: bool, combo_count: int
) -> tuple[int, list[str]]:
    """Lowest bracket the card list mechanically permits, plus reasons.

    This is deliberately conservative about combos: Spellbook reports that a
    combo is assembled, not how fast it is, so any combo is treated as ruling
    out Core without automatically assuming Optimized.
    """
    if has_mass_land_denial:
        return 4, [
            "Mass land denial is excluded from brackets 1-3, so the list cannot "
            "be lower than Optimized."
        ]
    if game_changer_count >= 4:
        return 4, [f"{game_changer_count} Game Changers is more than Upgraded allows."]
    if game_changer_count >= 1:
        return 3, [
            f"{game_changer_count} Game Changer(s): brackets 1 and 2 allow none, "
            "3 allows up to three."
        ]
    if combo_count:
        return 3, [
            "No Game Changers, but a combo is fully present. Core excludes "
            "intentional two-card wins."
        ]
    return 2, [
        "No Game Changers and no combos found, so the list alone permits Core. "
        "It can still be Upgraded by intent and play pattern."
    ]


BRACKET_CRITERIA: str = """\
Commander brackets (official, February 2026 update)

Bracket 1 - Exhibition
  Theme first. No Game Changers, no mass land denial, no extra turns,
  no intentional two-card wins. Expect nine or more turns.

Bracket 2 - Core
  Unoptimised and straightforward. No Game Changers, no mass land denial,
  no intentional two-card wins. Win conditions are incremental, telegraphed
  on the board and disruptable. Expect eight or more turns.

Bracket 3 - Upgraded
  Up to three Game Changers. No mass land denial. Extra turn spells are
  allowed but not chained. Two-card combos only if they cannot win before
  about turn six. Expect six or more turns. Games feature strong synergy,
  high card quality, and wins deployed in one big turn from accrued
  resources.

Bracket 4 - Optimised
  No deck-building restriction beyond the banned list, but not built for
  the cEDH metagame. Fast, compact wins.

Bracket 5 - cEDH
  Built and tuned for the competitive metagame.

The Game Changer count is a floor, not a ceiling: a deck can be under-rated
by its card list if its intent is stronger, and a deck with no Game Changers
is not automatically Core. Brackets are a pre-game conversation tool.
"""
