"""Tunable game configuration — balance knobs and presentation defaults.

Centralised here so the numbers can be adjusted in one place without touching the
game logic (game.py) or the renderer (render.py).
"""

from __future__ import annotations

# --- Win condition ---------------------------------------------------------
# A team can win early if its claimed stations (Body) lead every opponent's Body +
# Neck by MORE than this — but only by declaring it (see below).
WINNING_THRESHOLD = 10

# --- Declaring a win ---------------------------------------------------------
# The lead win is never automatic. A team pays to declare it, everyone is told, and
# the lead is checked this long afterwards, giving rivals a last chance to respond.
# A failed declaration stops that team declaring again for the cooldown. The last
# team standing still wins immediately.
DECLARE_WIN_COST = 2
DECLARE_WIN_WINDOW_MINUTES = 20
DECLARE_WIN_COOLDOWN_MINUTES = 30

# --- Contested objectives ------------------------------------------------------
OBJECTIVE_INTERVAL_MINUTES = 60  # a new one this often; unclaimed ones stay live
OBJECTIVE_COINS = 5  # for completing a challenge AT it
OBJECTIVE_STATIONS = 3  # it also counts as this many extra stations in the score
OBJECTIVE_PASS_COINS = 2  # for claiming it in passing, which ends it

# --- Coins -----------------------------------------------------------------
STARTING_COINS = 5  # coins each team starts the game with
EASIER_REWARD = 1  # coins for completing the easier of the two offered challenges
HARDER_REWARD = 3  # coins for completing the harder one

# --- Bonus interchanges ----------------------------------------------------
BONUS_AT_FRONT = 3  # extra coins for completing a challenge AT a bonus interchange
BONUS_CLAIMED = 1  # extra coins for claiming a bonus interchange from elsewhere (in the neck)
DEFAULT_BONUS_CHANCE = 0.15  # per-interchange chance of being a bonus (origins are always excluded)

# --- Initial challenge -------------------------------------------------------
# There's no neck yet at the start of the game, so the initial challenge can't be
# sized via get_difficulty(neck_weights(...)) like a normal one. Instead every team
# gets the same challenge, drawn once with a difficulty picked uniformly from this
# band (see GameState.initial_challenge).
# Centred on a typical route's difficulty: about 6 since get_difficulty was recalibrated on
# the 23 Aug 2026 playtest (the band was 2.5-5.5, around the old typical 4).
INITIAL_DIFFICULTY_MIN = 4.5
INITIAL_DIFFICULTY_MAX = 7.5

# --- Powerups ---------------------------------------------------------------
# The keys of POWERUP_COSTS define the set of known powerups; the value is the
# coin cost to buy one into the hand. Each can be enabled/disabled per game.
POWERUP_COSTS = {
    "jump": 8,  # make one station permanently passable (for everyone)
    "efficiency": 4,  # next veto/failure is free (no veto period)
    "retreat": 3,  # cancel current challenge request; next request must differ
    "detour": 2,  # switch declared line without a challenge (unannounced)
    "curse": 3,  # draw CURSE_OPTIONS curses, keep one to play on another team
}

CURSE_OPTIONS = 2  # curses drawn when you buy one; you keep one, the rest go back

# --- Team colours ----------------------------------------------------------
# Ordered by priority (first N used for N teams), boldest first. Chosen by
# maximising the minimum CIEDE2000 distance between teams: every pair is ΔE >= 22
# apart and each is >= 15 from every colour drawn on the map. The TfL palette
# saturates the hue wheel, so a few teams sit near a line colour — the priority is
# that teams are unmistakable from *each other*.
DEFAULT_TEAM_COLORS = [
    "#0000ff",  # blue
    "#bb00aa",  # magenta
    "#aa9900",  # gold
    "#ff66bb",  # pink
    "#8877ff",  # periwinkle
    "#991100",  # dark red
]

# --- Paths -------------------------------------------------------------------
CHALLENGES_PATH = "challenges.json"
CONNECTIONS_PATH = "map/connections.json"
CURSES_PATH = "curses.json"
