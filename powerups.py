"""Powerups system (see PLAN_POWERUPS.md).

Teams buy powerups with coins into an unlimited hand and play them later. This
module owns the powerup *content* (the curse deck) and the registry of play
handlers. The game state (game.py) imports from here; this module never imports
game.py — handlers receive the game object as their first argument — so there is
no import cycle. Adding a future powerup is one entry in ``config.POWERUP_COSTS``
plus one handler in ``POWERUP_HANDLERS`` — and, if it does something at purchase
time rather than when played, an optional entry in ``POWERUP_ON_BUY``.
"""

from __future__ import annotations

import json
import random
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from config import POWERUP_COSTS

if TYPE_CHECKING:
    from game import GameState


@dataclass(frozen=True)
class Curse:
    id: str
    name: str
    description: str


class CurseDeck:
    """A depleting, self-refilling deck of curses (mirrors ``ChallengePool``).

    Draws are uniform random *without replacement*: a drawn curse leaves the
    current cycle. When the cycle empties, the deck resets to the full loaded
    list and the next draw proceeds from a fresh cycle — so draws never fail
    (given >= 1 curse in the JSON), and a curse can repeat only across cycles,
    never within one.
    """

    def __init__(self, path: str = "curses.json"):
        with open(path) as f:
            data = json.load(f)
        self._catalog: list[Curse] = [Curse(**entry) for entry in data["curses"]]
        self._remaining: list[Curse] = list(self._catalog)

    def __len__(self) -> int:
        """How many curses the deck knows in total (its catalog) — not the current cycle."""
        return len(self._catalog)

    def all(self) -> list[Curse]:
        """The curses remaining in the current cycle."""
        return list(self._remaining)

    def get(self, curse_id: str) -> Curse:
        """Look a curse up by id from the full loaded catalog (raises KeyError if unknown)."""
        for curse in self._catalog:
            if curse.id == curse_id:
                return curse
        raise KeyError(f"Unknown curse: {curse_id!r}")

    def draw(self, rng: random.Random | None = None) -> Curse:
        """Draw one curse uniformly without replacement, refilling the deck when empty."""
        picker = rng if rng is not None else random
        if not self._catalog:
            raise ValueError("Cannot draw from a curse deck with no curses")
        if not self._remaining:
            self._remaining = list(self._catalog)
        return self._remaining.pop(picker.randrange(len(self._remaining)))


# --- Play handlers -----------------------------------------------------------
# Each handler is ``handler(game, team, **kwargs)``. Handlers validate *before*
# mutating anything and raise ValueError on bad input, so a failed play never
# consumes the card (GameState.play_powerup removes the card only after the
# handler returns). Curse returns the Curse it played; every other handler
# returns None.


def _handle_jump(game: GameState, team: str, *, station: str | None = None) -> None:
    """Make a station permanently passable for everyone (does not steal it)."""
    if station is None or not game.map.has_station(station):
        raise ValueError(f"Unknown station for jump: {station!r}")
    game.jumped_stations.add(station)
    return None


def _handle_efficiency(game: GameState, team: str) -> None:
    """Arm a free veto. No stacking — ``free_vetoes`` is set to 1, never above."""
    game.get_snake(team).free_vetoes = 1
    return None


def _handle_double_up(game: GameState, team: str) -> None:
    """Arm two doubled challenge rewards. Sets (never adds) to 2 — never exceeds 2."""
    game.get_snake(team).double_up_remaining = 2
    return None


def _handle_retreat(game: GameState, team: str) -> None:
    """Cancel the active request; the next request must go to a different station."""
    snake = game.get_snake(team)
    if not snake.neck_active:
        raise ValueError(f"{team!r} has no active challenge request to retreat")
    if snake.declared_line is None:
        raise ValueError(f"{team!r} cannot retreat the initial challenge")
    snake.blocked_station = snake.front
    snake.front = snake.anchor
    snake.neck_active = False
    snake.offer = None
    # A detour parked mid-challenge was validated against the Front the team is
    # now walking back from, so it lapses rather than carrying to a station it may
    # not even serve. (The Front cannot move any other way while a neck is live.)
    snake.pending_detour = None
    return None


