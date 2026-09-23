"""The referee's tool for a game's save. fallback-referee.md explains when and how to use it.

    python admin.py SAVE                  where everyone stands, and the numbered history
    python admin.py SAVE CHANGES          apply a changes file: writes save/<id>.json and a map
    python admin.py SAVE CHANGES --live   type changes one at a time, when running a game by hand

Only use it on a save the bot isn't running.

A changes file has one change per line. Moves start with the time they happened, in order:

    backup 14:03                             when the backup was posted
    14:05 Alpha complete Central harder      the line they're getting on, then easier or harder
    14:06 Beta request Liverpool Street
    14:07 Beta buy Curse
    14:07 Beta choose Get a Melon            the curse they kept from the draw
    14:08 Beta veto
    14:09 Alpha jump Holborn
    14:10 Beta curse Alpha with Get a Melon
    14:11 Alpha play Good Service
    14:12 Beta play Retreat
    14:12 Alpha detour Elizabeth
    14:13 Alpha declare
    14:14 Beta concede
    until 14:15                              run the game's clock on to here ("until now" works too)

Fixes need no time:

    Alpha coins 7                            or +2, or -1
    Alpha give Detour
    Alpha give curse Get a Melon
    Alpha take Jump
    Alpha take curse Get a Melon
    Alpha line Victoria                      at their Anchor, with no challenge on
    Alpha cancel                             calls off their challenge
    Alpha end veto
    Alpha out
    Alpha back in
    objective add Green Park
    objective remove Green Park
    undo 4                                   a number from the history, always done first

Capitals and punctuation in names don't matter. Anything after a # is a note.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import difflib
import inspect
import io
import json
import os
import re
import sys
import tempfile
from collections import Counter
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from config import (
    CURSES_PATH,
    DECLARE_WIN_COOLDOWN_MINUTES,
    DECLARE_WIN_WINDOW_MINUTES,
    POWERUP_COMMANDS,
    POWERUP_NAMES,
)

with contextlib.redirect_stdout(io.StringIO()):  # the engine logs every event it registers
    import render
    from game import GameState
    from jloxgame.state import Status
    from powerups import CurseDeck

if TYPE_CHECKING:
    from challenges import Challenge
    from game import Snake
    from jloxgame.state import EventInstance, Team

VETO_MINUTES = 15  # main.py schedules the unveto itself, not the engine
MIN = 60_000


def _forms(table: dict[str, tuple[str, ...]]) -> dict[str, str]:
    return {form: verb for verb, forms in table.items() for form in forms}


_MOVE_VERBS = _forms(
    {
        "request": ("request", "requests", "requested"),
        "complete": ("complete", "completes", "completed"),
        "veto": ("veto", "vetoes", "vetoed"),
        "buy": ("buy", "buys", "bought"),
        "choose": ("choose", "chooses", "chose", "keep", "keeps", "kept"),
        "play": ("play", "plays", "played", "activate", "activates", "activated"),
        "jump": ("jump", "jumps", "jumped"),
        "detour": ("detour", "detours", "detoured"),
        "curse": ("curse", "curses", "cursed"),
        "declare": ("declare", "declares", "declared"),
        "concede": ("concede", "concedes", "conceded"),
    }
)
_FIX_VERBS = _forms(
    {
        "coins": ("coins", "coin"),
        "give": ("give",),
        "take": ("take",),
        "line": ("line",),
        "cancel": ("cancel",),
        "end": ("end",),
        "out": ("out",),
        "back": ("back", "revive"),
    }
)
_DIFFICULTY = {"easier": False, "easy": False, "harder": True, "hard": True}
_FILLER = {"at", "to", "on", "onto", "the", "a", "an", "with", "taking", "take", "getting", "get", "via"}
_MOVE_EVENTS = {
    "request_challenge",
    "complete_challenge",
    "veto_challenges",
    "buy_powerup",
    "choose_curse",
    "play_normal_powerup",
    "play_jump",
    "play_detour",
    "play_curse",
    "declare_win",
}
_FIX_EVENTS = {
    "admin_set_coins",
    "admin_give",
    "admin_take",
    "admin_set_line",
    "admin_cancel_challenge",
    "admin_end_veto",
    "admin_knock_out",
    "admin_bring_back",
    "admin_add_objective",
    "admin_remove_objective",
}
_HIDDEN = {"configured", "started", "__reload__"}
# Timers an event sets going, and how long after it they are due: undoing the event takes them too.
_LINKS = {
    "veto_challenges": (("unveto", VETO_MINUTES),),
    "declare_win": (("resolve_win_declaration", DECLARE_WIN_WINDOW_MINUTES),),
    "resolve_win_declaration": (("end_declare_cooldown", DECLARE_WIN_COOLDOWN_MINUTES),),
}


class AdminError(Exception):
    """A problem with the save or the changes. Nothing has been written."""


@dataclass(frozen=True)
class Change:
    at: int  # game time, ms
    text: str
    line: int = 0
    timed: bool = True


@dataclass(frozen=True)
class Outcome:
    """What a run did, for live mode to pick out what's new."""

    said: list[str]
    after_last: list[str]  # timers that came due after the last change
    undone: list[str]
    summary: list[str]
    map_note: str


@dataclass
class Changes:
    backup: datetime | None = None
    until: datetime | None = None
    undo: list[tuple[int, int]] = field(default_factory=list)  # (file line, history number)
    entries: list[tuple[int, datetime | None, str]] = field(default_factory=list)


class _Game(GameState):
    """Replays a save like the bot, without jloxgame writing ./save when a replay breaks."""

    replaying: ClassVar[EventInstance | None] = None

    def actualise_instance(self, inst: EventInstance) -> None:
        _Game.replaying = inst
        getattr(self, inst.__type__)(*inst.args, **inst.kwargs)


@contextlib.contextmanager
def _quiet() -> Iterator[None]:
    with contextlib.redirect_stdout(io.StringIO()):
        yield


# --- Names --------------------------------------------------------------------------------------


def _norm(name: str) -> str:
    name = re.sub(r"['’]", "", name.lower().replace("&", " and "))
    return re.sub(r"[^a-z0-9]+", " ", name).strip()


def _word(word: str) -> str:
    return word.lower().strip(".!?:;'\"()")


