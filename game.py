from __future__ import annotations

import re
import traceback
from dataclasses import dataclass, field

from discord import ApplicationContext, Colour, Interaction, SelectOption
from discord.ui import DesignerModal, InputText, Label, StringSelect, TextDisplay

import jloxgame
from challenges import Challenge, ChallengePool, get_difficulty, neck_weights
from config import (
    BONUS_AT_FRONT,
    BONUS_CLAIMED,
    CHALLENGES_PATH,
    CONNECTIONS_PATH,
    CURSES_PATH,
    DECLARE_WIN_COOLDOWN_MINUTES,
    DECLARE_WIN_COST,
    DECLARE_WIN_WINDOW_MINUTES,
    DEFAULT_BONUS_CHANCE,
    DEFAULT_TEAM_COLORS,
    EASIER_REWARD,
    HARDER_REWARD,
    INITIAL_DIFFICULTY_MAX,
    INITIAL_DIFFICULTY_MIN,
    OBJECTIVE_COINS,
    OBJECTIVE_INTERVAL_MINUTES,
    OBJECTIVE_PASS_COINS,
    OBJECTIVE_STATIONS,
    POWERUP_COSTS,
    POWERUP_NAMES,
    STARTING_COINS,
    WINNING_THRESHOLD,
)
from jloxgame import GameContext, Team
from jloxgame.state import Status, event
from network import Map
from objectives import choose_objective
from powerups import NORMAL_POWERUP_HANDLERS, POWERUP_ON_BUY, Curse, CurseDeck, handle_curse, handle_detour, handle_jump


@dataclass
class Snake:
    origin: str
    anchor: str
    front: str

    vetoed: bool = False

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
    hand: list[str] = field(default_factory=list[str])  # powerup ids held; duplicates allowed, no limit
    free_vetoes: int = 0  # 1 while a free (Efficiency) veto is armed; never above 1
    blocked_station: str | None = None  # set by Retreat; the next request must differ
    pending_detour: str | None = None  # Detour played mid-challenge; overrides the next declared line
    held_curses: list[Curse] = field(default_factory=list[Curse])  # curses bought and not yet played
    curse_choice: list[Curse] = field(default_factory=list[Curse])  # drawn on buy, waiting to be kept
    curses: list[Curse] = field(default_factory=list[Curse])  # curses inflicted on this team by others
    # Ids of every challenge ever offered to this team (both slots, vetoed offers and the
    # shared initial challenge included). Offers skip these while alternatives exist.
    # Rebuilt by replaying the event log, so it never needs saving.
    seen_challenges: set[str] = field(default_factory=set[str])
    # --- Declaring a win ---
    win_declared: bool = False  # a declaration is waiting for its check (see GameState.declare_win)
    declare_cooldown: bool = False  # a declaration failed recently; can't declare again yet
    # --- Objectives ---
    objectives_won: int = 0  # each adds OBJECTIVE_STATIONS to the score

    @property
    def eliminated(self) -> bool:
        """Out of the game — either crashed or conceded."""
        return self.crashed or self.conceded


