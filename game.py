from __future__ import annotations

from dataclasses import dataclass, field
import re
import traceback
from typing import Any, Self

from discord import ApplicationContext, Colour, Interaction, SelectOption
from discord.ui import DesignerModal, Label, InputText, StringSelect, TextDisplay

from challenges import Challenge, ChallengePool, get_difficulty, neck_weights
from config import (
    BONUS_AT_FRONT,
    BONUS_CLAIMED,
    CHALLENGES_PATH,
    CURSES_PATH,
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
from jloxgame.events import GameEvent, register_event
from jloxgame.state import Status
from map import Map
from powerups import POWERUP_HANDLERS, POWERUP_ON_BUY, Curse, CurseDeck

import jloxgame
from jloxgame import GameContext, Team

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
    double_up_remaining: int = 0  # completions left with a doubled reward (0-2)
    blocked_station: str | None = None  # set by Retreat; the next request must differ
    pending_detour: str | None = None  # Detour played mid-challenge; overrides the next declared line
    held_curses: list[Curse] = field(default_factory=list[Curse])  # curses bought and not yet played
    curses: list[Curse] = field(default_factory=list[Curse])  # curses inflicted on this team by others

    @property
    def eliminated(self) -> bool:
        """Out of the game — either crashed or conceded."""
        return self.crashed or self.conceded

class GameState(GameContext):
    def __init__(self) -> None:
        super().__init__()

        self.map: Map = Map("map/connections.json")
        self.snakes: dict[Team, Snake] = {}  # Team -> Snake
        self.bonus_interchanges: set[str] = set()  # interchanges that pay bonus coins
        self.challenges: ChallengePool | None = None  # pool the offers are drawn from (None = no challenges)
        self.initial_challenge: Challenge | None = None  # shared initial challenge (default for every team, unless vetoed)
        # --- Powerups ---
        self.enabled_powerups: set[str] = set(POWERUP_COSTS.keys())  # powerup ids buyable this game
        self.jumped_stations: set[str] = set()  # globally, permanently passable (all players)
        self.curse_deck: CurseDeck | None = None  # deck the curse powerup draws from (None = curse unavailable)

        self.latest_generated_map = 0 # not synced

        try:
            self.challenges = ChallengePool(CHALLENGES_PATH)
        except FileNotFoundError:
            self.challenges = None
        
        try:
            self.curse_deck = CurseDeck(CURSES_PATH)
            # A deck with no curses in it counts as no deck at all
            if len(self.curse_deck) == 0: self.curse_deck = None
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

    def total_controlled(self, team: Team) -> int:
        """Body + active Neck — the opponent-side total in the win-lead comparison (see `winner`)."""
        return len(self.body_stations(team)) + len(self.neck(team))

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

    def request_challenge(self, team: Team, station: str) -> None:
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
        path = self.map.path_between_on_line(snake.travel_line, snake.anchor, station)
        neck_is_claimed = any(self._blocks_travel(interchange) for interchange in path[1:])

        snake.blocked_station = None  # any successful request clears the retreat block
        snake.front = station
        snake.neck_active = True
        if neck_is_claimed:
            self.crash(team)
        else:
            self._draw_offer(team)

    def complete_challenge(self, team: Team, next_line: str, *, hard: bool = False) -> list[str]:
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
                self.map.claim_segment(snake.travel_line, full_path[i], full_path[i + 1], team)

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

    def veto_challenges(self, team: Team) -> bool:
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

    def _draw_offer(self, team: Team) -> None:
        """Draw the (easier, harder) pair for a team's current neck, sized by its difficulty.

        Only used once a line has been declared (i.e. after the initial
        challenge) — there's a real neck to measure by then. Difficulty is a
        function of the neck's length and the weights (approx. number of lines)
        of its interchanges (`get_difficulty` ∘ `neck_weights`). No-op if the game
        has no challenge pool.
        """
        if self.challenges is None:
            return
        snake = self.get_snake(team)
        weights = neck_weights(self.map, snake.travel_line or "", self.neck(team))
        snake.offer = self.challenges.pair_for(get_difficulty(weights), rng=self.rng)

    # Powerups

    def buy_powerup(self, team: Team, powerup_id: str) -> Curse | None:
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

    def play_powerup(self, team: Team, powerup_id: str, **kwargs: Any) -> Curse | None:
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

    def winner(self) -> Team | None:
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

    def tiebreak_winner(self) -> Team | None:
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
    
    # jloxgame functions

    async def configure(self, dctx: ApplicationContext) -> bool:
        modal = ConfigModal(self.status, 
            [team.name for team in self.teams], 
            [self.snakes[team].origin for team in self.teams], 
            [f"#{team.colour:0>6x}" for team in self.teams],
            self.bonus_chance,
            {powerup: powerup in self.enabled_powerups for powerup in POWERUP_COSTS.keys()}
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
            self.teams = [Team(name, int(color[1:], base=16)) for name, color in zip(team_names, team_colors + DEFAULT_TEAM_COLORS)]

            self.add_event(Configure(team_positions, team_colors, bonus_chance, set(enabled_powerups)))

            for team, colour in zip(self.teams, team_colors):
                if team.role:
                    colour_int = int(colour[1:], base=16)
                    if team.role.colours.primary.value != colour_int:
                        await team.role.edit(color = Colour(colour_int))

            return True

        except Exception:
            await dctx.respond(f"Something went wrong: {message}", ephemeral=True)
            print(traceback.format_exc())
            return False
    
    async def start(self, dctx: ApplicationContext) -> None:
        self.add_event(Start())

class ConfigModal(DesignerModal):
    def __init__(
        self, 
        status: jloxgame.Status, 
        team_names: list[str] = [], 
        team_positions: list[str] = [], 
        team_colors: list[str] = [], 
        bonus_chance: float = DEFAULT_BONUS_CHANCE, 
        enabled_powerups: dict[str, bool] = {}
    ) -> None:
        super().__init__(title="Snake: London Setup") # pyright: ignore[reportUnknownMemberType]
        
        if status == jloxgame.Status.INIT:
            self.team_names_input = InputText(placeholder="Team Alpha, Team Beta, Team Gamma, Team Delta, Team Epsilon", value=", ".join(team_names))        
            self.add_item(Label(f"Teams (comma-separated)", self.team_names_input)) # pyright: ignore[reportUnknownMemberType]
        else:
            self.add_item(TextDisplay("Teams: " + ", ".join(team_names))) # pyright: ignore[reportUnknownMemberType]

        if status in [jloxgame.Status.INIT, jloxgame.Status.SETUP]:
            self.team_positions_input = InputText(placeholder="Wembley Park, Abbey Wood, Tooting Broadway, Rayners Lane, Ealing Broadway", value=", ".join(team_positions))
            self.add_item(Label(f"Team starting positions (comma-separated)", self.team_positions_input)) # pyright: ignore[reportUnknownMemberType]
        else:
            self.add_item(TextDisplay("Team starting positions: " + "".join(team_positions))) # pyright: ignore[reportUnknownMemberType]
        
        self.team_colors_input = InputText(placeholder=", ".join(DEFAULT_TEAM_COLORS), value=", ".join(team_colors), required=False)
        self.add_item(Label(f"Team colours (comma-separated)", self.team_colors_input)) # pyright: ignore[reportUnknownMemberType]

        if status in [jloxgame.Status.INIT, jloxgame.Status.SETUP]:
            self.bonus_chance_input = InputText(placeholder=str(DEFAULT_BONUS_CHANCE), value=str(bonus_chance))
            self.add_item(Label(f"Bonus chance", self.bonus_chance_input)) # pyright: ignore[reportUnknownMemberType]
        else:
            self.add_item(TextDisplay(f"Bonus chance: {bonus_chance}")) # pyright: ignore[reportUnknownMemberType]

        self.enabled_powerups_input = StringSelect(max_values=len(POWERUP_COSTS), options=[SelectOption(label=key, default=enabled_powerups.get(key, True)) for key in POWERUP_COSTS.keys()])
        self.add_item(Label(f"Enabled powerups", self.enabled_powerups_input)) # pyright: ignore[reportUnknownMemberType]
        
    async def callback(self, interaction: Interaction):
        return await interaction.response.defer()

@register_event
@dataclass
class Configure(GameEvent[GameState]):
    @staticmethod
    def event_type() -> str: return "configure"
    
    team_positions: list[str]
    team_colors: list[str]
    bonus_chance: float
    enabled_powerups: set[str]
        
    def to_dict(self) -> dict[str, Any]:
        return {
            "team_positions": self.team_positions, 
            "team_colors": self.team_colors, 
            "bonus_chance": self.bonus_chance,
            "enabled_powerups": list(self.enabled_powerups)
        }
    
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return cls(data["team_positions"], data["team_colors"], data["bonus_chance"], set(data["enabled_powerups"]))
    
    def update(self, gctx: GameState) -> None:
        gctx.enabled_powerups = self.enabled_powerups
        if gctx.curse_deck is None: gctx.enabled_powerups.discard("curse") # if there are no curses, this powerup is not playable

        if gctx.status in [Status.INIT, Status.SETUP]:
            print(f"[{gctx.thread_id} | configure | info] resetting snakes")
            for team, station, colour in zip(gctx.teams, self.team_positions, self.team_colors + DEFAULT_TEAM_COLORS):
                team.colour = int(colour[1:], base=16)
                gctx.snakes[team] = Snake(
                    origin=station,
                    anchor=station,
                    front=station,
                    color=colour,
                    travel_line=None,
                    announced_line=None,
                    coins=STARTING_COINS,
                )
            gctx.bonus_chance = self.bonus_chance
            gctx.status = Status.SETUP
        else:
            print(f"[{gctx.thread_id} | configure | info] game running, only changing colours/powerups")
            for team, colour in zip(gctx.teams, self.team_colors):
                gctx.snakes[team].color = colour

@register_event
@dataclass
class Start(GameEvent[GameState]):
    @staticmethod
    def event_type() -> str: return "start"
        
    def to_dict(self) -> dict[str, Any]:
        return {}
    
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return cls()
    
    def update(self, gctx: GameState) -> None:
        if gctx.status != Status.SETUP: return

        print(f"[{gctx.thread_id} | start | info] randomising interchanges")
        # Origins are never bonus interchanges
        origins = set(snake.origin for snake in gctx.snakes.values())
        gctx.bonus_interchanges = {s for s in gctx.map.station_keys() if s not in origins and gctx.rng.random() < gctx.bonus_chance}
        
        print(f"[{gctx.thread_id} | start | info] picking initial challenge")
        if gctx.challenges is not None:
            gctx.initial_challenge = gctx.challenges.pick_in_range(INITIAL_DIFFICULTY_MIN, INITIAL_DIFFICULTY_MAX, rng=gctx.rng)

        for team in gctx.teams:
            gctx.initial_request_challenge(team)

        gctx.status = Status.RUNNING

@register_event
@dataclass
class TimeLimit(GameEvent[GameState]):
    @staticmethod
    def event_type() -> str: return "time_limit"
        
    def to_dict(self) -> dict[str, Any]:
        return {}
    
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return cls()
    
    def update(self, gctx: GameState) -> None:
        if gctx.status != Status.RUNNING: return

        winner = gctx.tiebreak_winner()
        print(f"[{gctx.thread_id} | time_limit | info] tiebreak winner {winner}")
        gctx.status = Status.END