def _tokens(text: str) -> list[str]:
    return [w for w in re.split(r"[\s,;]+", text.strip()) if w]


def _skip(words: list[str]) -> list[str]:
    i = 0
    while i < len(words) and _word(words[i]) in _FILLER:
        i += 1
    return words[i:]


class _Names[V]:
    """Finds one kind of thing by name, ignoring capitals and punctuation."""

    def __init__(self, kind: str, pairs: Iterable[tuple[str, V]] = (), label: Callable[[V], str] = str) -> None:
        self.kind = kind
        self.label = label
        self._strong: dict[str, set[V]] = {}
        self._weak: dict[str, set[V]] = {}
        for name, value in pairs:
            self.add(name, value)

    def add(self, name: str, value: V, *, weak: bool = False) -> None:
        (self._weak if weak else self._strong).setdefault(_norm(name), set()).add(value)

    def _hits(self, name: str) -> set[V]:
        return self._strong.get(name) or self._weak.get(name) or set()

    def find(self, text: str) -> V | None:
        hits = self._hits(_norm(text))
        return next(iter(hits)) if len(hits) == 1 else None

    def get(self, text: str) -> V:
        name = _norm(text)
        if not name:
            raise AdminError(f"The {self.kind} is missing.")
        hits = self._hits(name)
        if len(hits) == 1:
            return next(iter(hits))
        if hits:
            raise AdminError(
                f"'{text}' could be more than one {self.kind}: {', '.join(sorted(map(self.label, hits)))}."
            )
        close = difflib.get_close_matches(name, [*self._strong, *self._weak], n=3, cutoff=0.6)
        labels = list(dict.fromkeys(self.label(v) for c in close for v in self._hits(c)))  # closest first
        hint = f" Did you mean {' or '.join(labels)}?" if labels else ""
        raise AdminError(f"There's no {self.kind} called '{text}'.{hint}")

    def take_prefix(self, words: list[str]) -> tuple[V, list[str]] | None:
        for i in range(len(words), 0, -1):
            value = self.find(" ".join(words[:i]))
            if value is not None:
                return value, words[i:]
        return None


def _find_name[V](names: _Names[V], words: list[str]) -> V | None:
    """The name as written, else without its leading filler words: "Get a Melon" is a curse."""
    while True:
        value = names.find(" ".join(words))
        if value is not None or not words or _word(words[0]) not in _FILLER:
            return value
        words = words[1:]


def _get_name[V](names: _Names[V], words: list[str]) -> V:
    value = _find_name(names, words)
    return value if value is not None else names.get(" ".join(_skip(words)))


def _take_name[V](names: _Names[V], words: list[str]) -> tuple[V, list[str]] | None:
    while True:
        found = names.take_prefix(words)
        if found is not None or not words or _word(words[0]) not in _FILLER:
            return found
        words = words[1:]


def _route(stations: list[str]) -> str:
    return " > ".join(stations) or "-"


def _offer(snake: Snake, *, full: bool) -> list[str]:
    """The challenges a team has on offer, as the bot would show them."""
    if snake.offer is None:
        return []
    easier, harder = snake.offer

    def show(challenge: Challenge) -> str:
        return f"{challenge.name} (difficulty {challenge.difficulty})" + (f": {challenge.description}" if full else "")

    if easier == harder:
        return [f"challenge: {show(easier)}"]
    return [f"easier: {show(easier)}", f"harder: {show(harder)}"]


# --- Describing events --------------------------------------------------------------------------


@dataclass(frozen=True)
class _Labels:
    teams: dict[int, str]
    curses: dict[str, str]

    @classmethod
    def of(cls, raw: dict) -> _Labels:
        try:
            curses = {c.id: c.name for c in CurseDeck(CURSES_PATH).catalog()}
        except (OSError, ValueError, KeyError, TypeError):
            curses = {}
        return cls({t["role_id"]: t["name"] for t in raw.get("teams", [])}, curses)

    def team(self, team_id: object) -> str:
        return self.teams.get(team_id, str(team_id))  # type: ignore[arg-type]

    def card(self, powerup_id: str, curse_id: str | None) -> str:
        if curse_id:
            return f"the curse {self.curses.get(curse_id, curse_id)}"
        return POWERUP_NAMES.get(powerup_id, powerup_id)


def _event_params(kind: str, args: list, kwargs: dict) -> dict:
    for cls in GameState.__mro__:
        if kind in vars(cls):
            try:
                return dict(inspect.signature(vars(cls)[kind].func).bind(None, *args, **kwargs).arguments)
            except (AttributeError, TypeError):
                return {}
    return {}


def _describe(labels: _Labels, kind: str, args: list, kwargs: dict, result: object = None) -> str:
    """Something that happened, in plain words."""
    a = _event_params(kind, args, kwargs)
    team = labels.team(a.get("team_id"))
    match kind:
        case "request_challenge":
            return f"{team} requested {a['station']}"
        case "complete_challenge":
            return f"{team} completed{' (harder)' if a.get('hard') else ''}, now on the {a['next_line']}"
        case "veto_challenges":
            return f"{team} vetoed"
        case "buy_powerup":
            return f"{team} bought {POWERUP_NAMES.get(a['powerup_id'], a['powerup_id'])}"
        case "choose_curse":
            return f"{team} kept the curse {labels.curses.get(a['curse_id'], a['curse_id'])}"
        case "play_normal_powerup":
            return f"{team} played {POWERUP_NAMES.get(a['powerup_id'], a['powerup_id'])}"
        case "play_jump":
            return f"{team} jumped {a['station']}"
        case "play_detour":
            return f"{team} played Detour onto the {a['line']}"
        case "play_curse":
            return f"{team} cursed {labels.team(a['target_team_id'])}"
        case "declare_win":
            return f"{team} declared a win"
        case "unveto":
            return f"{team}'s veto period ended"
        case "new_objective":
            return f"new objective at {result}" if result else "new objective"
        case "resolve_win_declaration":
            return f"{team}'s declared win was settled" + ("" if result is None else ": won" if result else ": failed")
        case "end_declare_cooldown":
            return f"{team} could declare a win again"
        case "time_limit":
            return "the time limit ran out"
        case "admin_set_coins":
            return f"fix: {team} set to {a['coins']} coins"
        case "admin_give":
            return f"fix: gave {team} {labels.card(a['powerup_id'], a.get('curse_id'))}"
        case "admin_take":
            return f"fix: took {labels.card(a['powerup_id'], a.get('curse_id'))} from {team}"
        case "admin_set_line":
            return f"fix: {team} put on the {a['line']}"
        case "admin_cancel_challenge":
            return f"fix: {team}'s challenge called off"
        case "admin_end_veto":
            return f"fix: {team}'s veto period ended"
        case "admin_knock_out":
            return f"{team} went out of the game"
        case "admin_bring_back":
            return f"fix: {team} brought back into the game"
        case "admin_add_objective":
            return f"fix: objective added at {a['station']}"
        case "admin_remove_objective":
            return f"fix: objective at {a['station']} taken away"
    return kind


