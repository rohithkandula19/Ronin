"""Pandle — guess the hidden 5-letter word in 6 tries, ronin's take on Wordle.

A real Wordle: green/amber/grey tiles with correct duplicate-letter handling,
a live on-screen QWERTY keyboard that colors each key by its best-known state,
all past guesses stacked, and a wide validity check that rejects non-words.
"""
from __future__ import annotations

import random

from rich.console import Console

from ._engine import GameMeta, ask_line, header

# --------------------------------------------------------------------------- #
# Word lists
# --------------------------------------------------------------------------- #
# Curated answer pool — common, fair 5-letter words the secret is drawn from.
ANSWERS: list[str] = [
    "apple", "brave", "crane", "drive", "eagle", "flame", "grape", "house",
    "input", "joker", "knife", "lemon", "money", "night", "ocean", "plant",
    "queen", "river", "stone", "table", "ultra", "vivid", "water", "youth",
    "zebra", "bread", "chair", "dream", "earth", "frost", "ghost", "happy",
    "ivory", "light", "music", "noble", "olive", "pride", "quiet", "raven",
    "sugar", "tiger", "uncle", "vocal", "wheat", "amber", "blaze", "cloud",
    "dance", "elbow", "fancy", "glory", "honey", "irony", "jolly", "kneel",
    "lunar", "maple", "north", "orbit", "pearl", "quill", "robot", "spice",
    "trust", "vault", "whale", "yacht", "angel", "beach", "candy", "depth",
    "ember", "field", "grain", "heart", "image", "judge", "lucky", "metal",
    "ninja", "onion", "piano", "rapid", "shine", "throw", "unity", "wrist",
]

