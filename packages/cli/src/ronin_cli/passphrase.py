"""Generate memorable diceware-style passphrases — `ronin passphrase`.

Picks words at random (via ``secrets``) from a curated list to build passphrases
that are both strong and memorable (e.g. ``copper-tiger-velvet-anchor``). The
entropy math and assembly are pure/testable; the randomness uses ``secrets``.
"""
from __future__ import annotations

import math
import secrets

WORDS = [
    "able", "acid", "amber", "anchor", "apple", "arc", "arrow", "atom", "aura", "axis",
    "bacon", "badge", "bamboo", "banjo", "basil", "beacon", "bison", "blaze", "bloom", "boa",
    "bolt", "bonus", "brass", "bravo", "brick", "bronze", "buffer", "cabin", "cactus", "cargo",
    "cedar", "chalk", "charm", "cherry", "chrome", "cider", "cipher", "clay", "cloud", "clover",
    "cobalt", "comet", "copper", "coral", "cosmic", "cotton", "cove", "crane", "crisp", "crystal",
    "dawn", "delta", "denim", "diamond", "ember", "envoy", "fable", "falcon", "fern", "flint",
    "flora", "forest", "fox", "frost", "galaxy", "garnet", "ginger", "glacier", "granite", "harbor",
    "hazel", "honey", "ivory", "jade", "jasmine", "jet", "jungle", "kelp", "kite", "koala",
    "lagoon", "lantern", "lava", "lemon", "lily", "lotus", "lunar", "lynx", "magnet", "mango",
    "maple", "marble", "meadow", "mint", "mirror", "moss", "moth", "nectar", "nimbus", "nova",
    "oak", "ocean", "olive", "onyx", "opal", "orbit", "otter", "pansy", "pearl", "pebble",
    "pepper", "petal", "pine", "pixel", "plum", "polar", "prism", "pulse", "quartz", "quill",
    "quiver", "radar", "raven", "reed", "rhino", "ripple", "river", "rose", "ruby", "rust",
    "saffron", "sage", "sand", "sapphire", "shadow", "shell", "silk", "silver", "slate", "solar",
    "spark", "spruce", "stone", "storm", "summit", "swift", "tango", "tide", "tiger", "topaz",
    "torch", "tulip", "tundra", "ultra", "umber", "valley", "velvet", "vine", "violet", "walnut",
    "wave", "willow", "wolf", "zephyr", "zinc",
]


def passphrase(words: int = 4, separator: str = "-", capitalize: bool = False,
               add_number: bool = False) -> dict:
    """Build a passphrase + report its entropy. ``words`` clamped to 2-12. Uses
    ``secrets`` for selection; the entropy/format logic is pure."""
    n = max(2, min(int(words or 4), 12))
    picks = [secrets.choice(WORDS) for _ in range(n)]
    if capitalize:
        picks = [w.capitalize() for w in picks]
    phrase = separator.join(picks)
    if add_number:
        phrase += separator + str(secrets.randbelow(90) + 10)
    bits = n * math.log2(len(WORDS)) + (math.log2(90) if add_number else 0)
    return {"passphrase": phrase, "words": n, "entropy_bits": round(bits, 1)}


def entropy_for(words: int, wordlist_size: int = len(WORDS)) -> float:
    """Entropy (bits) of a passphrase of ``words`` words. Pure."""
    return round(words * math.log2(wordlist_size), 1)