def _describe_due(labels: _Labels, kind: str, args: list, kwargs: dict) -> str:
    """A timer that hasn't gone off yet, in plain words."""
    team = labels.team(_event_params(kind, args, kwargs).get("team_id"))
    return {
        "unveto": f"{team}'s veto period ends",
        "new_objective": "a new objective appears",
        "resolve_win_declaration": f"{team}'s declared win is settled",
        "end_declare_cooldown": f"{team} can declare a win again",
        "time_limit": "the time limit runs out",
    }.get(kind, kind)


def _elapsed(t: int) -> str:
    return f"{t // 3_600_000}:{t // MIN % 60:02d}"


# --- The save's history -------------------------------------------------------------------------


def _history(raw: dict) -> list[int]:
    """Where each numbered event sits in the save's log: #1 is history[0]. Timers not yet due aren't numbered."""
    return [
        i
        for i, e in enumerate(raw["event_log"])
        if e["__type__"] not in _HIDDEN and e["__time__"] <= raw["last_update"]
    ]


def _with_timers(events: list[dict], i: int) -> list[int]:
    """The event at i, and the timers it set going, whether they have gone off yet or not."""
    found = [i]
    event = events[i]
    team = _event_params(event["__type__"], event["args"], event["kwargs"]).get("team_id")
    for kind, minutes in _LINKS.get(event["__type__"], ()):
        due = event["__time__"] + minutes * MIN
        for j in range(i + 1, len(events)):
            timer = events[j]
            if (
                timer["__type__"] == kind
                and _event_params(kind, timer["args"], timer["kwargs"]).get("team_id") == team
                and abs(timer["__time__"] - due) <= 10_000
            ):
                found += _with_timers(events, j)
                break
    return found


def _undo(raw: dict, undo: list[tuple[int, int]], labels: _Labels) -> tuple[dict, list[int], list[str]]:
    """Takes events out of the log. Returns the new save, where each of its events came from, and what went."""
    events, history = raw["event_log"], _history(raw)
    dropped: set[int] = set()
    said = []
    for line, number in undo:
        if not 1 <= number <= len(history):
            raise AdminError(f"Line {line}: there's no #{number}. The history goes up to #{len(history)}.")
        i = history[number - 1]
        event = events[i]
        what = _describe(labels, event["__type__"], event["args"], event["kwargs"])
        if event["__type__"] not in _MOVE_EVENTS | _FIX_EVENTS:
            raise AdminError(f"Line {line}: #{number} ({what}) went off by itself, so it can't be undone.")
        for j in _with_timers(events, i):
            if j in dropped:
                continue
            dropped.add(j)
            e = events[j]
            if j == i:
                said.append(f"#{number}  {what}")
            elif j in history:
                said.append(f"#{history.index(j) + 1}  {_describe(labels, e['__type__'], e['args'], e['kwargs'])}")
            else:
                said.append(f"      the timer for: {_describe_due(labels, e['__type__'], e['args'], e['kwargs'])}")
    kept = [i for i in range(len(events)) if i not in dropped]
    return {**raw, "event_log": [events[i] for i in kept]}, kept, said


def _load(raw: dict, game_id: int) -> _Game:
    _Game.replaying = None
    with tempfile.TemporaryDirectory() as tmp:
        Path(tmp, f"{game_id}.json").write_text(json.dumps(raw), encoding="utf-8")
        with _quiet():
            game = _Game.load(Path(tmp), game_id)
    # Loading logs the whole gap since the save as downtime; this isn't the bot restarting.
    if game.event_log and game.event_log[-1].__type__ == "__reload__":
        game.event_log.pop()
    return game


def _broken_at(raw: dict) -> int | None:
    """Where in the save's log the last failed load broke, if it broke on an event."""
    inst = _Game.replaying
    if inst is None:
        return None
    for i, e in enumerate(raw["event_log"]):
        if (e["__type__"], e["__time__"], e["args"], e["kwargs"]) == (
            inst.__type__,
            inst.__time__,
            inst.args,
            inst.kwargs,
        ):
            return i
    return None


def _load_save(path: Path, game_id: int) -> tuple[dict, _Game]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as e:
        raise AdminError(f"Couldn't read the save: {e}") from None
    except ValueError as e:
        raise AdminError(f"Couldn't load the save, because it isn't a save file: {e}") from None
    try:
        return raw, _load(raw, game_id)
    except Exception as e:  # noqa: BLE001 - a broken or foreign file can fail anywhere in the replay
        where = ""
        i = _broken_at(raw)
        if i is not None and i in _history(raw):
            ev = raw["event_log"][i]
            where = f" It breaks at #{_history(raw).index(i) + 1} ({_describe(_Labels.of(raw), ev['__type__'], ev['args'], ev['kwargs'])})."
        raise AdminError(f"Couldn't load the save: {e}.{where}") from None


def _load_undone(raw: dict, undo: list[tuple[int, int]], game_id: int, labels: _Labels) -> tuple[_Game, list[str]]:
    new_raw, kept, said = _undo(raw, undo, labels)
    try:
        return _load(new_raw, game_id), said
    except Exception as e:  # noqa: BLE001 - whatever breaks, the referee needs to know which event
        j = _broken_at(new_raw)
        history = _history(raw)
        if j is None or kept[j] not in history:
            raise AdminError(f"Undoing that leaves a history that no longer loads: {e}") from None
        number = history.index(kept[j]) + 1
        ev = new_raw["event_log"][j]
        what = _describe(labels, ev["__type__"], ev["args"], ev["kwargs"])
        raise AdminError(
            f"Undoing that breaks #{number} ({what}), which depended on it: {e}\n"
            f"Undo #{number} as well. If what it did still matters, put it back with a fix."
        ) from None


