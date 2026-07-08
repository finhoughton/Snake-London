from __future__ import annotations

import random
from dataclasses import dataclass, field

from challenges import Challenge, ChallengePool, get_difficulty, neck_weights
from config import (
    BONUS_AT_FRONT,
    BONUS_CLAIMED,
    DEFAULT_BONUS_CHANCE,
    DEFAULT_TEAM_COLORS,
    EASIER_REWARD,
    HARDER_REWARD,
    INITIAL_DIFFICULTY_MAX,
    INITIAL_DIFFICULTY_MIN,
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
    conceded: bool = False
    coins: int = 0
    offer: tuple[Challenge, Challenge] | None = None  # (easier, harder); identical entries during the initial phase

    @property
    def eliminated(self) -> bool:
        """Out of the game — either crashed or conceded."""
        return self.crashed or self.conceded


@dataclass
class GameState:
    map: Map
    snakes: dict[str, Snake]  # team -> Snake
    bonus_interchanges: set[str] = field(default_factory=set)  # interchanges that pay bonus coins
    challenges: ChallengePool | None = None  # pool the offers are drawn from (None = no challenges)
    rng: random.Random = field(default_factory=random.Random)  # drives challenge draws
    initial_challenge: Challenge | None = None  # shared initial challenge (default for every team, unless vetoed)

    # Snake access

    def get_snake(self, team: str) -> Snake:
        return self.snakes[team]

    def active_teams(self) -> list[str]:
        """Teams still in the game (not crashed or conceded)."""
        return [t for t, s in self.snakes.items() if not s.eliminated]

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
        """Body + active Neck — the opponent-side total in the win-lead comparison (see `winner`)."""
        return len(self.body_stations(team)) + len(self.neck(team))

    # Game events

    def _acting_snake(self, team: str) -> Snake:
        """Look up a snake for an action, rejecting teams that are out of the game."""
        snake = self.snakes[team]
        if snake.crashed:
            raise ValueError(f"{team!r} has crashed and can no longer act")
        if snake.conceded:
            raise ValueError(f"{team!r} has conceded and can no longer act")
        return snake

    def initial_request_challenge(self, team: str) -> None:
        """Request the initial challenge at the Origin.

        Used at the start of the game before the team has a declared line or has
        travelled anywhere. Activates the neck so that complete_challenge() can be
        called to declare the first line, and offers the game's shared
        `initial_challenge` — since there's no neck yet to size a difficulty from,
        every team gets the same challenge (drawn once, in `new_game`).
        """
        snake = self._acting_snake(team)
        if snake.declared_line is not None:
            raise ValueError(f"{team!r} has already completed their initial challenge")
        if snake.neck_active:
            raise ValueError(f"{team!r} already has an active challenge request")
        snake.neck_active = True
        self._sync_initial_offer(snake)

    def request_challenge(self, team: str, station: str) -> None:
        """Travel to an interchange and request a challenge there.

        checks:
          - The team has a declared line.
          - The target station is reachable from the Anchor on that line.

        Activates the Neck. If the path runs through any claimed interchange
        (your own or an opponent's), the move is legal but the neck is claimed,
        so the snake crashes immediately.
        """
        snake = self._acting_snake(team)
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

        # A neck that runs through any claimed interchange — your own or an
        # opponent's — crashes the snake. Requesting is still a legal move; the
        # crash is the consequence of the neck being claimed.
        path = self.map._path_between_on_line(snake.declared_line, snake.anchor, station)
        neck_is_claimed = any(self.map.is_claimed(interchange) for interchange in path[1:])

        snake.front = station
        snake.neck_active = True
        if neck_is_claimed:
            self.crash(team)
        else:
            self._draw_offer(team)

    def complete_challenge(self, team: str, next_line: str, *, hard: bool = False) -> list[str]:
        """Complete a challenge: claim the Neck, award coins, advance the Anchor, declare next line.

        ``hard`` selects which of the two offered challenges was completed — the
        easier one (default) pays EASIER_REWARD coins, the harder pays HARDER_REWARD.
        Each newly-claimed bonus interchange also pays out: BONUS_AT_FRONT if it is
        the Front (where the challenge was completed), else BONUS_CLAIMED.

        Returns the list of newly claimed interchanges.
        """
        snake = self._acting_snake(team)
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

        # Claiming these interchanges may have invaded another team's active neck,
        # which crashes that snake.
        self._apply_neck_crashes(exclude=team)

        # Award coins: the challenge reward plus any bonus interchanges just claimed.
        snake.coins += HARDER_REWARD if hard else EASIER_REWARD
        for station_key in segment:
            if station_key in self.bonus_interchanges:
                snake.coins += BONUS_AT_FRONT if station_key == snake.front else BONUS_CLAIMED

        snake.anchor = snake.front
        snake.neck_active = False
        snake.declared_line = next_line
        snake.offer = None
        return segment

    # Challenge offers

    def current_challenges(self, team: str) -> tuple[Challenge, Challenge] | None:
        """The two challenges currently offered to a team (easier, harder), or None.

        Both are live at once — the team completes whichever it likes (pass the
        matching ``hard`` to ``complete_challenge``). During the initial phase
        (before a line is declared) both entries are the same single challenge —
        there's no real easier/harder choice yet. That challenge is the game's
        shared `initial_challenge` by default, unless this team has vetoed it, in
        which case both entries are its own freshly-drawn replacement instead.
        """
        return self.snakes[team].offer

    def veto_challenges(self, team: str) -> None:
        """Veto the current challenge(s) and draw fresh one(s) for this team only.

        Also used after a *failed* challenge, which the rules treat like a veto.
        The 15-minute veto period itself is enforced by the caller (the Discord
        bot); the engine only refreshes the offer. Before a line is declared (the
        initial challenge), this draws a new challenge for the vetoing team alone
        — every other team keeps the game's shared `initial_challenge` unchanged.
        Afterwards (a normal challenge) it draws a fresh (easier, harder) pair
        sized to the requester's own neck, as always.
        """
        snake = self._acting_snake(team)
        if not snake.neck_active:
            raise ValueError(f"{team!r} has no active challenge to veto")
        if snake.declared_line is None:
            self._draw_new_initial_offer(snake)
        else:
            self._draw_offer(team)

    def _sync_initial_offer(self, snake: Snake) -> None:
        """Set a snake's offer to the game's shared initial challenge (both slots identical)."""
        snake.offer = (self.initial_challenge, self.initial_challenge) if self.initial_challenge else None

    def _draw_new_initial_offer(self, snake: Snake) -> None:
        """Draw a fresh initial challenge for one team after a veto.

        Only this team's offer changes — `GameState.initial_challenge` (the
        default every other, not-yet-vetoed team still shares) is left untouched.
        No-op (sets None) if the game has no challenge pool.
        """
        if self.challenges is None:
            snake.offer = None
            return
        challenge = self.challenges.pick_in_range(INITIAL_DIFFICULTY_MIN, INITIAL_DIFFICULTY_MAX, rng=self.rng)
        snake.offer = (challenge, challenge)

    def _draw_offer(self, team: str) -> None:
        """Draw the (easier, harder) pair for a team's current neck, sized by its difficulty.

        Only used once a line has been declared (i.e. after the initial
        challenge) — there's a real neck to measure by then. Difficulty is a
        function of the neck's length and the weights (approx. number of lines)
        of its interchanges (`get_difficulty` ∘ `neck_weights`). No-op if the game
        has no challenge pool.
        """
        if self.challenges is None:
            return
        snake = self.snakes[team]
        weights = neck_weights(self.map, snake.declared_line or "", self.neck(team))
        snake.offer = self.challenges.pair_for(get_difficulty(weights), rng=self.rng)

    def crash(self, team: str) -> None:
        """Mark a snake as crashed."""
        self.snakes[team].crashed = True

    def concede(self, team: str) -> None:
        """Concede the game — a voluntary loss (a loss path alongside crashing)."""
        snake = self.snakes[team]
        if snake.eliminated:
            raise ValueError(f"{team!r} is already out of the game")
        snake.conceded = True

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

    def _apply_neck_crashes(self, exclude: str) -> None:
        """Crash any other active-neck team whose neck now contains a claimed station.

        Called right after a team claims interchanges: a neck interchange that has
        *become* claimed by another team crashes that snake (the primary lose
        condition). Completions are resolved one at a time — i.e. in call /
        completion-timestamp order — so the first team to claim a contested
        interchange survives and the other crashes.
        """
        for other_team, other_snake in self.snakes.items():
            if other_team == exclude or other_snake.eliminated:
                continue
            if not self.is_neck_safe(other_team):
                self.crash(other_team)

    def winner(self) -> str | None:
        """Return the winning team if a win condition is met, otherwise None.

        Win conditions:
          1. All opponents are out (crashed or conceded).
          2. A team's claimed stations (Body) lead every opponent's Body + Neck by
             more than WINNING_THRESHOLD.
        """
        active = self.active_teams()
        if len(active) == 1:
            return active[0]
        for team in active:
            others = [t for t in active if t != team]
            ours = len(self.body_stations(team))
            if all(ours > self.total_controlled(o) + WINNING_THRESHOLD for o in others):
                return team
        return None

    def tiebreak_winner(self) -> str | None:
        """End-of-game tiebreaker: the active team with the most claimed stations (Body).

        For use when the time limit is reached (the clock itself is the bot's job).
        Only claimed stations count — necks don't. Returns None on an exact tie for
        the lead, or if no teams remain.
        """
        active = self.active_teams()
        if not active:
            return None
        counts = {t: len(self.body_stations(t)) for t in active}
        best = max(counts.values())
        leaders = [t for t, count in counts.items() if count == best]
        return leaders[0] if len(leaders) == 1 else None


def new_game(
    start_positions: dict[str, str],
    team_colors: dict[str, str] | None = None,
    connections_path: str = "map/connections.json",
    *,
    bonus_chance: float = DEFAULT_BONUS_CHANCE,
    bonus_interchanges: set[str] | frozenset[str] | None = None,
    challenge_pool: ChallengePool | None = None,
    challenges_path: str = "challenges.json",
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

    # One RNG drives both bonus selection and challenge drawing (seed via `rng`).
    picker = rng or random.Random()

    # Origins are never bonus interchanges, whether chosen randomly or passed in.
    origins = set(start_positions.values())
    if bonus_interchanges is None:
        bonus_interchanges = {s for s in game_map.station_keys() if s not in origins and picker.random() < bonus_chance}
    else:
        bonus_interchanges = set(bonus_interchanges) - origins

    if challenge_pool is None:
        # challenges.json is gitignored, so a missing file just means no offers.
        try:
            challenge_pool = ChallengePool(challenges_path)
        except FileNotFoundError:
            challenge_pool = None

    initial_challenge = (
        challenge_pool.pick_in_range(INITIAL_DIFFICULTY_MIN, INITIAL_DIFFICULTY_MAX, rng=picker)
        if challenge_pool is not None
        else None
    )

    return GameState(
        map=game_map,
        snakes=snakes,
        bonus_interchanges=set(bonus_interchanges),
        challenges=challenge_pool,
        rng=picker,
        initial_challenge=initial_challenge,
    )
