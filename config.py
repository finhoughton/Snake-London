"""Tunable game configuration — balance knobs and presentation defaults.

Centralised here so the numbers can be adjusted in one place without touching the
game logic (game.py) or the renderer (render.py).
"""

from __future__ import annotations

# --- Win condition ---------------------------------------------------------
# A team wins early if it leads every opponent's controlled count by MORE than this.
WINNING_THRESHOLD = 10

# --- Coins -----------------------------------------------------------------
STARTING_COINS = 5  # coins each team starts the game with
EASIER_REWARD = 1  # coins for completing the easier of the two offered challenges
HARDER_REWARD = 3  # coins for completing the harder one

# --- Bonus interchanges ----------------------------------------------------
BONUS_AT_FRONT = 3  # extra coins for completing a challenge AT a bonus interchange
BONUS_CLAIMED = 1  # extra coins for claiming a bonus interchange from elsewhere (in the neck)
DEFAULT_BONUS_CHANCE = 0.15  # per-interchange chance of being a bonus (origins are always excluded)

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
