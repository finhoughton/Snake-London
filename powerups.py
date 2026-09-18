"""Powerups system

Teams buy powerups with coins into an unlimited hand and play them later. This
module owns the powerup *content* (the curse deck) and the registry of play
handlers. The game state (game.py) imports from here; this module never imports
game.py — handlers receive the game object as their first argument — so there is
no import cycle. Adding a future powerup is one entry in ``config.POWERUP_COSTS``
plus one handler in ``NORMAL_POWERUP_HANDLERS`` — and, if it does something at purchase
time rather than when played, an optional entry in ``POWERUP_ON_BUY``.
"""

from __future__ import annotations

import json
import random
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from config import CURSE_OPTIONS, POWERUP_COSTS
from jloxgame.state import Team

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
        return self.draw_options(1, rng=rng)[0]

    def draw_options(self, count: int, rng: random.Random | None = None) -> list[Curse]:
        """Draw up to ``count`` *distinct* curses to choose between.

        Fewer only when the deck knows fewer. A cycle that empties part-way through
        refills without what this draw already took, so one draw never offers a pair.
        """
        picker = rng if rng is not None else random
        if not self._catalog:
            raise ValueError("Cannot draw from a curse deck with no curses")
        drawn: list[Curse] = []
        for _ in range(min(count, len(self._catalog))):
            if not self._remaining:
                self._remaining = [c for c in self._catalog if c not in drawn]
            drawn.append(self._remaining.pop(picker.randrange(len(self._remaining))))
        return drawn

    def put_back(self, curse: Curse) -> None:
        """Return a curse that wasn't kept to the current cycle."""
        self._remaining.append(curse)


# --- Play handlers -----------------------------------------------------------
# Each handler is ``handler(game, team, **kwargs)``. Handlers validate *before*
# mutating anything and raise ValueError on bad input, so a failed play never
# consumes the card (`play_normal_powerup`/`play_jump`/`play_detour`/`play_curse` remove
# the card only after the handler returns). Curse returns the Curse it played; every
# other handler returns None.


def handle_jump(game: GameState, team: Team, *, station: str | None = None) -> None:
    """Make a station permanently passable for everyone (does not steal it)."""
    if station is None or not game.map.has_station(station):
        raise ValueError(f"Unknown station for jump: {station!r}")
    game.jumped_stations.add(station)


def _handle_efficiency(game: GameState, team: Team) -> None:
    """Arm a free veto. No stacking — ``free_vetoes`` is set to 1, never above."""
    game.get_snake(team).free_vetoes = 1


def _handle_retreat(game: GameState, team: Team) -> None:
    """Cancel the active request; the next request must go to a different station."""
    snake = game.get_snake(team)
    if not snake.neck_active:
        raise ValueError(f"{team!r} has no active challenge request to retreat")
    if snake.travel_line is None:
        raise ValueError(f"{team!r} cannot retreat the initial challenge")
    snake.blocked_station = snake.front
    snake.front = snake.anchor
    snake.neck_active = False
    snake.offer = None
    # A detour parked mid-challenge was validated against the Front the team is
    # now walking back from, so it lapses rather than carrying to a station it may
    # not even serve. (The Front cannot move any other way while a neck is live.)
    snake.pending_detour = None


def handle_detour(game: GameState, team: Team, *, line: str | None = None) -> None:
    """Board a different line from the one declared — playable at any time.

    The line swapped to is the one the team will *next* board, so it has to serve
    wherever that boarding happens: the Anchor when idle, but the Front when a
    challenge is already under way (the team travels on from there once it
    completes). A mid-challenge detour therefore can't take effect yet — it is
    parked on ``Snake.pending_detour`` and overrides the line declared by the next
    ``complete_challenge``.

    Only ``travel_line`` ever moves. ``announced_line`` keeps whatever was declared
    publicly, because the rules make this powerup's use unannounced — that gap between
    the two fields *is* the secret, and it closes on its own at the next challenge
    request, when the neck (and so the real line) becomes public anyway.
    """
    snake = game.get_snake(team)
    if snake.travel_line is None:
        raise ValueError(f"{team!r} has no declared line to detour from")
    if line is None or not game.map.has_line(line):
        raise ValueError(f"Unknown line for detour: {line!r}")
    boarding = snake.front if snake.neck_active else snake.anchor
    if not game.map.get_station(boarding).has_line(line):
        raise ValueError(f"{boarding!r} is not on line {line!r}")
    if snake.neck_active:
        snake.pending_detour = line
    else:
        snake.travel_line = line


def handle_curse(game: GameState, team: Team, *, target_team: Team, curse_id: str | None = None) -> Curse:
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


def _on_buy_curse(game: GameState, team: Team) -> list[Curse]:
    """Draw the curses the buyer chooses between; the deck loses them now, not at play.

    The team keeps one with ``GameState.choose_curse`` and the rest go back. A deck
    that knows only one curse offers no choice, so that one is kept immediately.
    """
    if game.curse_deck is None:
        raise ValueError("No curse deck available")
    snake = game.get_snake(team)
    if snake.curse_choice:
        raise ValueError(f"{team!r} must keep one of the curses already drawn first")
    drawn = game.curse_deck.draw_options(CURSE_OPTIONS, rng=game.rng)
    if len(drawn) == 1:
        snake.held_curses.append(drawn[0])
    else:
        snake.curse_choice = drawn
    return drawn


POWERUP_ON_BUY: dict[str, Callable[[GameState, Team], list[Curse]]] = {
    "curse": _on_buy_curse,
}


NORMAL_POWERUP_HANDLERS: dict[str, Callable[[GameState, Team], None]] = {
    "efficiency": _handle_efficiency,
    "retreat": _handle_retreat,
}

# `config.POWERUP_COSTS` defines which powerups exist (and so which are buyable and
# enabled by default); NORMAL_POWERUP_HANDLERS defines what playing a parameterless one
# does. This is a *subset* check, not a match: jump, detour and curse are priced here but
# played through their own events, so they have no entry above. What it does catch is a
# handler with no price — unbuyable, so unplayable. Checked at import so a half-added
# powerup breaks loudly and immediately rather than mid-game.
if set(NORMAL_POWERUP_HANDLERS) - set(
    POWERUP_COSTS
):  # anshul: changed to be a subset! avoiding using this messily-typed lookup table
    raise RuntimeError(
        "Powerup registry mismatch — every handler needs a price in config.POWERUP_COSTS: "
        f"handled without a price {sorted(set(NORMAL_POWERUP_HANDLERS) - set(POWERUP_COSTS))}"
    )

# Buy handlers are optional, so this table is a *subset* of the costs table rather
# than a match — but a buy handler for an id nobody can buy is still a mistake.
if set(POWERUP_ON_BUY) - set(POWERUP_COSTS):
    raise RuntimeError(f"Buy handlers for unpriced powerups: {sorted(set(POWERUP_ON_BUY) - set(POWERUP_COSTS))}")