# --- Applying changes ---------------------------------------------------------------------------


class _Session:
    """Applies moves and fixes to a loaded game, stamping each with when it really happened."""

    def __init__(self, game: GameState, clock: Callable[[int], str]) -> None:
        self.game = game
        self.clock = clock
        self.said: list[str] = []
        self.after_last = 0  # where in `said` the timers that came due after the last change begin
        self.stations = _Names[str]("station")
        self.lines = _Names[str]("line")
        for s in game.map.iter_stations():
            self.stations.add(s.key, s.key)
            self.stations.add(s.display_name, s.key)
        for s in game.map.iter_stations():
            for part in s.display_name.split(" / "):
                self.stations.add(part, s.key, weak=True)
        for line in game.map.iter_lines():
            self.lines.add(line.key, line.key)
            self.lines.add(line.display_name, line.key)
            self.lines.add(re.sub(r"\s+line$", "", line.display_name, flags=re.IGNORECASE), line.key)
        self.teams = _Names[int](
            "team", ((t.name, t.role_id) for t in game.teams), label=lambda i: game.get_team(i).name
        )
        self.powerups = _Names[str]("powerup", label=lambda p: POWERUP_NAMES.get(p, p))
        for pid, display in POWERUP_NAMES.items():
            for name in (pid, display, POWERUP_COMMANDS[pid]):
                self.powerups.add(name, pid)

    def _pin(self, at: int) -> None:
        self.game.game_time_now = lambda: at  # type: ignore[method-assign]

    def unpin(self) -> None:
        vars(self.game).pop("game_time_now", None)

    def _say(self, at: int, text: str) -> None:
        stamp = self.clock(at)
        first, *rest = text.split("\n")
        lines = [f"  {stamp}  {first}", *(f"  {' ' * len(stamp)}      {line}" for line in rest)]
        self.said += lines
        for line in lines:
            print(line)

    def fire_timers(self, until: int) -> None:
        game = self.game
        while game.scheduled_events and game.scheduled_events[0].__time__ <= until:
            inst = game.scheduled_events.pop(0)
            self._pin(inst.__time__)
            try:
                with _quiet():
                    result = getattr(game, inst.__type__)(*inst.args, **inst.kwargs)
            except Exception as e:
                raise AdminError(
                    f"The '{inst.__type__}' timer due at {self.clock(inst.__time__)} failed: {e}\n"
                    "That's a fault in the game itself. For this game, see 'If the script won't work for a game' in "
                    "the referee guide, and keep this message for Anshul to look at after the game."
                ) from e
            labels = _Labels({t.role_id: t.name for t in game.teams}, {})
            self._say(inst.__time__, "(timer) " + _describe(labels, inst.__type__, inst.args, inst.kwargs, result))

    def apply(self, change: Change) -> None:
        self.fire_timers(change.at)
        self._pin(change.at)
        crashed = {t.role_id for t in self.game.teams if self.game.get_snake(t).crashed}
        try:
            with _quiet():
                said = self._do(_tokens(change.text), change.timed)
        except AdminError as e:
            raise AdminError(f"Line {change.line}: {change.text}\n  {e}") from None
        except Exception as e:  # noqa: BLE001 - whatever the engine raises, the referee needs the line it came from
            raise AdminError(f"Line {change.line}: {change.text}\n  The game refused this: {e}") from None
        self._say(change.at, said)
        for team in self.game.teams:
            if self.game.get_snake(team).crashed and team.role_id not in crashed:
                self._say(change.at, f"!! {team.name} CRASHED")

    def finish(self, resume_at: int) -> None:
        self.after_last = len(self.said)
        self.fire_timers(resume_at)
        self.unpin()
        self.game.last_update = resume_at  # anything later would be saved as a timer, not a move

    def _do(self, words: list[str], timed: bool) -> str:
        if words and _word(words[0]) == "objective":
            return self._objective(words[1:])
        found = self.teams.take_prefix(words)
        if found is None:
            teams = ", ".join(t.name for t in self.game.teams)
            raise AdminError(f"It doesn't start with a team name. The teams are: {teams}.")
        team_id, rest = found
        team = self.game.get_team(team_id)
        if not rest:
            raise AdminError("There's nothing after the team name.")
        word = _word(rest[0])
        if word in _MOVE_VERBS:
            if not timed:
                raise AdminError("Moves need the time they happened at the start of the line, like '14:05 Alpha veto'.")
            return getattr(self, f"_{_MOVE_VERBS[word]}")(team, rest[1:])
        if word in _FIX_VERBS:
            return getattr(self, f"_fix_{_FIX_VERBS[word]}")(team, rest[1:])
        raise AdminError(f"'{rest[0]}' isn't a move or a fix. See 'python admin.py --help' for the list.")

    # Moves

    def _request(self, team: Team, words: list[str]) -> str:
        station = _get_name(self.stations, words)
        self.game.request_challenge(team.role_id, station)
        said = f"{team.name} requested {station} · Neck: {_route(self.game.neck(team))}"
        return "\n".join([said, *_offer(self.game.get_snake(team), full=True)])

    def _complete(self, team: Team, words: list[str]) -> str:
        snake = self.game.get_snake(team)
        hard = _DIFFICULTY.get(_word(words[-1])) if words else None
        if hard is not None:
            words = words[:-1]
        at, line = self._at_and_line(words)
        if at is not None and snake.neck_active and at != snake.front:
            raise AdminError(f"{team.name}'s challenge is at {snake.front}, not {at}.")
        first = snake.travel_line is None
        if hard is None and not first:
            raise AdminError("Say which challenge they did: end the line with 'easier' or 'harder'.")
        front, before = snake.front, snake.coins
        self.game.complete_challenge(team.role_id, line, hard=bool(hard))
        kind = "first challenge" if first else "harder" if hard else "easier"
        earned = f"+{snake.coins - before} coins, {snake.coins} in all"
        return f"{team.name} completed at {front} ({kind}), now on the {line} · {earned}"

    def _at_and_line(self, words: list[str]) -> tuple[str | None, str]:
        if not words or _word(words[0]) != "at":
            return None, _get_name(self.lines, words)
        for i in range(2, len(words)):
            station = self.stations.find(" ".join(words[1:i]))
            line = _find_name(self.lines, words[i:])
            if station is not None and line is not None:
                return station, line
        raise AdminError("Couldn't read that as 'at STATION, taking LINE'.")

    def _veto(self, team: Team, words: list[str]) -> str:
        if self.game.veto_challenges(team.role_id):
            said = f"{team.name} vetoed · {POWERUP_NAMES['efficiency']} used, so no wait"
        else:
            self.game.schedule_event(0, VETO_MINUTES, 0, self.game.unveto, team.role_id)
            ends = self.game.game_time_now() + VETO_MINUTES * MIN
            said = f"{team.name} vetoed · veto period ends {self.clock(ends)}"
        return "\n".join([said, *_offer(self.game.get_snake(team), full=True)])

    def _buy(self, team: Team, words: list[str]) -> str:
        powerup = _get_name(self.powerups, words)
        drawn = self.game.buy_powerup(team.role_id, powerup)
        said = f"{team.name} bought {POWERUP_NAMES[powerup]} · {self.game.get_snake(team).coins} coins left"
        if drawn:
            said += f" · drew {', '.join(c.name for c in drawn)}"
        return said

    def _choose(self, team: Team, words: list[str]) -> str:
        options = self.game.get_snake(team).curse_choice
        if not options:
            raise AdminError(f"{team.name} has no curses to choose from. They need a 'buy Curse' line first.")
        names = _Names("curse", [*((c.name, c.id) for c in options), *((c.id, c.id) for c in options)])
        curse_id = _find_name(names, words)
        if curse_id is None:
            drawn = ", ".join(c.name for c in options)
            deck = self.game.curse_deck.catalog() if self.game.curse_deck else []
            wanted = _find_name(
                _Names("curse", [*((c.name, c.name) for c in deck), *((c.id, c.name) for c in deck)]), words
            )
            raise AdminError(
                f"{team.name}'s draw came out as {drawn}, and '{' '.join(words)}' isn't one of them. The draw only "
                "matches the real one when every move before it is in the file, in the right order, so check for "
                f"a missing move. If there isn't one, replace this line with a fix: {team.name} give curse "
                f"{wanted or ' '.join(words)}"
            )
        return f"{team.name} kept {self.game.choose_curse(team.role_id, curse_id).name}"

    def _play(self, team: Team, words: list[str]) -> str:
        found = _take_name(self.powerups, words)
        if found is None:
            raise AdminError(f"Play which powerup? They are: {', '.join(POWERUP_NAMES.values())}.")
        powerup, rest = found
        if powerup in ("jump", "detour", "curse"):
            return getattr(self, f"_{powerup}")(team, rest)
        self.game.play_normal_powerup(team.role_id, powerup)
        return f"{team.name} played {POWERUP_NAMES[powerup]}"

    def _jump(self, team: Team, words: list[str]) -> str:
        station = _get_name(self.stations, words)
        self.game.play_jump(team.role_id, station=station)
        return f"{team.name} jumped {station}"

    def _detour(self, team: Team, words: list[str]) -> str:
        line = _get_name(self.lines, words)
        self.game.play_detour(team.role_id, line=line)
        parked = " (takes effect when they complete)" if self.game.get_snake(team).pending_detour else ""
        return f"{team.name} played Detour onto the {line}{parked}"

    def _curse(self, team: Team, words: list[str]) -> str:
        found = _take_name(self.teams, words)
        if found is None:
            raise AdminError("Curse which team? Write it as: TEAM curse OTHER-TEAM with CURSE.")
        target_id, rest = found
        held = self.game.get_snake(team).held_curses
        if not held:
            raise AdminError(f"{team.name} isn't holding a curse. They need 'buy Curse' and 'choose' lines first.")
        curse_id = _get_name(_Names("curse", [*((c.name, c.id) for c in held), *((c.id, c.id) for c in held)]), rest)
        curse = self.game.play_curse(team.role_id, target_team_id=target_id, curse_id=curse_id)
        return f"{team.name} cursed {self.game.get_team(target_id).name} with {curse.name}"

    def _declare(self, team: Team, words: list[str]) -> str:
        self.game.declare_win(team.role_id)
        settles = self.game.game_time_now() + DECLARE_WIN_WINDOW_MINUTES * MIN
        return f"{team.name} declared a win · settled at {self.clock(settles)}"

    def _concede(self, team: Team, words: list[str]) -> str:
        self.game.admin_knock_out(team.role_id)
        return f"{team.name} conceded"

    # Fixes

    def _fix_coins(self, team: Team, words: list[str]) -> str:
        m = re.fullmatch(r"([+-]?)(\d+)", "".join(words))
        if m is None:
            raise AdminError("Give a number: 'coins 7' sets them, 'coins +2' or 'coins -1' changes them.")
        have = self.game.get_snake(team).coins
        coins = {"+": have + int(m[2]), "-": have - int(m[2]), "": int(m[2])}[m[1]]
        if coins < 0:
            raise AdminError(f"{team.name} only has {have} coins.")
        self.game.admin_set_coins(team.role_id, coins)
        return f"{team.name} now has {coins} coins"

    def _card(self, team: Team, words: list[str], *, held: bool) -> tuple[str, str | None]:
        """A powerup, and for a curse which one: any curse when giving, one they hold when taking."""
        found = _take_name(self.powerups, words)
        if found is None:
            raise AdminError(f"Which powerup? They are: {', '.join(POWERUP_NAMES.values())}.")
        powerup, rest = found
        if powerup != "curse":
            if rest:
                raise AdminError(f"Didn't understand '{' '.join(rest)}' after {POWERUP_NAMES[powerup]}.")
            return powerup, None
        if not rest:
            raise AdminError(f"Which curse? Write it as: {team.name} {'take' if held else 'give'} curse Get a Melon.")
        if self.game.curse_deck is None:
            raise AdminError("This game has no curses.")
        pool = self.game.get_snake(team).held_curses if held else self.game.curse_deck.catalog()
        if not pool:
            raise AdminError(f"{team.name} isn't holding any curses.")
        name_of = {c.id: c.name for c in pool}
        names = _Names("curse", [*((c.name, c.id) for c in pool), *((c.id, c.id) for c in pool)], label=name_of.get)
        return "curse", _get_name(names, rest)

    def _card_name(self, powerup: str, curse_id: str | None) -> str:
        if curse_id is None:
            return POWERUP_NAMES[powerup]
        return f"the curse {self.game.curse_deck.get(curse_id).name}"

    def _fix_give(self, team: Team, words: list[str]) -> str:
        powerup, curse_id = self._card(team, words, held=False)
        choosing = bool(self.game.get_snake(team).curse_choice and curse_id)
        self.game.admin_give(team.role_id, powerup, curse_id or "")
        settled = " (settling the draw they were choosing from)" if choosing else ""
        return f"gave {team.name} {self._card_name(powerup, curse_id)}{settled}"

    def _fix_take(self, team: Team, words: list[str]) -> str:
        powerup, curse_id = self._card(team, words, held=True)
        self.game.admin_take(team.role_id, powerup, curse_id or "")
        return f"took {self._card_name(powerup, curse_id)} from {team.name}"

    def _fix_line(self, team: Team, words: list[str]) -> str:
        line = _get_name(self.lines, words)
        self.game.admin_set_line(team.role_id, line)
        return f"{team.name} is now on the {line}, at {self.game.get_snake(team).anchor}"

    def _fix_cancel(self, team: Team, words: list[str]) -> str:
        self.game.admin_cancel_challenge(team.role_id)
        return f"{team.name}'s challenge is called off; they're back at {self.game.get_snake(team).anchor}"

    def _fix_end(self, team: Team, words: list[str]) -> str:
        if [_word(w) for w in words[:1]] != ["veto"]:
            raise AdminError("Did you mean 'end veto'?")
        self.game.admin_end_veto(team.role_id)
        return f"{team.name}'s veto period is over"

    def _fix_out(self, team: Team, words: list[str]) -> str:
        self.game.admin_knock_out(team.role_id)
        return f"{team.name} is out of the game"

    def _fix_back(self, team: Team, words: list[str]) -> str:
        self.game.admin_bring_back(team.role_id)
        return f"{team.name} is back in the game, at {self.game.get_snake(team).anchor}"

    def _objective(self, words: list[str]) -> str:
        action = _word(words[0]) if words else ""
        if action not in ("add", "remove"):
            raise AdminError("Write it as 'objective add STATION' or 'objective remove STATION'.")
        station = _get_name(self.stations, words[1:])
        if action == "add":
            self.game.admin_add_objective(station)
            return f"objective added at {station}"
        self.game.admin_remove_objective(station)
        return f"objective at {station} taken away"