# Additional words that are valid *guesses* but never the answer. The accepted
# guess set is ANSWERS ∪ EXTRA_VALID — anything else that is 5 a-z letters is
# rejected with a hint so players can't cheese with gibberish.
EXTRA_VALID: list[str] = [
    "abide", "actor", "adept", "agile", "alarm", "alert", "alibi", "alien",
    "alloy", "aloft", "aloud", "altar", "amaze", "ample", "angle", "ankle",
    "apron", "ardor", "arena", "aroma", "array", "ascot", "askew", "atlas",
    "audio", "augur", "aught", "avail", "await", "awake", "award", "aware",
    "awful", "badge", "bagel", "baker", "balmy", "basic", "baste", "beard",
    "beast", "befit", "began", "begin", "being", "below", "bench", "berry",
    "bicep", "bingo", "birch", "black", "blade", "blame", "bland", "blank",
    "blast", "bleak", "blend", "bless", "blimp", "blind", "blink", "bliss",
    "block", "bloom", "blown", "bluff", "blunt", "blush", "board", "boast",
    "bonus", "boost", "booth", "bored", "bound", "brain", "brake", "brand",
    "brash", "brass", "bring", "brisk", "broad", "broke", "brook", "broom",
    "brown", "brush", "buddy", "buggy", "build", "built", "bunch", "burnt",
    "cabin", "cable", "cacao", "cadet", "cameo", "canal", "canon", "cargo",
    "carve", "caste", "catch", "cause", "cease", "cedar", "chalk", "champ",
    "chant", "chaos", "charm", "chart", "chase", "cheap", "cheat", "check",
    "cheek", "cheer", "chess", "chest", "chief", "child", "chill", "chime",
    "china", "chirp", "choir", "choke", "chord", "chore", "chose", "chunk",
    "civic", "civil", "claim", "clamp", "clang", "clash", "clasp", "class",
    "claw", "clean", "clear", "clerk", "click", "cliff", "climb", "cling",
    "cloak", "clock", "clone", "close", "cloth", "clove", "clown", "clued",
    "clump", "clung", "coach", "coast", "cobra", "comet", "comic", "comma",
    "coral", "couch", "cough", "could", "count", "court", "cover", "covet",
    "crack", "craft", "cramp", "crash", "crate", "crawl", "craze", "crazy",
    "cream", "creek", "creep", "crept", "crest", "crime", "crisp", "croak",
    "crook", "cross", "crowd", "crown", "crude", "cruel", "crumb", "crush",
    "crust", "crypt", "cubic", "curve", "cyber", "cynic", "daily", "dairy",
    "daisy", "dandy", "dealt", "death", "debit", "debut", "decal", "decay",
    "decoy", "decor", "delay", "delta", "demon", "denim", "dense", "depot",
    "devil", "diary", "digit", "diner", "dingo", "dirty", "disco", "ditch",
    "diver", "dizzy", "dodge", "donor", "doubt", "dough", "dozen", "draft",
    "drain", "drama", "drank", "drawn", "dread", "dress", "dried", "drift",
    "drill", "drink", "drone", "drown", "drunk", "dryer", "dusty", "dwarf",
    "eager", "easel", "eaten", "ebony", "edict", "eerie", "egret", "eject",
    "elder", "elect", "elite", "email", "emcee", "empty", "enact", "ended",
    "enemy", "enjoy", "ensue", "enter", "entry", "envoy", "epoch", "equal",
    "equip", "erase", "error", "essay", "ether", "ethic", "event", "evict",
    "evoke", "exact", "exalt", "excel", "exert", "exile", "exist", "extra",
    "fable", "facet", "faint", "fairy", "faith", "false", "fault", "favor",
    "feast", "fence", "ferry", "fetch", "fever", "fiber", "fifth", "fifty",
    "fight", "filer", "filly", "filth", "final", "finch", "first", "fixer",
    "fizzy", "flair", "flake", "flank", "flash", "flask", "fleet", "flesh",
    "flick", "fling", "flint", "flirt", "float", "flock", "flood", "floor",
    "flora", "flour", "flown", "fluff", "fluid", "fluke", "flush", "flute",
    "foamy", "focal", "focus", "foggy", "folly", "force", "forge", "forte",
    "forth", "forty", "forum", "found", "frail", "frame", "frank", "fraud",
    "freak", "fresh", "fried", "frill", "front", "fruit", "fudge", "fully",
    "fungi", "funny", "fussy", "fuzzy", "gable", "gamer", "gauge", "gaunt",
    "gauze", "gavel", "gecko", "genie", "genre", "germ", "giant", "giddy",
    "girth", "given", "giver", "glade", "gland", "glare", "glass", "glaze",
    "gleam", "glide", "glint", "globe", "gloom", "glove", "glyph", "gnome",
    "going", "golem", "goofy", "goose", "gorge", "gouda", "grace", "grade",
    "graft", "grand", "grant", "graph", "grasp", "grass", "grave", "gravy",
    "graze", "great", "greed", "green", "greet", "grief", "grill", "grime",
    "grind", "groan", "groin", "groom", "grope", "gross", "group", "grove",
    "growl", "grown", "gruff", "grunt", "guard", "guava", "guess", "guest",
    "guide", "guild", "guile", "guilt", "guise", "gulch", "gully", "gumbo",
    "gusto", "gusty", "habit", "hairy", "halve", "handy", "hardy", "harsh",
    "haste", "hasty", "hatch", "haunt", "haven", "havoc", "hazel", "heady",
    "heath", "heave", "heavy", "hedge", "hefty", "hello", "hence", "herbs",
    "heron", "hilly", "hinge", "hippo", "hitch", "hoard", "hobby", "hoist",
    "homer", "honor", "horde", "horse", "hotel", "hound", "hover", "human",
    "humid", "humor", "hunch", "hunky", "hurry", "husky", "hutch", "hydro",
    "hyena", "hymen", "ideal", "idiom", "idiot", "igloo", "imbue", "impel",
    "incur", "index", "inept", "inert", "infer", "ingot", "inlay", "inlet",
    "inner", "input", "inset", "intro", "ionic", "irate", "issue", "itchy",
    "jaunt", "jazzy", "jelly", "jewel", "jiffy", "joint", "joist", "joust",
    "juice", "juicy", "jumbo", "jumpy", "junta", "kayak", "kebab", "ketch",
    "khaki", "kinky", "kiosk", "kitty", "knack", "knave", "knead", "knelt",
    "knock", "knoll", "known", "koala", "label", "labor", "laden", "ladle",
    "lance", "lapse", "large", "larva", "laser", "latch", "later", "lathe",
    "latte", "laugh", "layer", "leach", "leafy", "leaky", "leant", "leapt",
    "learn", "lease", "leash", "least", "ledge", "leech", "leery", "lefty",
    "legal", "lemur", "level", "lever", "libel", "liken", "lilac", "limbo",
    "limit", "linen", "liner", "lingo", "links", "lipid", "lithe", "liver",
    "llama", "loath", "lobby", "local", "locus", "lodge", "lofty", "logic",
    "login", "loose", "lorry", "loser", "louse", "lousy", "lover", "lower",
    "loyal", "lucid", "lumen", "lumpy", "lunch", "lunge", "lurch", "lurid",
    "lusty", "lyric", "macho", "macro", "madam", "magic", "magma", "maize",
    "major", "maker", "manga", "mango", "mangy", "mania", "manor", "march",
    "marsh", "mason", "match", "matey", "mauve", "maxim", "maybe", "mayor",
    "meant", "medal", "media", "melon", "mercy", "merge", "merit", "merry",
    "messy", "metro", "micro", "midst", "might", "milky", "mimic", "mince",
    "miner", "minor", "minus", "mirth", "miser", "missy", "mocha", "modal",
    "model", "modem", "moist", "molar", "moldy", "mommy", "moose", "moral",
    "moss", "motel", "motif", "motor", "motto", "mound", "mount", "mourn",
    "mouse", "mouth", "mover", "movie", "mucky", "muddy", "mulch", "mummy",
    "mumps", "munch", "mural", "murky", "mushy", "muter", "myrrh", "nadir",
    "naive", "naked", "nanny", "nasal", "nasty", "naval", "navel", "neat",
    "needy", "nerve", "nervy", "never", "newer", "newly", "nicer", "niche",
    "niece", "nifty", "nimbi", "nippy", "noise", "noisy", "nomad", "nosey",
    "notch", "novel", "nudge", "nurse", "nutty", "nylon", "nymph", "oasis",
    "occur", "ocher", "octal", "offer", "often", "olden", "older", "omega",
    "onset", "opera", "opine", "opium", "optic", "orbit", "order", "organ",
    "other", "otter", "ought", "ounce", "outer", "ovary", "overt", "owing",
    "owner", "ozone", "paddy", "padre", "pagan", "paint", "panel", "panic",
    "pansy", "papa", "paper", "parka", "parse", "party", "pasta", "paste",
    "pasty", "patch", "patio", "patsy", "patty", "pause", "payee", "payer",
    "peace", "peach", "pearl", "pecan", "pedal", "penal", "pence", "penny",
    "perch", "peril", "perky", "pesky", "pesto", "petal", "petty", "phase",
    "phone", "photo", "piano", "picky", "piece", "piety", "piggy", "pilot",
    "pinch", "piney", "pinky", "pinto", "pious", "pipe", "pitch", "pithy",
    "pivot", "pixel", "pixie", "pizza", "place", "plaid", "plain", "plait",
    "plane", "plank", "plate", "plaza", "plead", "pleat", "plied", "pluck",
    "plumb", "plume", "plump", "plunk", "plush", "poach", "podgy", "point",
    "poise", "poker", "polar", "polka", "polyp", "pooch", "poppy", "porch",
    "poser", "posit", "pouch", "pound", "pour", "power", "prank", "prawn",
    "preen", "press", "price", "prick", "prime", "primo", "print", "prior",
    "prism", "privy", "prize", "probe", "prone", "prong", "proof", "props",
    "prose", "proud", "prove", "prowl", "proxy", "prune", "psalm", "pulpy",
    "pulse", "punch", "pupil", "puppy", "puree", "purer", "purge", "purse",
    "pushy", "putty", "quack", "quail", "quake", "qualm", "quark", "quart",
    "quash", "quasi", "quell", "query", "quest", "queue", "quick", "quirk",
    "quota", "quote", "rabbi", "rabid", "radar", "radio", "rainy", "raise",
    "rally", "ranch", "randy", "range", "rangy", "ratio", "ratty", "razor",
    "reach", "react", "ready", "realm", "rebel", "rebus", "rebut", "recap",
    "recur", "reedy", "refer", "regal", "rehab", "reign", "relax", "relay",
    "relic", "remit", "renal", "repay", "repel", "reply", "rerun", "reset",
    "resin", "retro", "retry", "reuse", "revel", "rhino", "rhyme", "ridge",
    "rifle", "right", "rigid", "rigor", "rinse", "ripen", "riper", "risen",
    "riser", "risky", "rival", "roach", "roast", "rocky", "rogue", "roman",
    "rouge", "rough", "round", "rouse", "route", "rover", "rowdy", "rower",
    "royal", "ruddy", "rugby", "ruler", "rumba", "rumor", "rural", "rusty",
    "saber", "sadly", "safer", "saint", "salad", "sally", "salon", "salsa",
    "salty", "salve", "sandy", "saucy", "sauna", "saute", "savor", "savoy",
    "scald", "scale", "scalp", "scaly", "scamp", "scant", "scare", "scarf",
    "scary", "scene", "scent", "scoff", "scold", "scone", "scoop", "scope",
    "score", "scorn", "scour", "scout", "scowl", "scram", "scrap", "scrub",
    "scrum", "scuba", "seedy", "seize", "sense", "serif", "serum", "serve",
    "setup", "seven", "sever", "sewer", "shack", "shade", "shady", "shaft",
    "shake", "shaky", "shale", "shall", "shame", "shank", "shape", "share",
    "shark", "sharp", "shave", "shawl", "shear", "sheen", "sheep", "sheer",
    "sheet", "shelf", "shell", "shied", "shift", "shine", "shiny", "shire",
    "shirk", "shirt", "shoal", "shock", "shone", "shook", "shoot", "shore",
    "short", "shout", "shove", "shown", "showy", "shrew", "shrub", "shrug",
    "shuck", "shunt", "shush", "siege", "sieve", "sight", "sigma", "silky",
    "silly", "since", "sinew", "siren", "sissy", "sixth", "sixty", "skate",
    "skier", "skill", "skimp", "skirt", "skull", "skunk", "slain", "slang",
    "slant", "slash", "slate", "slave", "sleek", "sleep", "sleet", "slept",
    "slice", "slick", "slide", "slime", "slimy", "sling", "slink", "sloop",
    "slope", "slosh", "sloth", "slump", "slung", "slunk", "slurp", "slush",
    "slyly", "smack", "small", "smart", "smash", "smear", "smell", "smelt",
    "smile", "smirk", "smith", "smoke", "smoky", "snack", "snail", "snake",
    "snaky", "snare", "snarl", "sneak", "sneer", "snide", "sniff", "snipe",
    "snoop", "snore", "snort", "snout", "snowy", "snuck", "snuff", "soapy",
    "sober", "soggy", "solar", "solid", "solve", "sonar", "sonic", "sooth",
    "sooty", "sorry", "sound", "south", "space", "spade", "spank", "spare",
    "spark", "spasm", "spawn", "speak", "spear", "speck", "speed", "spell",
    "spend", "spent", "sperm", "spice", "spicy", "spied", "spiel", "spike",
    "spiky", "spill", "spilt", "spine", "spiny", "spire", "spite", "splat",
    "split", "spoil", "spoke", "spoof", "spook", "spool", "spoon", "spore",
    "sport", "spout", "spray", "spree", "sprig", "spunk", "spurn", "spurt",
    "squad", "squat", "squib", "stack", "staff", "stage", "staid", "stain",
    "stair", "stake", "stale", "stalk", "stall", "stamp", "stand", "stank",
    "stare", "stark", "start", "stash", "state", "stave", "stead", "steak",
    "steal", "steam", "steed", "steel", "steep", "steer", "stein", "stern",
    "stick", "stiff", "still", "stilt", "sting", "stink", "stint", "stock",
    "stoic", "stoke", "stole", "stomp", "stood", "stool", "stoop", "store",
    "stork", "storm", "story", "stout", "stove", "strap", "straw", "stray",
    "strip", "strut", "stuck", "study", "stuff", "stump", "stung", "stunk",
    "stunt", "style", "suave", "sulky", "sully", "sumac", "sunny", "super",
    "surer", "surge", "surly", "swami", "swamp", "swarm", "swash", "swath",
    "swear", "sweat", "sweep", "sweet", "swell", "swept", "swift", "swill",
    "swine", "swing", "swirl", "swish", "swoon", "swoop", "sword", "swore",
    "sworn", "swung", "synod", "syrup", "tabby", "taboo", "tacit", "tacky",
    "taffy", "taint", "taken", "taker", "tally", "talon", "tamer", "tango",
    "tangy", "taper", "tapir", "tardy", "tarot", "taste", "tasty", "tatty",
    "taunt", "tawny", "teach", "teary", "tease", "teddy", "teeth", "tempo",
    "tenet", "tenor", "tense", "tenth", "tepee", "tepid", "terra", "terse",
    "testy", "thank", "theft", "their", "theme", "there", "these", "thick",
    "thief", "thigh", "thing", "think", "third", "thong", "thorn", "those",
    "three", "threw", "throb", "throw", "thrum", "thumb", "thump", "thyme",
    "tiara", "tibia", "tidal", "tiger", "tight", "tilde", "timer", "timid",
    "tipsy", "titan", "tithe", "title", "toast", "today", "toffy", "togas",
    "token", "tonal", "tonga", "tonic", "tooth", "topaz", "topic", "torch",
    "torso", "torus", "total", "totem", "touch", "tough", "towel", "tower",
    "toxic", "toxin", "trace", "track", "tract", "trade", "trail", "train",
    "trait", "tramp", "trash", "tread", "treat", "trend", "triad", "trial",
    "tribe", "trice", "trick", "tried", "tripe", "trite", "troll", "troop",
    "trope", "trout", "trove", "truce", "truck", "truer", "truly", "trump",
    "trunk", "truss", "truth", "tryst", "tubby", "tulip", "tully", "tumor",
    "tunic", "turbo", "tutor", "twang", "tweak", "tweed", "tweet", "twice",
    "twine", "twirl", "twist", "twixt", "tying", "udder", "ulcer", "ultra",
    "umbra", "unbox", "uncut", "under", "undid", "undue", "unfed", "unfit",
    "unify", "union", "unite", "unlit", "unmet", "unset", "untie", "until",
    "unwed", "unzip", "upper", "upset", "urban", "urine", "usage", "usher",
    "using", "usual", "usurp", "utile", "utter", "vague", "valet", "valid",
    "valor", "value", "valve", "vapor", "vegan", "venom", "venue", "verge",
    "verse", "verso", "verve", "vicar", "video", "vigil", "vigor", "villa",
    "vinyl", "viola", "viper", "viral", "virus", "visit", "visor", "vista",
    "vital", "vodka", "vogue", "voice", "voila", "voter", "vowel", "wacky",
    "wafer", "wager", "wagon", "waist", "waive", "waltz", "warty", "waste",
    "watch", "watt", "wavy", "waxen", "weary", "weave", "wedge", "weedy",
    "weigh", "weird", "welch", "welsh", "wench", "whack", "wharf", "wheel",
    "whelp", "where", "which", "whiff", "while", "whine", "whiny", "whirl",
    "whisk", "white", "whole", "whoop", "whose", "widen", "wider", "widow",
    "width", "wield", "wight", "willy", "wimpy", "wince", "winch", "windy",
    "wiser", "wispy", "witch", "witty", "woken", "woman", "women", "woody",
    "wooer", "wooly", "woozy", "wordy", "world", "worry", "worse", "worst",
    "worth", "would", "wound", "woven", "wrack", "wrap", "wrath", "wreak",
    "wreck", "wrest", "wring", "wrong", "wrote", "wrung", "wryly", "yearn",
    "yeast", "yield", "yodel", "yokel", "young", "yummy", "zesty", "zonal",
]