class GameState(GameContext):
    def __init__(self) -> None:
        super().__init__()

        self.map: Map = Map(CONNECTIONS_PATH)
        self.snakes: dict[Team, Snake] = {}  # Team -> Snake
        self.bonus_interchanges: set[str] = set()  # interchanges that pay bonus coins
        self.challenges: ChallengePool | None = None  # pool the offers are drawn from (None = no challenges)
        self.initial_challenge: Challenge | None = (
            None  # shared initial challenge (default for every team, unless vetoed)
        )
        # --- Powerups ---
        self.enabled_powerups: set[str] = set(POWERUP_COSTS.keys())  # powerup ids buyable this game
        self.jumped_stations: set[str] = set()  # globally, permanently passable (all players)
        self.curse_deck: CurseDeck | None = None  # deck the curse powerup draws from (None = curse unavailable)
        # --- Winning ---
        self.declared_winner: Team | None = None  # set by a passed declaration, or by the time limit
        self.objectives: list[str] = []  # live objectives, oldest first

        self.latest_generated_map = 0  # not synced

        try:
            self.challenges = ChallengePool(CHALLENGES_PATH)
        except FileNotFoundError:
            self.challenges = None

        try:
            self.curse_deck = CurseDeck(CURSES_PATH)
            # A deck with no curses in it counts as no deck at all
            if len(self.curse_deck) == 0:
                self.curse_deck = None
        except FileNotFoundError:
            self.curse_deck = None

        self.bonus_chance = DEFAULT_BONUS_CHANCE

        self.teams = []

    # Snake access

    def get_snake(self, team: Team) -> Snake:
        return self.snakes[team]

    def active_teams(self) -> list[Team]:
        """Teams still in the game (not crashed or conceded)."""
        return [t for t, s in self.snakes.items() if not s.eliminated]

    # Neck / body queries

    def neck(self, team: Team) -> list[str]:
        """Path from Anchor to Front (anchor excluded, front included).

        Returns an empty list when the snake is at its Anchor.
        """
        snake = self.get_snake(team)
        if snake.front == snake.anchor:
            return []
        if snake.travel_line is None:
            raise ValueError(f"{team!r} has no declared line")
        path = self.map.path_between_on_line(snake.travel_line, snake.anchor, snake.front)
        return path[1:]  # exclude anchor

    def body_stations(self, team: Team) -> list[str]:
        """All interchanges currently in the snake's Body (claimed stations)."""
        return self.map.stations_claimed_by(team)

    def score(self, team: Team) -> int:
        """Claimed stations plus OBJECTIVE_STATIONS per objective won."""
        return len(self.body_stations(team)) + OBJECTIVE_STATIONS * self.get_snake(team).objectives_won

    def total_controlled(self, team: Team) -> int:
        """Score + active Neck — the opponent-side total in the win-lead comparison (see `has_winning_lead`)."""
        return self.score(team) + len(self.neck(team))

    # Game events

    def _acting_snake(self, team: Team) -> Snake:
        """Look up a snake for an action, rejecting teams that are out of the game."""
        snake = self.get_snake(team)
        if snake.crashed:
            raise ValueError(f"{team!r} has crashed and can no longer act")
        if snake.conceded:
            raise ValueError(f"{team!r} has conceded and can no longer act")
        return snake

    def initial_request_challenge(self, team: Team) -> None:
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

    @event()
    def request_challenge(self, team_id: int, station: str) -> None:
        """Travel to an interchange and request a challenge there.

        checks:
          - The team has a declared line.
          - The target station is reachable from the Anchor on that line.

        Activates the Neck. If the path runs through any claimed interchange
        (your own or an opponent's), the move is legal but the neck is claimed,
        so the snake crashes immediately.
        """
        team = self.get_team(team_id)
        snake = self._acting_snake(team)
        if self.status != Status.RUNNING:
            raise ValueError("The game is not running")
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
        path = self.map.path_between_on_line(snake.travel_line, snake.anchor, station)
        neck_is_claimed = any(self._blocks_travel(interchange) for interchange in path[1:])

        snake.blocked_station = None  # any successful request clears the retreat block
        snake.front = station
        snake.neck_active = True
        if neck_is_claimed:
            self.crash(team)
        else:
            self._draw_offer(team)

    @event()
    def complete_challenge(self, team_id: int, next_line: str, *, hard: bool = False) -> list[str]:
        """Complete a challenge: claim the Neck, award coins, advance the Anchor, declare next line.

        ``hard`` selects which of the two offered challenges was completed — the
        easier one (default) pays EASIER_REWARD coins, the harder pays HARDER_REWARD.
        Each newly-claimed bonus interchange also pays out: BONUS_AT_FRONT if it is
        the Front (where the challenge was completed), else BONUS_CLAIMED.

        The *initial* challenge is the exception: it pays nothing at all (there is
        only ever one challenge on offer, so ``hard`` is meaningless there).

        Returns the list of newly claimed interchanges.
        """
        team = self.get_team(team_id)
        snake = self._acting_snake(team)
        if self.status != Status.RUNNING:
            raise ValueError("The game is not running")
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

        # Award coins: the challenge reward plus any bonus interchanges and objectives
        # just claimed. The initial challenge pays nothing — it only unlocks the first line.
        if not is_initial:
            snake.coins += HARDER_REWARD if hard else EASIER_REWARD
            for station_key in newly_claimed:
                if station_key in self.bonus_interchanges:
                    snake.coins += BONUS_AT_FRONT if station_key == snake.front else BONUS_CLAIMED
                if station_key in self.objectives:
                    self.objectives.remove(station_key)
                    if station_key == snake.front:
                        snake.coins += OBJECTIVE_COINS
                        snake.objectives_won += 1
                    else:
                        snake.coins += OBJECTIVE_PASS_COINS

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

    def current_challenges(self, team: Team) -> tuple[Challenge, Challenge] | None:
        """The two challenges currently offered to a team (easier, harder), or None.

        Both are live at once — the team completes whichever it likes (pass the
        matching ``hard`` to ``complete_challenge``). During the initial phase
        (before a line is declared) both entries are the same single challenge —
        there's no real easier/harder choice yet. That challenge is the game's
        shared `initial_challenge` by default, unless this team has vetoed it, in
        which case both entries are its own freshly-drawn replacement instead.
        """
        return self.get_snake(team).offer

    @event()
    def veto_challenges(self, team_id: int) -> bool:
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
        team = self.get_team(team_id)
        snake = self._acting_snake(team)
        if self.status != Status.RUNNING:
            raise ValueError("The game is not running")
        if not snake.neck_active:
            raise ValueError(f"{team!r} has no active challenge to veto")
        free = snake.free_vetoes > 0
        if free:
            snake.free_vetoes = 0
        else:
            snake.vetoed = True
        if snake.travel_line is None:
            self._draw_new_initial_offer(snake)
        else:
            self._draw_offer(team)
        return free

    async def unveto_callback(self, team_id: int):
        team = self.get_team(team_id)
        if team.thread:
            await team.thread.send("Your veto period has expired!")

    @event(callback=unveto_callback)
    def unveto(self, team_id: int) -> None:
        team = self.get_team(team_id)
        snake = self._acting_snake(team)
        if self.status != Status.RUNNING:
            raise ValueError("The game is not running")

        snake.vetoed = False

    @staticmethod
    def _mark_seen(snake: Snake) -> None:
        """Record the current offer as shown to this team, so it isn't offered again."""
        if snake.offer is not None:
            snake.seen_challenges.update(c.id for c in snake.offer)

    def _sync_initial_offer(self, snake: Snake) -> None:
        """Set a snake's offer to the game's shared initial challenge (both slots identical)."""
        snake.offer = (self.initial_challenge, self.initial_challenge) if self.initial_challenge else None
        self._mark_seen(snake)

    def _draw_new_initial_offer(self, snake: Snake) -> None:
        """Draw a fresh initial challenge for one team after a veto.

        Only this team's offer changes — `GameState.initial_challenge` (the
        default every other, not-yet-vetoed team still shares) is left untouched.
        No-op (sets None) if the game has no challenge pool.
        """
        if self.challenges is None:
            snake.offer = None
            return
        challenge = self.challenges.pick_in_range(
            INITIAL_DIFFICULTY_MIN, INITIAL_DIFFICULTY_MAX, rng=self.rng, exclude=snake.seen_challenges
        )
        snake.offer = (challenge, challenge)
        self._mark_seen(snake)

    def _draw_offer(self, team: Team) -> None:
        """Draw the (easier, harder) pair for a team's current neck, sized by its difficulty.

        Only used once a line has been declared (i.e. after the initial
        challenge) — there's a real neck to measure by then. Difficulty is a
        function of the neck's length and the weights (approx. number of lines)
        of its interchanges (`get_difficulty` ∘ `neck_weights`). Challenges this team
        has already been shown are skipped while alternatives exist. No-op if the game
        has no challenge pool.
        """
        if self.challenges is None:
            return
        snake = self.get_snake(team)
        weights = neck_weights(self.map, self.neck(team))
        snake.offer = self.challenges.pair_for(get_difficulty(weights), rng=self.rng, exclude=snake.seen_challenges)
        self._mark_seen(snake)

    # Powerups

    @event()
    def buy_powerup(self, team_id: int, powerup_id: str) -> list[Curse] | None:
        """Buy a powerup into the team's hand, deducting its coin cost.

        Raises ValueError if the team is out of the game, the id is unknown, the
        powerup is not enabled this game, or the team can't afford it. (A missing
        curse deck already strips ``"curse"`` from ``enabled_powerups`` at
        ``new_game``, so no separate deck check is needed here.)

        Returns whatever the powerup's buy-time effect produced — for ``"curse"``
        that's the CURSE_OPTIONS curses drawn to choose between, one of which the team
        keeps with ``choose_curse``; None for everything else.
        """
        team = self.get_team(team_id)
        snake = self._acting_snake(team)
        if self.status != Status.RUNNING:
            raise ValueError("The game is not running")
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

    @event()
    def play_normal_powerup(self, team_id: int, powerup_id: str) -> None:
        """Play a normal powerup (no parameters) from the team's hand, dispatching to its handler.

        The card is removed from the hand only *after* the handler returns, so a
        failed play (the handler raises ValueError on bad input) keeps the card.
        """
        team = self.get_team(team_id)
        snake = self._acting_snake(team)
        if self.status != Status.RUNNING:
            raise ValueError("The game is not running")
        if powerup_id not in snake.hand:
            raise ValueError(f"{powerup_id!r} is not in {team!r}'s hand")

        NORMAL_POWERUP_HANDLERS[powerup_id](self, team)
        snake.hand.remove(powerup_id)

    @event()
    def choose_curse(self, team_id: int, curse_id: str) -> Curse:
        """Keep one of the curses drawn when a curse was bought; the rest go back in the deck."""
        team = self.get_team(team_id)
        snake = self._acting_snake(team)
        if self.status != Status.RUNNING:
            raise ValueError("The game is not running")
        kept = next((c for c in snake.curse_choice if c.id == curse_id), None)
        if kept is None:
            raise ValueError(f"{team!r} was not offered a curse with id {curse_id!r}")
        for curse in snake.curse_choice:
            if curse is not kept and self.curse_deck is not None:
                self.curse_deck.put_back(curse)
        snake.curse_choice = []
        snake.held_curses.append(kept)
        return kept

    @event()
    def play_curse(self, team_id: int, target_team_id: int, curse_id: str) -> Curse:
        """Play a curse from the team's hand, dispatching to its handler.
        Returns the ``Curse`` played.

        Takes ``target_team_id=`` plus ``curse_id=``, selecting which held curse to
        play. The curse itself was drawn when it was bought, so playing one never
        touches the deck.
        """
        team = self.get_team(team_id)
        target_team = self.get_team(target_team_id)
        snake = self._acting_snake(team)
        if self.status != Status.RUNNING:
            raise ValueError("The game is not running")
        if "curse" not in snake.hand:
            raise ValueError(f"curse is not in {team!r}'s hand")

        result = handle_curse(self, team, target_team=target_team, curse_id=curse_id)
        snake.hand.remove("curse")
        return result

    @event()
    def play_jump(self, team_id: int, station: str) -> None:
        """Play a jump from the team's hand, dispatching to its handler.

        Takes ``station=``, which becomes passable for every team for the rest of
        the game (see `jumped_stations`); ownership is unaffected.
        """
        team = self.get_team(team_id)
        snake = self._acting_snake(team)
        if self.status != Status.RUNNING:
            raise ValueError("The game is not running")
        if "jump" not in snake.hand:
            raise ValueError(f"jump is not in {team!r}'s hand")

        handle_jump(self, team, station=station)
        snake.hand.remove("jump")

    @event()
    def play_detour(self, team_id: int, line: str) -> None:
        """Play a detour from the team's hand, dispatching to its handler.

        ``"detour"`` takes ``line=``. Played at the Anchor it swaps ``travel_line``
        outright; played mid-challenge it parks on ``Snake.pending_detour`` and takes
        effect when the current challenge completes (see ``complete_challenge``).
        Either way ``announced_line`` is untouched — Detour is not announced.
        """
        team = self.get_team(team_id)
        snake = self._acting_snake(team)
        if self.status != Status.RUNNING:
            raise ValueError("The game is not running")
        if "detour" not in snake.hand:
            raise ValueError(f"detour is not in {team!r}'s hand")

        handle_detour(self, team, line=line)
        snake.hand.remove("detour")

    def crash(self, team: Team) -> None:
        """Mark a snake as crashed."""
        self.get_snake(team).crashed = True

    def concede(self, team: Team) -> None:
        """Concede the game — a voluntary loss (a loss path alongside crashing)."""
        snake = self.get_snake(team)
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

    def is_neck_safe(self, team: Team) -> bool:
        """Return True if no un-jumped interchange in the Neck is claimed by another team."""
        snake = self.get_snake(team)
        if not snake.neck_active:
            return True
        for station_key in self.neck(team):
            if station_key in self.jumped_stations:
                continue
            claim = self.map.get_claim(station_key)
            if claim is not None and claim != team:
                return False
        return True

    def _apply_neck_crashes(self, exclude: Team) -> None:
        """Crash any other active-neck team whose neck now contains a claimed station.

        Called right after a team claims interchanges: a neck interchange that has
        *become* claimed by another team crashes that snake (the primary lose
        condition). Completions are resolved one at a time — i.e. in call /
        completion-timestamp order — so the first team to claim a contested
        interchange survives and the other crashes.
        """
        for other_team, other_snake in self.snakes.items():
            if other_team.role_id == exclude.role_id or other_snake.eliminated:
                continue
            if not self.is_neck_safe(other_team):
                self.crash(other_team)

    def has_winning_lead(self, team: Team) -> bool:
        """Whether a team's score (see `score`) leads every other active team's score +
        Neck by more than WINNING_THRESHOLD. A lead alone never wins — see declare_win."""
        others = [t for t in self.active_teams() if t != team]
        ours = self.score(team)
        return all(ours > self.total_controlled(o) + WINNING_THRESHOLD for o in others)

    def winner(self) -> Team | None:
        """Return the winning team if the game has been won, otherwise None.

        Win conditions:
          1. All opponents are out (crashed or conceded) — wins immediately.
          2. A win declaration that passed its check (see declare_win). Having the
             lead is not enough: it has to be declared, and still hold
             DECLARE_WIN_WINDOW_MINUTES later.
          3. The time limit ran out and `tiebreak_winner` settled it (see time_limit).
        """
        active = self.active_teams()
        if len(active) == 1:
            return active[0]
        return self.declared_winner

    def tiebreak_winner(self) -> Team | None:
        """End-of-game tiebreaker: the active team with the highest score (see `score`).

        For use when the time limit is reached (the clock itself is the bot's job).
        Necks don't count. Returns None on an exact tie for the lead, or if no teams
        remain.
        """
        active = self.active_teams()
        if not active:
            return None
        counts = {t: self.score(t) for t in active}
        best = max(counts.values())
        leaders = [t for t, count in counts.items() if count == best]
        return leaders[0] if len(leaders) == 1 else None

    # Contested objectives

    @event()
    def new_objective(self) -> str | None:
        """Place a new objective and schedule the next (not on reload: it's already in the save)."""
        if self.status != Status.RUNNING:
            return None
        station = choose_objective(self)
        if station is not None:
            self.objectives.append(station)
        if not self.loading:
            self.schedule_event(0, OBJECTIVE_INTERVAL_MINUTES, 0, self.new_objective)
        return station

    # Declaring a win

    @event()
    def declare_win(self, team_id: int) -> None:
        """Pay DECLARE_WIN_COST to claim the lead win; it's checked DECLARE_WIN_WINDOW_MINUTES later.

        Announcing it is the bot's job. The check is scheduled here so it can't be
        forgotten — but not while a saved game is reloading, because the pending check
        is already in the save and would otherwise run twice.
        """
        team = self.get_team(team_id)
        snake = self._acting_snake(team)
        if self.status != Status.RUNNING:
            raise ValueError("The game is not running")
        if snake.win_declared:
            raise ValueError(f"{team!r} has already declared a win")
        if snake.declare_cooldown:
            raise ValueError(f"{team!r} declared recently and failed; wait for the cooldown to end")
        if snake.coins < DECLARE_WIN_COST:
            raise ValueError(f"Not enough coins to declare a win: need {DECLARE_WIN_COST}, have {snake.coins}")
        snake.coins -= DECLARE_WIN_COST
        snake.win_declared = True
        if not self.loading:
            self.schedule_event(0, DECLARE_WIN_WINDOW_MINUTES, 0, self.resolve_win_declaration, team_id)

    @event()
    def resolve_win_declaration(self, team_id: int) -> bool:
        """Check a declaration once its window has passed. Returns True if the team won.

        On success the game ends. On failure (no longer leading, or out of the game)
        the team can't declare again for DECLARE_WIN_COOLDOWN_MINUTES. Never raises for
        a crashed declarer or an already-ended game, because this runs from the scheduler.
        """
        team = self.get_team(team_id)
        snake = self.get_snake(team)
        if not snake.win_declared:
            return False
        snake.win_declared = False
        if self.status != Status.RUNNING:
            return False  # the game already ended some other way
        if not snake.eliminated and self.has_winning_lead(team):
            self.declared_winner = team
            self.status = Status.END
            return True
        snake.declare_cooldown = True
        if not self.loading:
            self.schedule_event(0, DECLARE_WIN_COOLDOWN_MINUTES, 0, self.end_declare_cooldown, team_id)
        return False

    @event()
    def end_declare_cooldown(self, team_id: int) -> None:
        """Let a team declare again once the cooldown after a failed declaration is over."""
        self.get_snake(self.get_team(team_id)).declare_cooldown = False

    @event()
    def time_limit(self) -> None:
        """End the game on the clock, settling it by `tiebreak_winner`.

        Runs from the scheduler, so it never raises. The result is recorded in
        `declared_winner` (None on an exact tie) — announcing it is the bot's job.
        """
        if self.status != Status.RUNNING:
            return

        self.declared_winner = self.tiebreak_winner()
        self.status = Status.END

    # jloxgame functions

    async def configure(self, dctx: ApplicationContext) -> bool:
        modal = ConfigModal(
            self.status,
            [team.name for team in self.teams],
            [self.snakes[team].origin for team in self.teams],
            [f"#{team.colour:0>6x}" for team in self.teams],
            self.bonus_chance,
            {powerup: powerup in self.enabled_powerups for powerup in POWERUP_COSTS},
        )
        await dctx.send_modal(modal)

        if not await modal.wait():
            return False

        message = ""
        try:
            assert modal.team_names_input.value is not None
            message = "Invalid team name format!"
            team_names = [s.strip() for s in modal.team_names_input.value.split(",")]
            message = "Invalid number of teams specified!"
            assert 2 <= len(team_names) <= 5

            assert modal.team_positions_input.value is not None
            message = "Invalid team starting location format!"
            team_positions = [s.strip() for s in modal.team_positions_input.value.split(",")]
            message = "Invalid station entered!"
            assert all(self.map.has_station(position) for position in team_positions)
            message = "Not enough stations entered!"
            assert len(team_positions) >= len(team_names)
            message = "Team starting stations must be unique!"
            assert len(team_positions) == len(set(team_positions))

            if modal.team_colors_input.value is not None and modal.team_colors_input.value != "":
                message = "Invalid team colour format!"
                team_colors = [s.strip() for s in modal.team_colors_input.value.split(",")]
            else:
                team_colors = []
            message = "Invalid colour entered!"
            assert all(re.match(r"#[\da-f]{6}$", color) is not None for color in team_colors)

            assert modal.bonus_chance_input.value is not None
            message = "Invalid bonus chance format!"
            bonus_chance = float(modal.bonus_chance_input.value.strip())
            message = "Invalid bonus chance entered!"
            assert 0 <= bonus_chance <= 1

            assert modal.enabled_powerups_input.values is not None
            enabled_powerups = modal.enabled_powerups_input.values

            assert dctx.guild
            self.teams = [
                Team(name, int(color[1:], base=16))
                for name, color in zip(team_names, team_colors + DEFAULT_TEAM_COLORS)
            ]

            if self.status == Status.INIT:
                self.initial_events.append(
                    self.configured.get_instance(
                        self.game_time_now(), team_positions, team_colors, bonus_chance, enabled_powerups
                    )
                )
            else:
                self.configured(team_positions, team_colors, bonus_chance, enabled_powerups)

            for team, colour in zip(self.teams, team_colors):
                if team.role:
                    colour_int = int(colour[1:], base=16)
                    if team.role.colours.primary.value != colour_int:
                        await team.role.edit(color=Colour(colour_int))

            return True

        except Exception:  # noqa: BLE001 - a UI handler must not let anything escape and kill the bot
            await dctx.respond(f"Something went wrong: {message}", ephemeral=True)
            print(traceback.format_exc())
            return False

    @event()
    def configured(
        self, team_positions: list[str], team_colors: list[str], bonus_chance: float, enabled_powerups: list[str]
    ) -> None:
        self.enabled_powerups = set(enabled_powerups)
        if self.curse_deck is None:
            self.enabled_powerups.discard("curse")  # if there are no curses, this powerup is not playable

        if self.status in [Status.INIT, Status.SETUP]:
            print(f"[{self.thread_id} | configure | info] resetting snakes")
            for team, station, colour in zip(self.teams, team_positions, team_colors + DEFAULT_TEAM_COLORS):
                team.colour = int(colour[1:], base=16)
                self.snakes[team] = Snake(
                    origin=station,
                    anchor=station,
                    front=station,
                    color=colour,
                    travel_line=None,
                    announced_line=None,
                    coins=STARTING_COINS,
                )
            self.bonus_chance = bonus_chance
            self.status = Status.SETUP
        else:
            print(f"[{self.thread_id} | configure | info] game running, only changing colours/powerups")
            for team, colour in zip(self.teams, team_colors):
                self.snakes[team].color = colour

    async def start(self, dctx: ApplicationContext) -> None:
        self.started()

    @event()
    def started(self) -> None:
        if self.status != Status.SETUP:
            return

        print(f"[{self.thread_id} | start | info] randomising interchanges")
        # Origins are never bonus interchanges
        origins = {snake.origin for snake in self.snakes.values()}
        self.bonus_interchanges = {
            s for s in self.map.station_keys() if s not in origins and self.rng.random() < self.bonus_chance
        }

        print(f"[{self.thread_id} | start | info] picking initial challenge")
        if self.challenges is not None:
            self.initial_challenge = self.challenges.pick_in_range(
                INITIAL_DIFFICULTY_MIN, INITIAL_DIFFICULTY_MAX, rng=self.rng
            )

        for team in self.teams:
            self.initial_request_challenge(team)

        self.status = Status.RUNNING
        self.unpause()
        # Not on reload: it's already in the save.
        if not self.loading:
            self.schedule_event(0, OBJECTIVE_INTERVAL_MINUTES, 0, self.new_objective)