async def replay(game: GameState, changes: list[Change], resume_at: int, clock: Callable[[int], str]) -> _Session:
    """Applies changes in order, firing any timers that fell due in between."""
    session = _Session(game, clock)
    for change in changes:
        session.apply(change)
    session.finish(resume_at)
    return session


# --- Reading a changes file ---------------------------------------------------------------------


def _parse_clock(text: str) -> time | None:
    m = re.fullmatch(r"(\d{1,2})[:.](\d{2})(?:[:.](\d{2}))?", text.strip())
    if not m:
        return None
    h, mi, s = int(m[1]), int(m[2]), int(m[3] or 0)
    return time(h, mi, s) if h < 24 and mi < 60 and s < 60 else None


def _since(clock: time, backup: datetime, now: datetime, line: int) -> datetime:
    at = datetime.combine(backup.date(), clock, tzinfo=backup.tzinfo)
    if at < backup:
        at += timedelta(days=1)  # past midnight
        if at > now:
            raise AdminError(
                f"Line {line}: {clock:%H:%M} is before the backup ({backup:%H:%M}), so that move is already "
                "in the save. Leave it out."
            )
    elif at > now:
        raise AdminError(f"Line {line}: {clock:%H:%M} hasn't happened yet.")
    return at


def read_changes(text: str, now: datetime) -> Changes:
    """Reads a changes file, resolving its times against `now`."""
    backup_at: tuple[int, time] | None = None
    until_at: tuple[int, time | None] | None = None  # a time of None means "now"
    changes = Changes()
    raw: list[tuple[int, time | None, str]] = []
    for no, line in enumerate(text.splitlines(), 1):
        line = re.sub(r"#(?!\d).*", "", line).strip()
        if not line:
            continue
        words = line.split()
        head = _word(words[0])
        if head in ("until", "down") and [_word(w) for w in words[1:]] == ["now"]:
            until_at = (no, None)
            continue
        if head in ("backup", "until", "down"):
            clock = _parse_clock(" ".join(_skip(words[1:])))
            if clock is None:
                raise AdminError(f"Line {no}: '{line}' - give the time as HH:MM.")
            if head != "backup":
                until_at = (no, clock)
            elif backup_at is not None:
                raise AdminError(f"Line {no}: there's already a backup line, on line {backup_at[0]}.")
            else:
                backup_at = (no, clock)
            continue
        if head == "undo":
            m = re.fullmatch(r"#?(\d+)", "".join(words[1:]))
            if m is None:
                raise AdminError(f"Line {no}: write it as 'undo 57', with the number from the history.")
            changes.undo.append((no, int(m[1])))
            continue
        clock = _parse_clock(words[0])
        if clock is not None and len(words) == 1:
            raise AdminError(f"Line {no}: there's a time but nothing after it.")
        raw.append((no, clock, line.split(None, 1)[1] if clock is not None else line))

    if backup_at is None:
        if until_at is not None or any(clock for _, clock, _ in raw):
            raise AdminError("Lines with a time need a 'backup HH:MM' line too: the time your backup was posted.")
    else:
        changes.backup = datetime.combine(now.date(), backup_at[1], tzinfo=now.tzinfo)
        if changes.backup > now:
            changes.backup -= timedelta(days=1)
    previous = changes.backup
    for no, clock, entry in raw:
        at = None
        if clock is not None:
            at = _since(clock, changes.backup, now, no)
            if at < previous:
                raise AdminError(f"Line {no}: {clock:%H:%M} is earlier than the line before it. Put them in order.")
            previous = at
        changes.entries.append((no, at, entry))
    if until_at is not None:
        changes.until = now if until_at[1] is None else _since(until_at[1], changes.backup, now, until_at[0])
    return changes