# Build the accepted-guess set once (lowercased, exactly 5 a-z letters).
VALID_GUESSES: frozenset[str] = frozenset(
    w for w in (ANSWERS + EXTRA_VALID)
    if len(w) == 5 and w.isalpha() and w.isascii()
)

# --------------------------------------------------------------------------- #
# Palette
# --------------------------------------------------------------------------- #
GREEN = "#9ece6a"   # hit
YELLOW = "#e0af68"  # present (amber)
GREY = "#6b7089"    # miss
TEAL = "#2dd4bf"    # accent
DARK = "#1a1b26"    # tile foreground on colored backgrounds
UNUSED = "#3b4261"  # keyboard key not yet tried

# Precedence used to pick a key's "best-known" state.
_RANK = {"hit": 3, "present": 2, "miss": 1}

KEYBOARD_ROWS = ("qwertyuiop", "asdfghjkl", "zxcvbnm")

MAX_TRIES = 6


def score_guess(secret: str, guess: str) -> list[str]:
    """Pure rule: score each letter as ``"hit"`` | ``"present"`` | ``"miss"``.

    Standard Wordle duplicate handling: greens are resolved first, then a letter
    only marks ``"present"`` as many times as it remains in the secret.
    """
    secret = secret.lower()
    guess = guess.lower()
    result = ["miss"] * 5
    remaining: dict[str, int] = {}

    # First pass: exact-position hits, tally leftover secret letters.
    for i in range(5):
        if guess[i] == secret[i]:
            result[i] = "hit"
        else:
            remaining[secret[i]] = remaining.get(secret[i], 0) + 1

    # Second pass: presents, consuming leftover letters.
    for i in range(5):
        if result[i] == "hit":
            continue
        ch = guess[i]
        if remaining.get(ch, 0) > 0:
            result[i] = "present"
            remaining[ch] -= 1

    return result