def _handle_detour(game: GameState, team: str, *, line: str | None = None) -> None:
    """Board a different line from the one declared — playable at any time.

    The line swapped to is the one the team will *next* board, so it has to serve
    wherever that boarding happens: the Anchor when idle, but the Front when a
    challenge is already under way (the team travels on from there once it
    completes). A mid-challenge detour therefore can't take effect yet — it is
    parked on ``Snake.pending_detour`` and overrides the line declared by the next
    ``complete_challenge``.
    """
    snake = game.get_snake(team)
    if snake.declared_line is None:
        raise ValueError(f"{team!r} has no declared line to detour from")
    if line is None or not game.map.has_line(line):
        raise ValueError(f"Unknown line for detour: {line!r}")
    boarding = snake.front if snake.neck_active else snake.anchor
    if not game.map.get_station(boarding).has_line(line):
        raise ValueError(f"{boarding!r} is not on line {line!r}")
    if snake.neck_active:
        snake.pending_detour = line
    else:
        snake.declared_line = line
    return None


def _handle_curse(game: GameState, team: str, *, target_team: str, curse_id: str | None = None) -> Curse:
    """Attach a curse the team already holds to another living team; return the Curse.

    The specific curse was drawn at *buy* time (see ``_on_buy_curse``), so this only
    moves it from the holder's hand to the target. ``curse_id`` picks which held curse
    to play; omitted, it plays the oldest one held (FIFO).
    """
    snake = game.get_snake(team)
    if target_team == team:
        raise ValueError("A curse must target another team")
    if target_team not in game.snakes:
        raise ValueError(f"Unknown team: {target_team!r}")
    if game.get_snake(target_team).eliminated:
        raise ValueError(f"{target_team!r} is already out of the game")
    if not snake.held_curses:
        raise ValueError(f"{team!r} holds no curse to play")
    if curse_id is None:
        curse = snake.held_curses[0]  # FIFO: the oldest curse still held
    else:
        curse = next((c for c in snake.held_curses if c.id == curse_id), None)
        if curse is None:
            raise ValueError(f"{team!r} does not hold a curse with id {curse_id!r}")
    snake.held_curses.remove(curse)
    game.get_snake(target_team).curses.append(curse)
    return curse


# --- Buy handlers ------------------------------------------------------------
# Optional, keyed by powerup id: an effect that happens at *purchase* time rather
# than when the card is played. Same contract as the play handlers — validate
# before mutating, raise ValueError on failure — and GameState.buy_powerup runs
# them only after every check passes, so a rejected buy consumes nothing.


def _on_buy_curse(game: GameState, team: str) -> Curse:
    """Draw a concrete curse into the buyer's hand; the deck loses it now, not at play."""
    if game.curse_deck is None:
        raise ValueError("No curse deck available")
    curse = game.curse_deck.draw(rng=game.rng)
    game.get_snake(team).held_curses.append(curse)
    return curse


POWERUP_ON_BUY: dict[str, Callable] = {
    "curse": _on_buy_curse,
}


POWERUP_HANDLERS: dict[str, Callable] = {
    "jump": _handle_jump,
    "efficiency": _handle_efficiency,
    "double_up": _handle_double_up,
    "retreat": _handle_retreat,
    "detour": _handle_detour,
    "curse": _handle_curse,
}

# The two tables must agree: `config.POWERUP_COSTS` defines which powerups exist
# (and so which are buyable and enabled by default), while POWERUP_HANDLERS defines
# what playing one does. An id in the costs table with no handler here would be sold
# happily and then fail with a bare KeyError when played — bypassing the ValueError
# contract every other failure path honours. Checked at import so a half-added
# powerup breaks loudly and immediately rather than mid-game.
if set(POWERUP_HANDLERS) != set(POWERUP_COSTS):
    raise RuntimeError(
        "Powerup registry mismatch — every id in config.POWERUP_COSTS needs a handler and vice versa: "
        f"priced without a handler {sorted(set(POWERUP_COSTS) - set(POWERUP_HANDLERS))}, "
        f"handled without a price {sorted(set(POWERUP_HANDLERS) - set(POWERUP_COSTS))}"
    )

# Buy handlers are optional, so this table is a *subset* of the costs table rather
# than a match — but a buy handler for an id nobody can buy is still a mistake.
if set(POWERUP_ON_BUY) - set(POWERUP_COSTS):
    raise RuntimeError(f"Buy handlers for unpriced powerups: {sorted(set(POWERUP_ON_BUY) - set(POWERUP_COSTS))}")