# --- Output -------------------------------------------------------------------------------------


def _facts(game: GameState) -> list[object]:
    """What a written save has to load back as."""
    teams = [
        (
            t.name,
            s.anchor,
            s.front,
            s.neck_active,
            s.travel_line,
            s.announced_line,
            s.coins,
            tuple(s.hand),
            tuple(c.id for c in s.held_curses),
            tuple(c.id for c in s.curse_choice),
            s.vetoed,
            s.crashed,
            s.conceded,
            s.win_declared,
            s.declare_cooldown,
            s.free_vetoes,
            s.blocked_station,
            s.pending_detour,
            s.objectives_won,
            tuple(c.id for c in s.curses),
            tuple(c.id for c in s.offer) if s.offer else None,
        )
        for t in game.teams
        for s in [game.get_snake(t)]
    ]
    claims = sorted((station, team.role_id) for station, team in game.map.all_claims().items())
    timers = [(e.__type__, e.__time__, list(e.args), dict(e.kwargs)) for e in game.scheduled_events]
    return [teams, claims, list(game.objectives), sorted(game.jumped_stations), timers]


def _summary(game: GameState, clock: Callable[[int], str]) -> list[str]:
    width = max(len(t.name) for t in game.teams)
    out = []
    for team in game.teams:
        s = game.get_snake(team)
        if s.crashed or s.conceded:
            bits = [f"OUT ({'crashed' if s.crashed else 'conceded'}) at {s.anchor}"]
        else:
            bits = [f"Anchor {s.anchor}"]
            if s.travel_line:
                told = f" (told everyone the {s.announced_line})" if s.announced_line != s.travel_line else ""
                bits.append(f"on the {s.travel_line}{told}")
            bits.append(f"Neck {_route(game.neck(team))}" if s.neck_active else "no challenge")
        bits.append(f"{s.coins} coins")
        if s.hand:
            bits.append("hand: " + ", ".join(POWERUP_NAMES.get(p, p) for p in s.hand))
        if s.held_curses:
            bits.append("holding " + ", ".join(c.name for c in s.held_curses))
        if s.curse_choice:
            bits.append("still to choose from " + ", ".join(c.name for c in s.curse_choice))
        if s.vetoed:
            ends = [e.__time__ for e in game.scheduled_events if e.__type__ == "unveto" and team.role_id in e.args]
            bits.append(f"vetoed until {clock(ends[0])}" if ends else "vetoed")
        if s.win_declared:
            bits.append("win declared")
        out.append(f"  {team.name:<{width}}  " + " · ".join(bits))
        if s.neck_active and not s.eliminated:
            out += [f"  {'':<{width}}    {line}" for line in _offer(s, full=False)]
    if game.objectives:
        out.append(f"  Live objectives: {', '.join(game.objectives)}")
    winner = game.winner()
    if winner is not None:
        out.append(f"  GAME OVER: {winner.name} won")
    elif game.status == Status.END:
        out.append("  GAME OVER: a draw")
    return out


