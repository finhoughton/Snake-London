from __future__ import annotations

import random
from dataclasses import dataclass, field

from config import (
    BONUS_AT_FRONT,
    BONUS_CLAIMED,
    DEFAULT_BONUS_CHANCE,
    DEFAULT_TEAM_COLORS,
    EASIER_REWARD,
    HARDER_REWARD,
    STARTING_COINS,
    WINNING_THRESHOLD,
)
from map import Map


@dataclass
class Snake:
    team: str
    origin: str
    anchor: str
    front: str
    color: str = "#888888"  # hex color for this team's claimed stations
    declared_line: str | None = None  # line declared after last challenge
    neck_active: bool = False  # True during challenge attempt, False otherwise
    crashed: bool = False
    coins: int = 0


@dataclass
class GameState:
    map: Map
    snakes: dict[str, Snake]  # team -> Snake
    bonus_interchanges: set[str] = field(default_factory=set)  # interchanges that pay bonus coins

    # Snake access

    def get_snake(self, team: str) -> Snake:
        return self.snakes[team]

    def active_teams(self) -> list[str]:
        """Teams whose snakes have not crashed."""
        return [t for t, s in self.snakes.items() if not s.crashed]

    # Neck / body queries

    def neck(self, team: str) -> list[str]:
        """Path from Anchor to Front (anchor excluded, front included).

        Returns an empty list when the snake is at its Anchor.
        """
        snake = self.snakes[team]
        if snake.front == snake.anchor:
            return []
        if snake.declared_line is None:
            raise ValueError(f"{team!r} has no declared line")
        path = self.map._path_between_on_line(snake.declared_line, snake.anchor, snake.front)
        return path[1:]  # exclude anchor

    def body_stations(self, team: str) -> list[str]:
        """All interchanges currently in the snake's Body (claimed stations)."""
        return self.map.stations_claimed_by(team)

    def total_controlled(self, team: str) -> int:
        """Body + active Neck — used for win-condition comparison."""
        return len(self.body_stations(team)) + len(self.neck(team))

    # Game events

    def initial_request_challenge(self, team: str) -> None:
        """Request the initial challenge at the Origin.

        Used at the start of the game before the team has a declared line or
        has travelled anywhere.  Simply activates the neck so that
        complete_challenge() can be called to declare the first line.
        """
        snake = self.snakes[team]
        if snake.declared_line is not None:
            raise ValueError(f"{team!r} has already completed their initial challenge")
        if snake.neck_active:
            raise ValueError(f"{team!r} already has an active challenge request")
        snake.neck_active = True

    def request_challenge(self, team: str, station: str) -> None:
        """Travel to an interchange and request a challenge there.

        checks:
          - The team has a declared line.
          - The target station is reachable from the Anchor on that line.
          - The path does not pass through any claimed interchanges.

        Activates the Neck, making it visible and vulnerable to other teams.
        """
        snake = self.snakes[team]

        if snake.declared_line is None:
            raise ValueError(f"{team!r} has no declared line — use initial_request_challenge() first")
        if not self.map.has_station(station):
            raise ValueError(f"Unknown station: {station!r}")
        if not self.map.get_station(station).has_line(snake.declared_line):
            raise ValueError(f"{station!r} is not on line {snake.declared_line!r}")
        if station == snake.anchor:
            raise ValueError(f"{station!r} is the current Anchor — travel to a different interchange")
        if snake.neck_active:
            raise ValueError(f"{team!r} already has an active challenge request")

        path = self.map._path_between_on_line(snake.declared_line, snake.anchor, station)
        for interchange in path[1:]:
            if self.map.is_claimed(interchange):
                claimant = self.map.get_claim(interchange)
                raise ValueError(
                    f"Path to {station!r} passes through claimed interchange {interchange!r} (owned by {claimant!r})"
                )

        snake.front = station
        snake.neck_active = True

    def complete_challenge(self, team: str, next_line: str, *, hard: bool = False) -> list[str]:
        """Complete a challenge: claim the Neck, award coins, advance the Anchor, declare next line.

        ``hard`` selects which of the two offered challenges was completed — the
        easier one (default) pays EASIER_REWARD coins, the harder pays HARDER_REWARD.
        Each newly-claimed bonus interchange also pays out: BONUS_AT_FRONT if it is
        the Front (where the challenge was completed), else BONUS_CLAIMED.

        Returns the list of newly claimed interchanges.
        """
        snake = self.snakes[team]
        if not snake.neck_active:
            raise ValueError(f"{team!r} has no active challenge request")
        if not self.map.has_line(next_line):
            raise ValueError(f"Unknown line: {next_line!r}")
        if not self.map.get_station(snake.front).has_line(next_line):
            raise ValueError(f"Front interchange {snake.front!r} is not on line {next_line!r}")

        segment = self.neck(team)
        if not segment:
            # Initial challenge: snake hasn't moved yet, claim the origin station
            segment = [snake.front]
        for station_key in segment:
            self.map.claim(station_key, team)

        # Record which line segments were claimed
        if snake.declared_line:
            full_path = [snake.anchor] + segment
            for i in range(len(full_path) - 1):
                self.map.claim_segment(snake.declared_line, full_path[i], full_path[i + 1], team)

        # Award coins: the challenge reward plus any bonus interchanges just claimed.
        snake.coins += HARDER_REWARD if hard else EASIER_REWARD
        for station_key in segment:
            if station_key in self.bonus_interchanges:
                snake.coins += BONUS_AT_FRONT if station_key == snake.front else BONUS_CLAIMED

        snake.anchor = snake.front
        snake.neck_active = False
        snake.declared_line = next_line
        return segment

    def crash(self, team: str) -> None:
        """Mark a snake as crashed."""
        self.snakes[team].crashed = True

    # Crash detection

    def is_neck_safe(self, team: str) -> bool:
        """Return True if no interchange in the Neck is claimed by another team."""
        snake = self.snakes[team]
        if not snake.neck_active:
            return True
        for station_key in self.neck(team):
            claim = self.map.get_claim(station_key)
            if claim is not None and claim != team:
                return False
        return True

    def winner(self) -> str | None:
        """Return the winning team if a win condition is met, otherwise None.

        Win conditions:
          1. All opponents have crashed.
          2. A team has >= WIN_LEAD_THRESHOLD more controlled interchanges than every opponent.
        """
        active = self.active_teams()
        if len(active) == 1:
            return active[0]
        for team in active:
            others = [t for t in active if t != team]
            ours = self.total_controlled(team)
            if all(ours > self.total_controlled(o) + WINNING_THRESHOLD for o in others):
                return team
        return None