class ConfigModal(DesignerModal):
    def __init__(
        self,
        status: jloxgame.Status,
        team_names: list[str] | None = None,
        team_positions: list[str] | None = None,
        team_colors: list[str] | None = None,
        bonus_chance: float = DEFAULT_BONUS_CHANCE,
        enabled_powerups: dict[str, bool] | None = None,
    ) -> None:
        super().__init__(title="Snake: London Setup")  # pyright: ignore[reportUnknownMemberType]

        team_names = team_names or []
        team_positions = team_positions or []
        team_colors = team_colors or []
        enabled_powerups = enabled_powerups or {}

        if status == jloxgame.Status.INIT:
            self.team_names_input = InputText(
                placeholder="Team Alpha, Team Beta, Team Gamma, Team Delta, Team Epsilon", value=", ".join(team_names)
            )
            self.add_item(Label("Teams (comma-separated)", self.team_names_input))  # pyright: ignore[reportUnknownMemberType]
        else:
            self.add_item(TextDisplay("Teams: " + ", ".join(team_names)))  # pyright: ignore[reportUnknownMemberType]

        if status in [jloxgame.Status.INIT, jloxgame.Status.SETUP]:
            self.team_positions_input = InputText(
                placeholder="Wembley Park, Abbey Wood, Tooting Broadway, Rayners Lane, Ealing Broadway",
                value=", ".join(team_positions),
            )
            self.add_item(Label("Team starting positions (comma-separated)", self.team_positions_input))  # pyright: ignore[reportUnknownMemberType]
        else:
            self.add_item(TextDisplay("Team starting positions: " + "".join(team_positions)))  # pyright: ignore[reportUnknownMemberType]

        self.team_colors_input = InputText(
            placeholder=", ".join(DEFAULT_TEAM_COLORS), value=", ".join(team_colors), required=False
        )
        self.add_item(Label("Team colours (comma-separated)", self.team_colors_input))  # pyright: ignore[reportUnknownMemberType]

        if status in [jloxgame.Status.INIT, jloxgame.Status.SETUP]:
            self.bonus_chance_input = InputText(placeholder=str(DEFAULT_BONUS_CHANCE), value=str(bonus_chance))
            self.add_item(Label("Bonus chance", self.bonus_chance_input))  # pyright: ignore[reportUnknownMemberType]
        else:
            self.add_item(TextDisplay(f"Bonus chance: {bonus_chance}"))  # pyright: ignore[reportUnknownMemberType]

        self.enabled_powerups_input = StringSelect(
            max_values=len(POWERUP_COSTS),
            options=[
                SelectOption(label=POWERUP_NAMES[key], value=key, default=enabled_powerups.get(key, True))
                for key in POWERUP_COSTS
            ],
        )
        self.add_item(Label("Enabled powerups", self.enabled_powerups_input))  # pyright: ignore[reportUnknownMemberType]

    async def callback(self, interaction: Interaction):
        return await interaction.response.defer()
