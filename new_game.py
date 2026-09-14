from random import Random

from challenges import ChallengePool
from config import DEFAULT_BONUS_CHANCE, DEFAULT_TEAM_COLORS, POWERUP_COSTS
from game import GameState
from jloxgame.state import Team
from powerups import CurseDeck
from game import GameState

# This function has been modified and moved to this file for use in local (offline) scripts.
# It initialises a game using the events system, and fills in dummy thread and role ids to bypass all discord requirements
# The game can be interacted with either using events or with the pre-existing functions.


def new_game(
    start_positions: dict[str, str],
    team_colors: dict[str, str] | None = None,
    bonus_chance: float = DEFAULT_BONUS_CHANCE,
    enabled_powerups: set[str] | None = None,
    rng: Random | None = None,
    *,
    bonus_interchanges: set[str] | frozenset[str] | None = None,
    challenge_pool: ChallengePool | None = None,
    curse_deck: CurseDeck | None = None,
) -> GameState:
    """Load the map and create a new GameState.

    start_positions maps each team name to their starting station key.
    team_colors optionally maps each team name to a hex color string.
    If a team has no entry in team_colors, it is assigned the next colour from
    DEFAULT_TEAM_COLORS in the order teams appear in start_positions.
    Teams must start at different interchanges, and each begins with STARTING_COINS
    coins, no declared line, and must complete an initial challenge first.

    Bonus interchanges (which pay out bonus coins) are chosen at random — each
    interchange has ``bonus_chance`` probability. Pass an explicit
    ``bonus_interchanges`` to override, or a seeded ``rng`` for reproducibility.
    Origins are never bonus interchanges (excluded from both paths).

    Challenges are drawn from ``challenge_pool`` (or loaded from ``challenges_path``,
    default ``challenges.json``); a missing file just means no offers. ``rng`` seeds
    bonus selection and all challenge draws.

    All teams share one **initial challenge** (`GameState.initial_challenge`), drawn
    once here — since there's no neck yet to size a difficulty from — with a
    difficulty picked uniformly from `INITIAL_DIFFICULTY_MIN`..`INITIAL_DIFFICULTY_MAX`
    (in `config.py`) rather than via `get_difficulty`.
    """
    if not start_positions:
        raise ValueError("At least one team is required")

    game = GameState()
    if rng:
        game.rng = rng
    game.thread_id = 1

    for team, station in start_positions.items():
        if not game.map.has_station(station):
            raise ValueError(f"Unknown start station for {team!r}: {station!r}")

    if len(start_positions) != len(set(start_positions.values())):
        raise ValueError("Teams must not start at the same interchange")

    colors = team_colors or {}
    default_color_iter = iter(DEFAULT_TEAM_COLORS)

    game.teams = [
        Team(name, int(colour[1:], 16), role_id=id) 
        for name, colour, id in zip(start_positions.keys(), DEFAULT_TEAM_COLORS, range(len(DEFAULT_TEAM_COLORS)))
    ]

    game.configured(
        list(start_positions.values()), 
        [colors.get(team) or next(default_color_iter) for team in start_positions], 
        bonus_chance, 
        list(enabled_powerups or POWERUP_COSTS.keys())
    )

    game.started()

    # Origins are never bonus interchanges, whether chosen randomly or passed in.
    if bonus_interchanges is not None:
        origins = set(start_positions.values())
        game.bonus_interchanges = set(bonus_interchanges) - origins

    if challenge_pool is not None:
        game.challenges = challenge_pool

    # A deck with no curses in it counts as no deck at all — otherwise the powerup
    # would stay enabled and sell a card that can never be played.
    if curse_deck is not None:
        if len(curse_deck) == 0:
            curse_deck = None
        game.curse_deck = curse_deck

    if game.curse_deck is None:
        game.enabled_powerups.discard("curse")

    return game