def is_valid_guess(word: str) -> bool:
    """A guess is valid when it's exactly 5 a-z letters and a known word."""
    word = word.lower()
    if len(word) != 5 or not word.isalpha() or not word.isascii():
        return False
    return word in VALID_GUESSES


def _tile(ch: str, state: str) -> str:
    """One tile: an uppercase letter on a colored background."""
    bg = {"hit": GREEN, "present": YELLOW, "miss": GREY}[state]
    return f"[bold {DARK} on {bg}] {ch.upper()} [/]"


def _empty_tile() -> str:
    return f"[{UNUSED}] · [/]"


def _render_board(
    console: Console, guesses: list[tuple[str, list[str]]]
) -> None:
    """Render all guesses stacked, plus empty rows for remaining tries."""
    console.print()
    for word, scored in guesses:
        row = " ".join(_tile(ch, s) for ch, s in zip(word, scored))
        console.print("    " + row)
    for _ in range(MAX_TRIES - len(guesses)):
        row = " ".join(_empty_tile() for _ in range(5))
        console.print("    " + row)
    console.print()


def _render_keyboard(console: Console, key_state: dict[str, str]) -> None:
    """Render the QWERTY keyboard, each key colored by its best-known state."""
    bg_for = {"hit": GREEN, "present": YELLOW, "miss": GREY}
    indents = {0: "    ", 1: "     ", 2: "       "}
    for ri, row in enumerate(KEYBOARD_ROWS):
        keys = []
        for ch in row:
            state = key_state.get(ch)
            if state is None:
                keys.append(f"[bold {UNUSED}] {ch.upper()} [/]")
            elif state == "miss":
                keys.append(f"[{GREY}] {ch.upper()} [/]")
            else:
                keys.append(f"[bold {DARK} on {bg_for[state]}] {ch.upper()} [/]")
        console.print(indents[ri] + " ".join(keys))
    console.print()