def _show(game: GameState, raw: dict, game_id: int) -> None:
    labels = _Labels.of(raw)
    events = raw["event_log"]
    print(f"Game {game_id}, {_elapsed(raw['last_update'])} into the game (game time, not counting downtime).")
    print("\nWhere everyone stands:")
    for line in _summary(game, _elapsed):
        print(line)
    print("\nHistory - use these numbers with 'undo':")
    for number, i in enumerate(_history(raw), 1):
        e = events[i]
        timer = "" if e["__type__"] in _MOVE_EVENTS | _FIX_EVENTS else "(timer) "
        what = _describe(labels, e["__type__"], e["args"], e["kwargs"])
        print(f"  #{number:<4}{_elapsed(e['__time__']):>6}  {timer}{what}")
    coming = [e for e in events if e["__time__"] > raw["last_update"]]
    if coming:
        print("\nComing up:")
        for e in coming:
            print(f"  {_elapsed(e['__time__']):>11}  {_describe_due(labels, e['__type__'], e['args'], e['kwargs'])}")


def _game_id(path: Path) -> int:
    m = re.match(r"\d{15,}", path.stem)
    if m is None:
        raise AdminError(
            f"Can't tell which game '{path.name}' is from its name. Add --game-id and the game's id to the command."
        )
    return int(m[0])


