MIN_PLAYERS = 4
MAX_PLAYERS = 24

RETRY_RUN_GAME = 3

PLAYER_TIMEOUT = 300
PLAYER_TIMEOUT2 = 60

DEFAULT_DAY_WARNING = 240
DEFAULT_DAY_TIMEOUT = 300
DEFAULT_NIGHT_WARNING = 90
DEFAULT_NIGHT_TIMEOUT = 60

EXTRA_WAIT = 30
WAIT_AFTER_JOIN = 15
WAIT_BUCKET_INIT = 1
WAIT_BUCKET_DELAY = 240
WAIT_BUCKET_MAX = 3

NOTIFY_COOLDOWN = 180

GAME_START_TIMEOUT = 60 * 30  # 30 minutes
QUIT_GAME_STASIS = 2

GUNNER_MISS = 1
GUNNER_SUICIDE = 1
GUNNER_HEADSHOT = 2
GUNNER_INJURE = 3
DRUNK_MISS = 3
DRUNK_SUICIDE = 2
DRUNK_HEADSHOT = 2
DRUNK_INJURE = 2
GUNNER_MULTIPLIER = 0.12  # bullets = ceil(num players * multiplier)
SHARPSHOOTER_MULTIPLIER = 0.06
DRUNK_MULTIPLIER = 3
GUNNER_REVENGE_WOLF = 0.25  # chance that gunner will kill wolf

DETECTIVE_REVEAL_CHANCE = 0.4

# Wolves that kill (the wolf cub only becomes one by growing up)
ACTUAL_WOLVES = [
    "wolf",
    "werecrow",
    "doomsayer",
    "wolf cub",
    "werekitten",
    "wolf shaman",
    "wolf mystic",
]

WOLFCHAT_ROLES = ACTUAL_WOLVES + ["traitor", "sorcerer", "warlock", "hag"]

# What the seer sees these roles as (anything else is seen as itself)
ROLES_SEEN_VILLAGER = [
    "werekitten",
    "traitor",
    "sorcerer",
    "warlock",
    "minion",
    "cultist",
    "villager",
    "jester",
    "fool",
    "amnesiac",
    "vengeful ghost",
    "hag",
    "piper",
    "clone",
    "lycan",
    "time lord",
    "turncoat",
    "executioner",
]
ROLES_SEEN_WOLF = [
    "wolf",
    "werecrow",
    "doomsayer",
    "wolf cub",
    "wolf shaman",
    "wolf mystic",
    "monster",
    "succubus",
    "mad scientist",
]

# Night actions, but for id and bless, which are used during the day
COMMANDS_FOR_ROLE = {
    "kill": [w for w in ACTUAL_WOLVES if w != "wolf cub"]
    + ["serial killer", "hunter", "vengeful ghost"],
    "see": ["seer", "oracle", "augur", "doomsayer"],
    "give": ["shaman", "wolf shaman", "crazed shaman"],
    "visit": ["harlot", "succubus"],
    "guard": ["guardian angel", "bodyguard"],
    "observe": ["werecrow", "sorcerer"],
    "id": ["detective"],
    "charm": ["piper"],
    "hex": ["hag"],
    "curse": ["warlock"],
    "choose": ["hot potato"],
    "side": ["turncoat"],
    "bless": ["priest"],
}
DAY_COMMANDS = ["id", "bless"]

# Roles the amnesiac can remember being
AMNESIAC_EXCLUDED = [
    "minion",
    "matchmaker",
    "villager",
    "cultist",
    "amnesiac",
    "clone",
    "monster",
    "piper",
]

lang = {
    "nokills": [
        "The night was quiet. Everyone is safe.",
        "Nobody died last night.",
        "The villagers awaken to a peaceful morning.",
    ],
    "lynched": [
        "The village has decided to lynch *{}*, a *{}*.",
        "*{}* was dragged to the gallows and revealed to be a *{}*.",
        "After much debate, the villagers lynch *{}*. They were a *{}*.",
    ],
    "lynchednoreveal": [
        "The village has decided to lynch *{}*.",
        "*{}* was dragged to the gallows.",
    ],
    "hastotem": ["*{}* has a totem!"],
    "hastotem2": ["*{}* and *{}* have totems!"],
    "hastotems": ["*{}*, and *{}* have totems!"],
}

SHAMAN_TOTEMS = [
    "death_totem",
    "protection_totem",
    "revealing_totem",
    "influence_totem",
    "impatience_totem",
    "pacifism_totem",
    "silence_totem",
    "desperation_totem",
]

WOLF_SHAMAN_TOTEMS = [
    "protection_totem",
    "impatience_totem",
    "pacifism_totem",
    "deceit_totem",
    "lycanthropy_totem",
    "luck_totem",
    "misdirection_totem",
    "silence_totem",
]