def _update_key_state(
    key_state: dict[str, str], word: str, scored: list[str]
) -> None:
    """Upgrade each guessed letter's key to its best-seen state."""
    for ch, s in zip(word, scored):
        cur = key_state.get(ch)
        if cur is None or _RANK[s] > _RANK[cur]:
            key_state[ch] = s


def _play(console: Console) -> None:
    header(console, GAME)
    secret = random.choice(ANSWERS)
    console.print(
        f"  [{GREY}]Guess the 5-letter word in {MAX_TRIES} tries. "
        f"Type [bold {TEAL}]q[/bold {TEAL}] to quit.[/]"
    )

    guesses: list[tuple[str, list[str]]] = []
    key_state: dict[str, str] = {}

    _render_board(console, guesses)
    _render_keyboard(console, key_state)

    while len(guesses) < MAX_TRIES:
        left = MAX_TRIES - len(guesses)
        raw = ask_line(console, f"guess ({left} left):")
        low = raw.lower()

        if low in ("q", "quit"):
            console.print(
                f"\n  [{GREY}]The word was "
                f"[bold {TEAL}]{secret.upper()}[/bold {TEAL}]. Later![/]\n"
            )
            return
        if raw == "":
            # Bare Enter / EOF — treat as quitting cleanly.
            console.print(
                f"\n  [{GREY}]The word was "
                f"[bold {TEAL}]{secret.upper()}[/bold {TEAL}]. Later![/]\n"
            )
            return

        if len(low) != 5 or not low.isalpha() or not low.isascii():
            console.print(f"  [{YELLOW}]Exactly 5 letters (a-z), please.[/]")
            continue
        if not is_valid_guess(low):
            console.print(
                f"  [{YELLOW}]'{low.upper()}' isn't in the word list — "
                f"try a real word.[/]"
            )
            continue

        scored = score_guess(secret, low)
        guesses.append((low, scored))
        _update_key_state(key_state, low, scored)

        _render_board(console, guesses)
        _render_keyboard(console, key_state)

        if all(s == "hit" for s in scored):
            console.print(
                f"  [bold {GREEN}]Solved it in {len(guesses)}/"
                f"{MAX_TRIES}![/bold {GREEN}] 🟩\n"
            )
            return

    console.print(
        f"  [bold {YELLOW}]Out of tries[/bold {YELLOW}] — the word was "
        f"[bold {TEAL}]{secret.upper()}[/bold {TEAL}].\n"
    )


GAME = GameMeta(key="wordle", name="Pandle", emoji="🟩",
                desc="guess the 5-letter word in 6 tries", play=_play)