async def run(
    save_path: Path,
    changes_path: Path | None = None,
    *,
    out_dir: Path = Path("save"),
    map_path: Path | None = None,
    game_id: int | None = None,
    now: datetime | None = None,
    changes_text: str | None = None,
    until_now: bool = False,
) -> Outcome | None:
    game_id = game_id or _game_id(save_path)
    now = now or datetime.now().astimezone()
    target = out_dir / f"{game_id}.json"
    if changes_path is not None and target.resolve() == save_path.resolve():
        raise AdminError(
            "The save you're changing is in the folder the result goes to, and would be overwritten. Move it "
            "somewhere else first (such as 'backups'), so that you can run this again if you need to."
        )
    raw, game = _load_save(save_path, game_id)
    if changes_path is None:
        _show(game, raw, game_id)
        return None

    try:
        text = changes_path.read_text(encoding="utf-8") if changes_text is None else changes_text
    except OSError as e:
        raise AdminError(f"Couldn't read the changes file: {e}") from None
    changes = read_changes(text, now)
    if until_now:
        if changes.backup is None:
            raise AdminError(
                "The changes file needs its 'backup HH:MM' line, so the script knows the time in the game."
            )
        changes.until = now
    labels = _Labels.of(raw)
    base = raw["last_update"]
    if changes.backup is not None:
        backup = changes.backup
        clock = lambda t: (backup + timedelta(milliseconds=t - base)).strftime("%H:%M")
        print(f"Game {game_id}, backup posted at {backup:%H:%M}.")
    else:
        clock = _elapsed
        print(f"Game {game_id}, {_elapsed(base)} into the game.")

    history = _history(raw)
    recent = [(n, i) for n, i in enumerate(history, 1) if raw["event_log"][i]["__type__"] in _MOVE_EVENTS | _FIX_EVENTS]
    if recent:
        print("The save's latest moves:")
        for number, i in recent[-3:]:
            e = raw["event_log"][i]
            print(f"  #{number:<4}~{clock(e['__time__'])}  {_describe(labels, e['__type__'], e['args'], e['kwargs'])}")
    undone: list[str] = []
    if changes.undo:
        game, undone = _load_undone(raw, changes.undo, game_id, labels)
        undone = [f"  undone: {line}" for line in undone]
        print("\nUndone:")
        for line in undone:
            print(line)

    to_game = (lambda at: base + round((at - changes.backup).total_seconds() * 1000)) if changes.backup else None
    entries, at = [], base
    for no, when, text in changes.entries:
        at = to_game(when) if when is not None else at
        entries.append(Change(at, text, no, timed=when is not None))
    resume_at = max([base, *(c.at for c in entries), *([to_game(changes.until)] if changes.until else [])])
    if entries:
        print("\nChanges:")
    session = await replay(game, entries, resume_at, clock)
    summary = _summary(game, clock)
    print("\nWhere everyone stands:")
    for line in summary:
        print(line)

    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=out_dir) as tmp:
        with _quiet():
            await game.save(Path(tmp))
            reloaded = _Game.load(Path(tmp), game_id)
        if _facts(reloaded) != _facts(game):
            raise AdminError(
                "The new save doesn't load back the way it should, so it hasn't been used. "
                "Send the save and your changes file to Fin."
            )
        os.replace(Path(tmp, f"{game_id}.json"), target)
    print(f"\nWrote {target}")

    map_path = map_path or Path("out") / f"admin_{game_id}.png"
    try:
        map_path.parent.mkdir(parents=True, exist_ok=True)
        svg = map_path.with_suffix(".svg")
        with _quiet():
            render.render_map(reloaded, svg)
            render.svg_to_png(svg, map_path)
        map_note = f"Map: {map_path}"
        print(f"{map_note} - check it against what the teams say.")
    except Exception as e:  # noqa: BLE001 - the save is already written; the map is only a check
        map_note = f"Couldn't draw the map ({e}). The save is fine."
        print(map_note)
    return Outcome(session.said, session.said[session.after_last :], undone, summary, map_note)


def live(
    save_path: Path,
    changes_path: Path,
    *,
    out_dir: Path = Path("save"),
    map_path: Path | None = None,
    game_id: int | None = None,
) -> int:
    """Takes changes one at a time at a prompt, adding each to the changes file only if it works."""
    print(
        "Type a move or a fix, then Enter. It's stamped with the time now unless you start it with one, and only\n"
        "added to the file if it works. Enter on its own shows anything that has come due. 'quit' stops."
    )
    if not changes_path.is_file():
        print(f"There's no changes file at {changes_path}. Live mode carries on from the one you made in section 1.")
        return 1
    shown: list[str] = []

    def attempt(line: str | None, *, first: bool = False) -> None:
        nonlocal shown
        try:
            text = changes_path.read_text(encoding="utf-8")
        except OSError as e:
            print(f"Couldn't read {changes_path}: {e}")
            return
        candidate = text if line is None else text + ("\n" if text and not text.endswith("\n") else "") + line + "\n"
        try:
            with _quiet():
                outcome = asyncio.run(
                    run(
                        save_path,
                        changes_path,
                        out_dir=out_dir,
                        map_path=map_path,
                        game_id=game_id,
                        changes_text=candidate,
                        until_now=True,
                    )
                )
        except AdminError as e:
            why, prefix = str(e), f"Line {len(candidate.splitlines())}: "
            if line is not None and why.startswith(prefix):  # they've just typed it: skip straight to the reason
                why = why.split("\n", 1)[1].strip() if "\n" in why else why.removeprefix(prefix)
            if "earlier than the line before it" in why:
                why += " To add a late move, type 'quit', put it in the right place in the file, and start again."
            print(f"Not done, and not added to the file. {why}")
            return
        if line is not None:
            changes_path.write_text(candidate, encoding="utf-8")
        reported = outcome.undone + outcome.said
        left, fresh = Counter(shown), []
        for said in reported:  # what wasn't in the last run's report, in order
            if left[said]:
                left[said] -= 1
            else:
                fresh.append(said)
        shown = reported
        if not first:
            print("\n".join(fresh) if fresh else "  Nothing new.")
        elif outcome.after_last:
            print("\nCame due since the last move:")
            print("\n".join(outcome.after_last))
        print("\nWhere everyone stands:")
        print("\n".join(outcome.summary))
        print(outcome.map_note)

    attempt(None, first=True)
    while True:
        try:
            line = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if _word(line) in ("quit", "exit"):
            return 0
        words = line.split()
        if words and _parse_clock(words[0]) is None and _word(words[0]) not in ("undo", "until", "down", "backup"):
            line = f"{datetime.now().astimezone():%H:%M} {line}"
        attempt(line or None)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="admin.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("save", type=Path, help="a save file: a backup from Discord, or one from the save folder")
    parser.add_argument("changes", type=Path, nargs="?", help="your changes file; leave it out to just look")
    parser.add_argument("--game-id", type=int, help="the game's id, if the save's file name doesn't start with it")
    parser.add_argument("--out", type=Path, default=Path("save"), help="where to write the new save (default: save)")
    parser.add_argument("--map", type=Path, help="where to draw the map (default: out/admin_<game id>.png)")
    parser.add_argument("--live", action="store_true", help="take changes one at a time, when running a game by hand")
    args = parser.parse_args(argv)
    if args.live:
        if args.changes is None:
            parser.error("--live needs a changes file")
        return live(args.save, args.changes, out_dir=args.out, map_path=args.map, game_id=args.game_id)
    try:
        asyncio.run(run(args.save, args.changes, out_dir=args.out, map_path=args.map, game_id=args.game_id))
    except AdminError as e:
        sys.stdout.flush()
        print(f"\nStopped. {e}\nNothing was written.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