def new_game(
    start_positions: dict[str, str],
    team_colors: dict[str, str] | None = None,
    connections_path: str = "map/connections.json",
    *,
    bonus_chance: float = DEFAULT_BONUS_CHANCE,
    bonus_interchanges: set[str] | frozenset[str] | None = None,
    rng: random.Random | None = None,
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
    """
    if not start_positions:
        raise ValueError("At least one team is required")

    game_map = Map(connections_path)

    for team, station in start_positions.items():
        if not game_map.has_station(station):
            raise ValueError(f"Unknown start station for {team!r}: {station!r}")

    if len(start_positions) != len(set(start_positions.values())):
        raise ValueError("Teams must not start at the same interchange")

    colors = team_colors or {}
    default_color_iter = iter(DEFAULT_TEAM_COLORS)
    snakes = {
        team: Snake(
            team=team,
            origin=station,
            anchor=station,
            front=station,
            color=colors.get(team) or next(default_color_iter, "#888888"),
            declared_line=None,
            coins=STARTING_COINS,
        )
        for team, station in start_positions.items()
    }

    # Origins are never bonus interchanges, whether chosen randomly or passed in.
    origins = set(start_positions.values())
    if bonus_interchanges is None:
        picker = rng or random.Random()
        bonus_interchanges = {s for s in game_map.station_keys() if s not in origins and picker.random() < bonus_chance}
    else:
        bonus_interchanges = set(bonus_interchanges) - origins

    return GameState(map=game_map, snakes=snakes, bonus_interchanges=set(bonus_interchanges))
