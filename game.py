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
    POWERUP_COSTS,
    STARTING_COINS,
    WINNING_THRESHOLD,
)
from map import Map
from powerups import POWERUP_HANDLERS, POWERUP_ON_BUY, Curse, CurseDeck


@dataclass
class Snake:
    team: str
    origin: str
    anchor: str
    front: str
    color: str = "#888888"  # hex color for this team's claimed stations
    # The line a snake is on is two separate facts, because Detour is secret. Everything
    # the engine *does* (necks, travel validation, segment claiming) keys off travel_line;
    # everything the public is told keys off announced_line. They match until a Detour is
    # played, and complete_challenge resets both. Never show travel_line to opponents.
    travel_line: str | None = None  # the line actually boarded; None until the initial challenge
    announced_line: str | None = None  # the line declared to the other teams (public info)
    neck_active: bool = False  # True during challenge attempt, False otherwise
    crashed: bool = False
    conceded: bool = False
    coins: int = 0
    offer: tuple[Challenge, Challenge] | None = None  # (easier, harder); identical entries during the initial phase
    # --- Powerups ---
    hand: list[str] = field(default_factory=list)  # powerup ids held; duplicates allowed, no limit
    free_vetoes: int = 0  # 1 while a free (Efficiency) veto is armed; never above 1
    double_up_remaining: int = 0  # completions left with a doubled reward (0-2)
    blocked_station: str | None = None  # set by Retreat; the next request must differ
    pending_detour: str | None = None  # Detour played mid-challenge; overrides the next declared line
    held_curses: list[Curse] = field(default_factory=list)  # curses bought and not yet played
    curses: list[Curse] = field(default_factory=list)  # curses inflicted on this team by others

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
    rng: random.Random = field(default_factory=random.Random)  # drives challenge and curse draws
    initial_challenge: Challenge | None = None  # shared initial challenge (default for every team, unless vetoed)
    # --- Powerups ---
    enabled_powerups: set[str] = field(default_factory=set)  # powerup ids buyable this game
    jumped_stations: set[str] = field(default_factory=set)  # globally, permanently passable (all players)
    curse_deck: CurseDeck | None = None  # deck the curse powerup draws from (None = curse unavailable)

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
        if snake.travel_line is None:
            raise ValueError(f"{team!r} has no declared line")
        path = self.map._path_between_on_line(snake.travel_line, snake.anchor, snake.front)
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
        if snake.travel_line is not None:
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
        if snake.travel_line is None:
            raise ValueError(f"{team!r} has no declared line — use initial_request_challenge() first")
        if not self.map.has_station(station):
            raise ValueError(f"Unknown station: {station!r}")
        if not self.map.get_station(station).has_line(snake.travel_line):
            raise ValueError(f"{station!r} is not on line {snake.travel_line!r}")
        if station == snake.anchor:
            raise ValueError(f"{station!r} is the current Anchor — travel to a different interchange")
        if snake.neck_active:
            raise ValueError(f"{team!r} already has an active challenge request")
        if snake.blocked_station is not None and station == snake.blocked_station:
            raise ValueError(f"{station!r} was just retreated from — request a different interchange")

        # A neck that runs through any claimed interchange — your own or an
        # opponent's — crashes the snake, unless the interchange has been jumped
        # (jumping makes it passable). Requesting is still a legal move; the
        # crash is the consequence of the neck being claimed.
        path = self.map._path_between_on_line(snake.travel_line, snake.anchor, station)
        neck_is_claimed = any(self._blocks_travel(interchange) for interchange in path[1:])

        snake.blocked_station = None  # any successful request clears the retreat block
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

        The *initial* challenge is the exception: it pays nothing at all (there is
        only ever one challenge on offer, so ``hard`` is meaningless there), and it
        leaves a Double up armed rather than spending a charge on a zero reward.

        Returns the list of newly claimed interchanges.
        """
        snake = self._acting_snake(team)
        if not snake.neck_active:
            raise ValueError(f"{team!r} has no active challenge request")
        is_initial = snake.travel_line is None
        if not self.map.has_line(next_line):
            raise ValueError(f"Unknown line: {next_line!r}")
        if not self.map.get_station(snake.front).has_line(next_line):
            raise ValueError(f"Front interchange {snake.front!r} is not on line {next_line!r}")

        segment = self.neck(team)
        if not segment:
            # Initial challenge: snake hasn't moved yet, claim the origin station
            segment = [snake.front]
        newly_claimed: list[str] = []
        for station_key in segment:
            # A jumped station can leave another team's claim inside the neck; the
            # original owner keeps it (jump affects passability, not ownership) —
            # so skip it, and it never counts toward this team's Body or bonuses.
            existing = self.map.get_claim(station_key)
            if existing is not None and existing != team:
                continue
            if existing is None:
                newly_claimed.append(station_key)
            self.map.claim(station_key, team)

        # Record which line segments were claimed (the full path, as always).
        if snake.travel_line:
            full_path = [snake.anchor] + segment
            for i in range(len(full_path) - 1):
                station_a, station_b = full_path[i], full_path[i + 1]
                # Same rule as the interchange loop above: track another team already
                # owns stays theirs. Only reachable by travelling through a jumped
                # interchange — any other route over their track would have crashed
                # this snake before it got here.
                if self.map.get_segment_claim(snake.travel_line, station_a, station_b) not in (None, team):
                    continue
                self.map.claim_segment(snake.travel_line, station_a, station_b, team)

        # Claiming these interchanges may have invaded another team's active neck,
        # which crashes that snake.
        self._apply_neck_crashes(exclude=team)

        # Award coins: the challenge reward (doubled while Double up is armed) plus
        # any bonus interchanges just claimed. Bonus coins are never doubled. The
        # initial challenge pays neither — it only unlocks the first line — and it
        # spends no Double up charge, since there is no reward to double.
        if not is_initial:
            reward = HARDER_REWARD if hard else EASIER_REWARD
            if snake.double_up_remaining > 0:
                reward *= 2
                snake.double_up_remaining -= 1
            snake.coins += reward
            for station_key in newly_claimed:
                if station_key in self.bonus_interchanges:
                    snake.coins += BONUS_AT_FRONT if station_key == snake.front else BONUS_CLAIMED

        snake.anchor = snake.front
        snake.neck_active = False
        # Declaring a line is public and resets both facts: whatever secret line the
        # team was travelling on, they have now announced this one and are on it.
        snake.announced_line = next_line
        snake.travel_line = next_line
        snake.offer = None
        # A Detour played during this challenge silently replaces the line actually
        # boarded — it was validated against the Front, which is now the Anchor. The
        # announcement above is left standing: that is exactly what makes it secret.
        if snake.pending_detour is not None:
            snake.travel_line = snake.pending_detour
            snake.pending_detour = None
        return newly_claimed

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

    def veto_challenges(self, team: str) -> bool:
        """Veto the current challenge(s) and draw fresh one(s) for this team only.

        Also used after a *failed* challenge, which the rules treat like a veto.
        The 15-minute veto period itself is enforced by the caller (the Discord
        bot); the engine only refreshes the offer. Before a line is declared (the
        initial challenge), this draws a new challenge for the vetoing team alone
        — every other team keeps the game's shared `initial_challenge` unchanged.
        Afterwards (a normal challenge) it draws a fresh (easier, harder) pair
        sized to the requester's own neck, as always.

        Returns True if a free (Efficiency) veto charge was consumed — the bot then
        skips the 15-minute veto period — else False for a normal, timed veto.
        """
        snake = self._acting_snake(team)
        if not snake.neck_active:
            raise ValueError(f"{team!r} has no active challenge to veto")
        free = snake.free_vetoes > 0
        if free:
            snake.free_vetoes = 0
        if snake.travel_line is None:
            self._draw_new_initial_offer(snake)
        else:
            self._draw_offer(team)
        return free

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
        weights = neck_weights(self.map, snake.travel_line or "", self.neck(team))
        snake.offer = self.challenges.pair_for(get_difficulty(weights), rng=self.rng)

    # Powerups

    def buy_powerup(self, team: str, powerup_id: str) -> Curse | None:
        """Buy a powerup into the team's hand, deducting its coin cost.

        Raises ValueError if the team is out of the game, the id is unknown, the
        powerup is not enabled this game, or the team can't afford it. (A missing
        curse deck already strips ``"curse"`` from ``enabled_powerups`` at
        ``new_game``, so no separate deck check is needed here.)

        Returns whatever the powerup's buy-time effect produced — for ``"curse"``
        that's the concrete ``Curse`` drawn into ``Snake.held_curses``, so the buyer
        knows what they're holding before they play it; None for everything else.
        """
        snake = self._acting_snake(team)
        if powerup_id not in POWERUP_COSTS:
            raise ValueError(f"Unknown powerup: {powerup_id!r}")
        if powerup_id not in self.enabled_powerups:
            raise ValueError(f"Powerup {powerup_id!r} is not enabled in this game")
        cost = POWERUP_COSTS[powerup_id]
        if snake.coins < cost:
            raise ValueError(f"Not enough coins to buy {powerup_id!r}: need {cost}, have {snake.coins}")
        # Buy-time effects run only once every check has passed, so a rejected
        # purchase never consumes deck content (a drawn curse would be lost).
        on_buy = POWERUP_ON_BUY.get(powerup_id)
        acquired = on_buy(self, team) if on_buy is not None else None
        snake.coins -= cost
        snake.hand.append(powerup_id)
        return acquired

    def play_powerup(self, team: str, powerup_id: str, **kwargs) -> Curse | None:
        """Play a powerup from the team's hand, dispatching to its handler.

        The card is removed from the hand only *after* the handler returns, so a
        failed play (the handler raises ValueError on bad input) keeps the card.
        Returns the handler's result — the ``Curse`` played for ``"curse"``, else None.

        ``"curse"`` takes ``target_team=`` plus an optional ``curse_id=`` selecting
        which held curse to play (default: the oldest held). The curse itself was
        drawn when it was bought, so playing one never touches the deck.

        ``"detour"`` takes ``line=``. Played at the Anchor it swaps ``travel_line``
        outright; played mid-challenge it parks on ``Snake.pending_detour`` and takes
        effect when the current challenge completes (see ``complete_challenge``).
        Either way ``announced_line`` is untouched — Detour is not announced.
        """
        snake = self._acting_snake(team)
        if powerup_id not in snake.hand:
            raise ValueError(f"{powerup_id!r} is not in {team!r}'s hand")
        result = POWERUP_HANDLERS[powerup_id](self, team, **kwargs)
        snake.hand.remove(powerup_id)
        return result

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

    def _blocks_travel(self, station: str) -> bool:
        """Whether a station a neck runs through is fatal to traverse.

        A claimed interchange (your own or an opponent's) blocks travel, unless it
        has been jumped — a jumped station is permanently passable for everyone.
        """
        return self.map.is_claimed(station) and station not in self.jumped_stations

    def is_neck_safe(self, team: str) -> bool:
        """Return True if no un-jumped interchange in the Neck is claimed by another team."""
        snake = self.snakes[team]
        if not snake.neck_active:
            return True
        for station_key in self.neck(team):
            if station_key in self.jumped_stations:
                continue
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
    enabled_powerups: set[str] | None = None,
    curse_deck: CurseDeck | None = None,
    curses_path: str = "curses.json",
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
            travel_line=None,
            announced_line=None,
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
        # A missing challenges.json is tolerated rather than fatal: a game with no
        # offers is still a playable board (and every test that supplies its own pool
        # never touches the repo file).
        try:
            challenge_pool = ChallengePool(challenges_path)
        except FileNotFoundError:
            challenge_pool = None

    initial_challenge = (
        challenge_pool.pick_in_range(INITIAL_DIFFICULTY_MIN, INITIAL_DIFFICULTY_MAX, rng=picker)
        if challenge_pool is not None
        else None
    )

    if curse_deck is None:
        # curses.json gets the same treatment as challenges.json: a missing file
        # just means no curse deck (which in turn disables the curse powerup).
        try:
            curse_deck = CurseDeck(curses_path)
        except FileNotFoundError:
            curse_deck = None

    # A deck with no curses in it counts as no deck at all — otherwise the powerup
    # would stay enabled and sell a card that can never be played.
    if curse_deck is not None and len(curse_deck) == 0:
        curse_deck = None

    # None enables every known powerup; otherwise honour the given set. A missing
    # curse deck strips "curse" either way — it's unusable without content.
    enabled = set(POWERUP_COSTS) if enabled_powerups is None else set(enabled_powerups)
    if curse_deck is None:
        enabled.discard("curse")

    return GameState(
        map=game_map,
        snakes=snakes,
        bonus_interchanges=set(bonus_interchanges),
        challenges=challenge_pool,
        rng=picker,
        initial_challenge=initial_challenge,
        enabled_powerups=enabled,
        jumped_stations=set(),
        curse_deck=curse_deck,
    )
