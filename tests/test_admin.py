"""admin.py: looking inside a save, replaying the moves lost after a backup, and fixing what's wrong."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest

import admin
from admin import AdminError, Change, read_changes, replay
from config import DECLARE_WIN_COST, EASIER_REWARD, HARDER_REWARD
from game import GameState
from jloxgame.state import Status
from new_game import new_game

GAME_ID = 1541016481268240495
MIN = 60_000
_DRAW_MAP = (admin.render.render_map, admin.render.svg_to_png)


@pytest.fixture(autouse=True)
def _no_map(monkeypatch):
    """Drawing a map takes a third of a second; only test_command_writes_a_save_the_bot_can_load needs one."""
    monkeypatch.setattr(admin.render, "render_map", lambda *a, **k: None)
    monkeypatch.setattr(admin.render, "svg_to_png", lambda *a, **k: None)


def _new() -> GameState:
    game = new_game({"Alpha": "Wembley Park", "Beta": "Stratford"})
    game.thread_id = GAME_ID
    return game


def _at(game: GameState, t: int) -> None:
    game.game_time_now = lambda: t


async def _bot(game: GameState, t: int, move=None) -> None:
    """What the live bot does by game time t: its scheduler ticks just after each timer falls due."""
    while game.scheduled_events and game.scheduled_events[0].__time__ < t:
        _at(game, game.scheduled_events[0].__time__ + 1)
        await game.schedule_tick()
    _at(game, t)
    await game.schedule_tick()
    if move:
        move(game)
        if game.winner() is not None and game.status == Status.RUNNING:
            game.won_game()  # as the bot does after a move that leaves one team standing


def _state(game: GameState) -> dict:
    snakes = {
        t.name: (
            s.anchor,
            s.front,
            s.neck_active,
            s.travel_line,
            s.announced_line,
            s.pending_detour,
            s.coins,
            tuple(s.hand),
            [c.id for c in s.held_curses],
            [c.id for c in s.curse_choice],
            [c.id for c in s.curses],
            s.vetoed,
            s.free_vetoes,
            s.crashed,
            s.win_declared,
            s.objectives_won,
            tuple(c.id for c in s.offer) if s.offer else None,
            sorted(s.seen_challenges),
        )
        for t in game.teams
        for s in [game.get_snake(t)]
    }
    return {
        "status": game.status,
        "snakes": snakes,
        "claims": sorted((station, team.name) for station, team in game.map.all_claims().items()),
        "objectives": list(game.objectives),
        "jumped": sorted(game.jumped_stations),
    }


def _timers_of(game: GameState, kind: str) -> list:
    return [e for e in game.scheduled_events if e.__type__ == kind]


def _timers(game: GameState) -> list[tuple]:
    return [(e.__type__, list(e.args), dict(e.kwargs)) for e in game.scheduled_events]


async def _round_trip(tmp_path) -> tuple[GameState, GameState]:
    """Play a game live past a backup, then rebuild it from the backup; returns (live, rebuilt)."""
    live = _new()
    a, b = (t.role_id for t in live.teams)
    await _bot(live, 1 * MIN, lambda g: g.complete_challenge(a, "Jubilee"))
    await _bot(live, 2 * MIN, lambda g: g.complete_challenge(b, "Central"))
    await _bot(live, 50 * MIN, lambda g: g.request_challenge(a, "Bond Street"))
    await _bot(live, 55 * MIN)
    (tmp_path / "backup").mkdir()
    await live.save(tmp_path / "backup")

    # The moves lost with the VPS. They straddle the first objective, due at ~60 minutes.
    lost = [
        (57 * MIN, "Alpha complete Central harder", lambda g: g.complete_challenge(a, "Central", hard=True)),
        (58 * MIN, "Beta request Liverpool Street", lambda g: g.request_challenge(b, "Liverpool Street")),
        (59 * MIN, "Beta veto", lambda g: g.veto_challenges(b)),
        (61 * MIN, "Beta buy curse", lambda g: g.buy_powerup(b, "curse")),
    ]
    for t, _, move in lost:
        await _bot(live, t, move)
    kept = live.get_snake(live.teams[1]).curse_choice[0]
    lost += [
        (62 * MIN, f"Beta choose {kept.name}", lambda g: g.choose_curse(b, kept.id)),
        (63 * MIN, f"Beta curse Alpha with {kept.name}", lambda g: g.play_curse(b, target_team_id=a, curse_id=kept.id)),
        (64 * MIN, "Alpha buy Detour", lambda g: g.buy_powerup(a, "detour")),
        (65 * MIN, "Alpha detour Elizabeth", lambda g: g.play_detour(a, line="Elizabeth")),
    ]
    for t, _, move in lost[4:]:
        await _bot(live, t, move)
    await _bot(live, 66 * MIN)

    _, game = admin._load_save(tmp_path / "backup" / f"{GAME_ID}.json", GAME_ID)
    await replay(game, [Change(t, text, i) for i, (t, text, _) in enumerate(lost, 1)], 66 * MIN, str)
    (tmp_path / "out").mkdir()
    await game.save(tmp_path / "out")
    return live, GameState.load(tmp_path / "out", GAME_ID)


@pytest.fixture
def round_trip(tmp_path) -> tuple[GameState, GameState]:
    async def go():
        return await _round_trip(tmp_path)

    return asyncio.run(go())


def test_rebuilt_save_loads_as_the_live_game(round_trip):
    live, rebuilt = round_trip
    assert _state(rebuilt) == _state(live)


def test_random_draws_come_out_the_same(round_trip):
    live, rebuilt = round_trip
    for team in live.teams:
        assert rebuilt.get_snake(team).seen_challenges == live.get_snake(team).seen_challenges
    assert rebuilt.get_snake(rebuilt.teams[0]).curses == live.get_snake(live.teams[0]).curses


def test_no_move_is_left_waiting_as_a_timer(round_trip):
    live, rebuilt = round_trip
    assert not {e.__type__ for e in rebuilt.scheduled_events} & admin._MOVE_EVENTS
    assert _timers(rebuilt) == _timers(live)


def test_veto_period_runs_from_the_real_veto(round_trip):
    live, rebuilt = round_trip

    def veto_ends(game):  # gone off already, or still to come
        return [e.__time__ for e in game.event_log + game.scheduled_events if e.__type__ == "unveto"]

    assert veto_ends(rebuilt)
    assert veto_ends(rebuilt) == pytest.approx(veto_ends(live), abs=1000)  # the bot's tick is a second


def test_timer_due_between_moves_fires_between_them(round_trip):
    _, rebuilt = round_trip
    kinds = [e.__type__ for e in rebuilt.event_log if e.__type__ in admin._MOVE_EVENTS | {"new_objective"}]
    objective = kinds.index("new_objective")
    assert kinds[objective - 1] == "veto_challenges"
    assert kinds[objective + 1] == "buy_powerup"


def test_backup_restart_is_not_logged_as_downtime(round_trip):
    _, rebuilt = round_trip
    reloads = [e for e in rebuilt.event_log if e.__type__ == "__reload__"]
    assert len(reloads) == 1  # only the load in the test itself


# --- applying single moves -------------------------------------------------------------------


def _apply(game: GameState, *texts: str, start: int = MIN) -> None:
    moves = [Change(start + i * MIN, text, i + 1) for i, text in enumerate(texts)]

    async def go():
        await replay(game, moves, moves[-1].at, str)

    asyncio.run(go())


def _error(game: GameState, *texts: str) -> str:
    with pytest.raises(AdminError) as e:
        _apply(game, *texts)
    return str(e.value)


@pytest.mark.parametrize(
    "text",
    [
        "Alpha complete Jubilee",
        "alpha COMPLETED jubilee line",
        "Alpha completed at Wembley Park, taking the Jubilee line",
        "Alpha completes at wembley park on the Jubilee.",
    ],
)
def test_first_completion_forms(text):
    game = _new()
    _apply(game, text)
    assert game.get_snake(game.teams[0]).travel_line == "Jubilee"


@pytest.mark.parametrize(
    ("text", "hard"),
    [
        ("Alpha complete Central harder", True),
        ("Alpha completed at Bond Street, taking the Central Line, hard", True),
        ("Alpha complete central easier", False),
    ],
)
def test_completion_difficulty(text, hard):
    game = _new()
    game.bonus_interchanges = set()
    _apply(game, "Alpha complete Jubilee", "Alpha request Bond Street")
    coins = game.get_snake(game.teams[0]).coins
    _apply(game, text, start=10 * MIN)
    snake = game.get_snake(game.teams[0])
    assert snake.anchor == "Bond Street"
    assert snake.coins - coins == (HARDER_REWARD if hard else EASIER_REWARD)


def test_team_names_with_spaces():
    game = new_game({"Team Fin": "Wembley Park", "Team Anshul": "Stratford"})
    game.thread_id = GAME_ID
    _apply(game, "Team Fin complete Jubilee", "team anshul complete Central")
    assert [game.get_snake(t).travel_line for t in game.teams] == ["Jubilee", "Central"]


@pytest.mark.parametrize(
    ("kind", "typed", "key"),
    [
        ("stations", "kings cross", "King's Cross"),
        ("stations", "King's Cross St Pancras", "King's Cross"),
        ("stations", "Monument", "Bank"),
        ("stations", "LIVERPOOL STREET", "Liverpool Street"),
        ("lines", "Piccadilly", "Picc"),
        ("lines", "Hammersmith & City", "H&C"),
        ("lines", "Waterloo and City Line", "W&C"),
        ("lines", "Bank Branch", "Bank Branch"),
    ],
)
def test_names_are_found_however_they_are_typed(kind, typed, key):
    names = getattr(admin._Session(_new(), str), kind)
    assert names.get(typed) == key


@pytest.mark.parametrize("words", ["Get a Melon", "with Get a Melon", "get a melon"])
def test_names_that_start_with_a_filler_word(words):
    curses = admin._Names("curse", [("Get a Melon", "melon"), ("No Way Out", "out")])
    assert admin._get_name(curses, words.split()) == "melon"


def test_team_names_that_start_with_a_filler_word():
    teams = admin._Names("team", [("The Snakes", 1), ("Beta", 2)])
    assert admin._take_name(teams, ["The", "Snakes", "with", "Get", "a", "Melon"]) == (1, ["with", "Get", "a", "Melon"])


def test_choosing_get_a_melon():
    game = _new()
    melon = next(c for c in game.curse_deck.all() if c.name == "Get a Melon")
    _apply(game, "Beta complete Central")
    game.get_snake(game.teams[1]).curse_choice = [melon]
    _apply(game, "Beta choose Get a Melon", start=10 * MIN)
    assert game.get_snake(game.teams[1]).held_curses == [melon]


def test_powerups_by_id_display_name_or_command():
    names = admin._Session(_new(), str).powerups
    assert {names.get(t) for t in ("efficiency", "Good Service", "good-service")} == {"efficiency"}


def test_veto_with_good_service_sets_no_timer():
    game = _new()
    _apply(game, "Alpha complete Jubilee", "Alpha request Bond Street")
    game.get_snake(game.teams[0]).free_vetoes = 1
    _apply(game, "Alpha veto", start=10 * MIN)
    assert not [e for e in game.scheduled_events if e.__type__ == "unveto"]


def test_crash_is_reported(capsys):
    game = _new()
    _apply(game, "Alpha complete Jubilee", "Beta complete Jubilee", "Alpha request Stratford")
    assert game.get_snake(game.teams[0]).crashed
    assert "Alpha CRASHED" in capsys.readouterr().out


# --- refusing bad moves ----------------------------------------------------------------------


def test_unknown_station_suggests_the_nearest():
    message = _error(_new(), "Alpha complete Jubilee", "Alpha request Bond Stret")
    assert message.startswith("Line 2:")
    assert "Did you mean Bond Street" in message


def test_unknown_team_lists_the_teams():
    assert "The teams are: Alpha, Beta." in _error(_new(), "Gamma complete Jubilee")


def test_completion_needs_a_difficulty_after_the_first():
    message = _error(_new(), "Alpha complete Jubilee", "Alpha request Bond Street", "Alpha complete Central")
    assert "'easier' or 'harder'" in message


def test_completion_at_the_wrong_station():
    message = _error(
        _new(),
        "Alpha complete Jubilee",
        "Alpha request Bond Street",
        "Alpha complete at Green Park, taking the Victoria, harder",
    )
    assert "at Bond Street, not Green Park" in message


def test_engine_refusals_are_reported():
    assert "The game refused this:" in _error(_new(), "Alpha request Bond Street")


def test_a_curse_not_in_the_draw_explains_why():
    game = _new()
    _apply(game, "Beta complete Central", "Beta buy Curse")
    drawn = {c.id for c in game.get_snake(game.teams[1]).curse_choice}
    other = next(c for c in game.curse_deck.all() if c.id not in drawn)
    message = _error(game, f"Beta choose {other.name}")
    assert "check for a missing move" in message


def test_conceding_is_replayed():
    game = _new()
    _apply(game, "Alpha concede")
    assert game.get_snake(game.teams[0]).conceded


def test_a_failed_move_stops_everything_after_it():
    game = _new()
    _error(game, "Alpha complete Jubilee", "Alpha request Nowhere", "Beta complete Central")
    assert game.get_snake(game.teams[1]).travel_line is None


# --- reading a changes file -----------------------------------------------------------------

NOW = datetime(2026, 10, 3, 14, 20, tzinfo=UTC)


def test_reads_times_moves_fixes_and_undos():
    changes = read_changes(
        "backup at 14:03\n# lost moves\n\nundo #57\n14:05 Alpha complete Central harder  # a note\n"
        "Alpha coins +2\n14:07 Beta veto\ndown 14:09\n",
        NOW,
    )
    assert changes.backup == datetime(2026, 10, 3, 14, 3, tzinfo=UTC)
    assert changes.until == datetime(2026, 10, 3, 14, 9, tzinfo=UTC)
    assert changes.undo == [(4, 57)]
    assert changes.entries == [
        (5, datetime(2026, 10, 3, 14, 5, tzinfo=UTC), "Alpha complete Central harder"),
        (6, None, "Alpha coins +2"),
        (7, datetime(2026, 10, 3, 14, 7, tzinfo=UTC), "Beta veto"),
    ]


def test_until_is_another_name_for_down():
    assert read_changes("backup 14:03\nuntil 14:15\n", NOW).until == datetime(2026, 10, 3, 14, 15, tzinfo=UTC)


def test_fixes_alone_need_no_backup_line():
    changes = read_changes("Alpha coins 7\nundo 12\n", NOW)
    assert changes.backup is None
    assert changes.entries == [(1, None, "Alpha coins 7")]


def test_until_earlier_than_the_last_move_is_harmless(tmp_path, backup_file):
    at = datetime.now().astimezone().replace(second=0, microsecond=0)
    (tmp_path / "moves.txt").write_text(
        f"backup {at:%H:%M}\n{at:%H:%M} Alpha complete Central harder\nuntil {at:%H:%M}\n"
    )
    out = tmp_path / "save"
    assert (
        admin.main([str(backup_file), str(tmp_path / "moves.txt"), "--out", str(out), "--map", str(tmp_path / "m.png")])
        == 0
    )
    game = GameState.load(out, GAME_ID)
    assert game.get_snake(game.teams[0]).anchor == "Bond Street"


def test_times_past_midnight():
    changes = read_changes("backup 23:55\n00:02 Beta veto\n", datetime(2026, 10, 4, 0, 10, tzinfo=UTC))
    assert changes.backup == datetime(2026, 10, 3, 23, 55, tzinfo=UTC)
    assert changes.entries[0][1] == datetime(2026, 10, 4, 0, 2, tzinfo=UTC)


@pytest.mark.parametrize(
    ("text", "complaint"),
    [
        ("14:05 Beta veto\n", "need a 'backup HH:MM' line"),
        ("backup 14:03\n14:01 Beta veto\n", "already in the save"),
        ("backup 14:03\n14:25 Beta veto\n", "hasn't happened yet"),
        ("backup 14:03\n14:07 Beta veto\n14:05 Alpha veto\n", "Put them in order"),
        ("backup 14:03\n14:05\n", "a time but nothing after it"),
        ("undo fifty\n", "write it as 'undo 57'"),
        ("backup 14:03\nbackup 14:04\n", "already a backup line"),
    ],
)
def test_bad_changes_files(text, complaint):
    with pytest.raises(AdminError, match=complaint):
        read_changes(text, NOW)


@pytest.mark.parametrize(
    ("name", "expected"),
    [(f"{GAME_ID}.json", GAME_ID), (f"{GAME_ID} (1).json", GAME_ID), (f"{GAME_ID}-1.json", GAME_ID)],
)
def test_game_id_from_downloaded_file_name(tmp_path, name, expected):
    assert admin._game_id(tmp_path / name) == expected


# --- the command -----------------------------------------------------------------------------


@pytest.fixture
def backup_file(tmp_path):
    async def go():
        game = _new()
        a, b = (t.role_id for t in game.teams)
        game.complete_challenge(a, "Jubilee")
        game.complete_challenge(b, "Central")
        game.request_challenge(a, "Bond Street")
        await game.schedule_tick()
        (tmp_path / "downloads").mkdir()
        await game.save(tmp_path / "downloads")

    asyncio.run(go())
    return tmp_path / "downloads" / f"{GAME_ID}.json"


def _moves_file(tmp_path, *moves: str):
    at = datetime.now().astimezone().replace(second=0, microsecond=0)
    lines = [f"backup {at:%H:%M}", *(f"{at:%H:%M} {m}" for m in moves)]
    (tmp_path / "moves.txt").write_text("\n".join(lines) + "\n")
    return tmp_path / "moves.txt"


def test_command_writes_a_save_the_bot_can_load(tmp_path, backup_file, monkeypatch):
    monkeypatch.setattr(admin.render, "render_map", _DRAW_MAP[0])
    monkeypatch.setattr(admin.render, "svg_to_png", _DRAW_MAP[1])
    moves = _moves_file(tmp_path, "Alpha complete Central harder", "Beta request Liverpool Street")
    out = tmp_path / "save"
    code = admin.main([str(backup_file), str(moves), "--out", str(out), "--map", str(tmp_path / "map.png")])
    assert code == 0
    game = GameState.load(out, GAME_ID)
    assert game.get_snake(game.teams[0]).anchor == "Bond Street"
    assert game.neck(game.teams[1])[-1] == "Liverpool Street"
    assert (tmp_path / "map.png").exists()


def test_command_writes_nothing_when_a_move_is_bad(tmp_path, backup_file, capsys):
    moves = _moves_file(tmp_path, "Alpha complete Central harder", "Beta request Liverpool St")
    out = tmp_path / "save"
    assert admin.main([str(backup_file), str(moves), "--out", str(out)]) == 1
    assert not (out / f"{GAME_ID}.json").exists()
    assert "Nothing was written." in capsys.readouterr().err


def test_command_will_not_overwrite_its_own_backup(tmp_path, backup_file, capsys):
    moves = _moves_file(tmp_path, "Alpha complete Central harder")
    assert admin.main([str(backup_file), str(moves), "--out", str(backup_file.parent)]) == 1
    assert "would be overwritten" in capsys.readouterr().err


def test_backup_that_will_not_load(tmp_path, capsys):
    (tmp_path / f"{GAME_ID}.json").write_text("{not json")
    moves = _moves_file(tmp_path, "Alpha veto")
    assert admin.main([str(tmp_path / f"{GAME_ID}.json"), str(moves), "--out", str(tmp_path / "save")]) == 1
    assert "Couldn't load the save" in capsys.readouterr().err


def test_command_refuses_a_save_that_does_not_reload_the_same(tmp_path, backup_file, capsys, monkeypatch):
    calls = iter(range(100))
    monkeypatch.setattr(admin, "_facts", lambda game: next(calls))
    moves = _moves_file(tmp_path, "Alpha complete Central harder")
    out = tmp_path / "save"
    assert admin.main([str(backup_file), str(moves), "--out", str(out)]) == 1
    assert not any(out.iterdir())
    assert "doesn't load back the way it should" in capsys.readouterr().err


# --- the engine's fix events -----------------------------------------------------------------


def _ids(game: GameState) -> tuple[int, int]:
    a, b = (t.role_id for t in game.teams)
    return a, b


def _melon(game: GameState):
    return next(c for c in game.curse_deck.catalog() if c.name == "Get a Melon")


def _reload(game: GameState, tmp_path) -> GameState:
    async def go():
        (tmp_path / "reload").mkdir(exist_ok=True)
        await game.schedule_tick()  # the bot's clock tick; without it, the moves would be saved as timers
        await game.save(tmp_path / "reload")
        return GameState.load(tmp_path / "reload", GAME_ID)

    return asyncio.run(go())


def test_fixes_survive_a_reload(tmp_path):
    game = _new()
    a, b = _ids(game)
    game.complete_challenge(a, "Jubilee")
    game.complete_challenge(b, "Central")
    game.request_challenge(a, "Bond Street")
    game.admin_set_coins(a, 11)
    game.admin_give(a, "jump")
    game.admin_give(b, "curse", _melon(game).id)
    game.admin_take(a, "jump")
    game.admin_give(a, "detour")
    game.admin_cancel_challenge(a)
    game.admin_set_line(a, "Met")
    game.admin_add_objective("Green Park")
    game.admin_knock_out(b)
    game.admin_bring_back(b)
    assert _state(_reload(game, tmp_path)) == _state(game)


def test_giving_a_curse_outright_adds_the_card_too():
    game = _new()
    a, _ = _ids(game)
    game.admin_give(a, "curse", _melon(game).id)
    snake = game.get_snake(game.teams[0])
    assert (snake.hand, snake.held_curses) == (["curse"], [_melon(game)])


@pytest.mark.parametrize("from_the_draw", [True, False])
def test_giving_a_curse_settles_a_draw_they_are_choosing_from(from_the_draw):
    game = _new()
    _, b = _ids(game)
    game.complete_challenge(b, "Central")
    drawn = game.buy_powerup(b, "curse")
    given = drawn[0] if from_the_draw else next(c for c in game.curse_deck.all() if c not in drawn)
    game.admin_give(b, "curse", given.id)
    snake = game.get_snake(game.teams[1])
    assert snake.hand == ["curse"]  # the card they paid for, not a second one
    assert (snake.held_curses, snake.curse_choice) == ([given], [])
    assert given not in game.curse_deck.all()  # it's theirs, so nobody can draw it
    assert all(c in game.curse_deck.all() for c in drawn if c != given)  # the rest of the draw goes back


def test_taking_a_curse_takes_its_card():
    game = _new()
    a, _ = _ids(game)
    game.admin_give(a, "curse", _melon(game).id)
    game.admin_take(a, "curse", _melon(game).id)
    snake = game.get_snake(game.teams[0])
    assert (snake.hand, snake.held_curses) == ([], [])


@pytest.mark.parametrize(
    ("fix", "complaint"),
    [
        (lambda g, a: g.admin_set_coins(a, -1), "can't go below 0"),
        (lambda g, a: g.admin_give(a, "teleport"), "Unknown powerup"),
        (lambda g, a: g.admin_give(a, "curse"), "Say which curse to give"),
        (lambda g, a: g.admin_give(a, "jump", "melon"), "Only a curse takes a curse_id"),
        (lambda g, a: g.admin_give(a, "curse", "no-such-curse"), "Unknown curse"),
        (lambda g, a: g.admin_take(a, "jump"), "has no Jump card"),
        (lambda g, a: g.admin_cancel_challenge(a), "first challenge"),
        (lambda g, a: g.admin_set_line(a, "Victoria"), "cancel it first"),  # still on the first challenge
        (lambda g, a: g.admin_end_veto(a), "isn't in a veto period"),
        (lambda g, a: g.admin_bring_back(a), "still in the game"),
        (lambda g, a: g.admin_add_objective("Nowhere"), "Unknown station"),
        (lambda g, a: g.admin_remove_objective("Green Park"), "isn't a live objective"),
    ],
)
def test_bad_fixes_are_refused_and_change_nothing(fix, complaint):
    game = _new()
    before = _state(game)
    with pytest.raises(ValueError, match=complaint):
        fix(game, game.teams[0].role_id)
    assert _state(game) == before


@pytest.mark.parametrize(
    ("fix", "complaint"),
    [
        (lambda g, a: g.admin_take(a, "curse", _melon(g).id), "isn't holding the curse"),
        (lambda g, a: g.admin_set_line(a, "Nowhere Line"), "Unknown line"),
        (lambda g, a: g.admin_cancel_challenge(a), "has no challenge on"),
        (lambda g, a: g.admin_add_objective("Green Park"), "already an objective"),
    ],
)
def test_more_bad_fixes_once_the_game_is_going(fix, complaint):
    game = _new()
    a, _ = _ids(game)
    game.complete_challenge(a, "Jubilee")
    game.admin_give(a, "curse", next(c for c in game.curse_deck.catalog() if c != _melon(game)).id)
    game.admin_add_objective("Green Park")
    before = _state(game)
    with pytest.raises(ValueError, match=complaint):
        fix(game, a)
    assert _state(game) == before


def test_line_fix_needs_a_line_through_the_anchor():
    game = _new()
    a, _ = _ids(game)
    game.complete_challenge(a, "Jubilee")
    with pytest.raises(ValueError, match="doesn't go through Wembley Park"):
        game.admin_set_line(a, "Victoria")
    game.request_challenge(a, "Bond Street")
    with pytest.raises(ValueError, match="cancel it first"):
        game.admin_set_line(a, "Met")


def test_cancel_puts_them_back_with_no_retreat_block():
    game = _new()
    a, _ = _ids(game)
    game.complete_challenge(a, "Jubilee")
    game.request_challenge(a, "Bond Street")
    game.admin_cancel_challenge(a)
    snake = game.get_snake(game.teams[0])
    assert (snake.front, snake.neck_active, snake.offer) == ("Wembley Park", False, None)
    game.request_challenge(a, "Bond Street")


def test_ending_a_veto_removes_its_timer():
    game = _new()
    a, _ = _ids(game)
    game.complete_challenge(a, "Jubilee")
    game.request_challenge(a, "Bond Street")
    game.veto_challenges(a)
    game.admin_end_veto(a)
    assert not game.get_snake(game.teams[0]).vetoed
    assert not [e for e in game.scheduled_events if e.__type__ == "unveto"]


def test_bringing_back_a_crashed_team():
    game = _new()
    a, b = _ids(game)
    game.complete_challenge(a, "Jubilee")
    game.complete_challenge(b, "Jubilee")
    game.request_challenge(a, "Stratford")
    game.admin_bring_back(a)
    snake = game.get_snake(game.teams[0])
    assert (snake.crashed, snake.front, snake.neck_active) == (False, "Wembley Park", False)


def test_bringing_back_a_team_before_their_first_challenge_restores_it():
    game = _new()
    a, _ = _ids(game)
    game.admin_knock_out(a)
    game.admin_bring_back(a)
    snake = game.get_snake(game.teams[0])
    assert snake.neck_active and snake.offer is not None
    game.complete_challenge(a, "Jubilee")


# --- fixes in a changes file -----------------------------------------------------------------


def _fix(game: GameState, *texts: str) -> None:
    changes = [Change(MIN, text, i + 1, timed=False) for i, text in enumerate(texts)]

    async def go():
        await replay(game, changes, MIN, str)

    asyncio.run(go())


@pytest.mark.parametrize(
    ("text", "coins"),
    [("Alpha coins 9", 9), ("Alpha coins +2", 7), ("alpha coins -2", 3), ("Alpha coins + 2", 7)],
)
def test_coin_fixes(text, coins):
    game = _new()
    _fix(game, text)
    assert game.get_snake(game.teams[0]).coins == coins


def test_coin_fix_cannot_go_below_zero():
    with pytest.raises(AdminError, match="only has 5 coins"):
        _fix(_new(), "Alpha coins -9")


def test_card_fixes():
    game = _new()
    _fix(game, "Alpha give Detour", "Alpha give a Good Service", "Beta give curse Get a Melon", "Alpha take detour")
    assert game.get_snake(game.teams[0]).hand == ["efficiency"]
    assert game.get_snake(game.teams[1]).held_curses == [_melon(game)]


def test_other_fixes():
    game = _new()
    _apply(game, "Alpha complete Jubilee", "Alpha request Bond Street", "Alpha veto")
    _fix(
        game,
        "Alpha end veto",
        "Alpha cancel",
        "Alpha line Metropolitan",
        "Beta out",
        "Beta back in",
        "objective add Green Park",
        "objective remove green park",
    )
    alpha, beta = (game.get_snake(t) for t in game.teams)
    assert (alpha.vetoed, alpha.neck_active, alpha.travel_line, alpha.announced_line) == (False, False, "Met", "Met")
    assert not beta.eliminated
    assert game.objectives == []


def test_moves_need_a_time():
    with pytest.raises(AdminError, match="Moves need the time"):
        _fix(_new(), "Alpha complete Jubilee")


def test_a_curse_not_in_the_draw_suggests_a_fix():
    game = _new()
    _apply(game, "Beta complete Central", "Beta buy Curse")
    drawn = {c.id for c in game.get_snake(game.teams[1]).curse_choice}
    other = next(c for c in game.curse_deck.catalog() if c.id not in drawn)
    assert f"Beta give curse {other.name}" in _error(game, f"Beta choose {other.name}")


# --- undo, and looking at a save -------------------------------------------------------------


def _vetoed_game(*, timer_fired: bool) -> GameState:
    """History: #1 Alpha completes, #2 Beta completes, #3 Alpha requests, #4 Alpha vetoes (#5 its timer)."""
    game = _new()
    a, b = _ids(game)

    async def go():
        await _bot(game, 1 * MIN, lambda g: g.complete_challenge(a, "Jubilee"))
        await _bot(game, 2 * MIN, lambda g: g.complete_challenge(b, "Central"))
        await _bot(game, 3 * MIN, lambda g: g.request_challenge(a, "Bond Street"))
        await _bot(game, 4 * MIN, lambda g: g.veto_challenges(a))
        await _bot(game, 30 * MIN if timer_fired else 5 * MIN)

    asyncio.run(go())
    return game


def _saved(game: GameState, tmp_path):
    async def go():
        (tmp_path / "downloads").mkdir(exist_ok=True)
        await game.save(tmp_path / "downloads")

    asyncio.run(go())
    return tmp_path / "downloads" / f"{GAME_ID}.json"


def _run(tmp_path, save, *lines: str) -> tuple[int, GameState | None]:
    (tmp_path / "changes.txt").write_text("\n".join(lines) + "\n")
    out = tmp_path / "save"
    code = admin.main([str(save), str(tmp_path / "changes.txt"), "--out", str(out), "--map", str(tmp_path / "m.png")])
    return code, GameState.load(out, GAME_ID) if code == 0 else None


def test_undo_takes_a_move_out_of_the_history(tmp_path, backup_file):
    code, game = _run(tmp_path, backup_file, "undo 3")
    assert code == 0
    snake = game.get_snake(game.teams[0])
    assert (snake.anchor, snake.neck_active) == ("Wembley Park", False)


@pytest.mark.parametrize("timer_fired", [False, True])
def test_undoing_a_veto_takes_its_timer_too(tmp_path, capsys, timer_fired):
    code, game = _run(tmp_path, _saved(_vetoed_game(timer_fired=timer_fired), tmp_path), "undo #4")
    assert code == 0
    assert not game.get_snake(game.teams[0]).vetoed
    assert not [e for e in game.event_log + game.scheduled_events if e.__type__ == "unveto"]
    assert "#4  Alpha vetoed" in capsys.readouterr().out


def test_undo_says_what_else_it_breaks(tmp_path, backup_file, capsys):
    code, _ = _run(tmp_path, backup_file, "undo 1")
    assert code == 1
    assert "breaks #3 (Alpha requested Bond Street)" in capsys.readouterr().err


@pytest.mark.parametrize(("line", "complaint"), [("undo 5", "no #5"), ("undo 99", "no #99")])
def test_undo_refuses_numbers_that_arent_in_the_history(tmp_path, capsys, line, complaint):
    code, _ = _run(tmp_path, _saved(_vetoed_game(timer_fired=True), tmp_path), line)
    assert code == 1
    assert complaint in capsys.readouterr().err


def test_looking_at_a_save_shows_the_numbered_history(backup_file, capsys):
    assert admin.main([str(backup_file)]) == 0
    out = capsys.readouterr().out
    assert any("#3" in line and "Alpha requested Bond Street" in line for line in out.splitlines())
    assert "Coming up:" in out and "a new objective appears" in out


def test_a_save_that_breaks_says_where(tmp_path, backup_file, capsys):
    raw = json.loads(backup_file.read_text())
    alpha = raw["teams"][0]["role_id"]
    raw["event_log"].insert(
        5, {"__type__": "request_challenge", "__time__": 0, "args": [alpha, "Nowhere"], "kwargs": {}}
    )
    backup_file.write_text(json.dumps(raw))
    assert admin.main([str(backup_file)]) == 1
    assert "It breaks at #" in capsys.readouterr().err


# --- running a game through the script -------------------------------------------------------


def test_requests_and_vetoes_show_the_challenges_drawn(capsys):
    game = _new()
    _apply(game, "Alpha complete Jubilee", "Alpha request Bond Street")
    easier, harder = game.get_snake(game.teams[0]).offer
    out = capsys.readouterr().out
    assert f"easier: {easier.name} (difficulty {easier.difficulty}): {easier.description}" in out
    assert f"harder: {harder.name}" in out
    _apply(game, "Alpha veto", start=10 * MIN)
    easier, harder = game.get_snake(game.teams[0]).offer
    assert f"easier: {easier.name}" in capsys.readouterr().out


def test_summary_lists_the_challenges_on_offer():
    game = _new()
    _apply(game, "Alpha complete Jubilee", "Alpha request Bond Street")
    easier, harder = game.get_snake(game.teams[0]).offer
    summary = "\n".join(admin._summary(game, str))
    assert f"easier: {easier.name} (difficulty {easier.difficulty})" in summary
    assert f"harder: {harder.name} (difficulty {harder.difficulty})" in summary


def test_until_now():
    assert read_changes("backup 14:03\nuntil now\n", NOW).until == NOW


# --- live mode ----------------------------------------------------------------------------------


def test_until_now_runs_the_clock_to_the_present(tmp_path, backup_file):
    at = datetime.now().astimezone().replace(second=0, microsecond=0)
    text = f"backup {at:%H:%M}\n{at:%H:%M} Alpha veto\n"

    async def go(until_now):
        return await admin.run(
            backup_file,
            tmp_path / "unused.txt",
            out_dir=tmp_path / "save",
            map_path=tmp_path / "m.png",
            now=at + timedelta(minutes=20),
            changes_text=text,
            until_now=until_now,
        )

    assert not any("veto period ended" in line for line in asyncio.run(go(False)).said)
    assert any("Alpha's veto period ended" in line for line in asyncio.run(go(True)).said)


def test_live_mode_only_keeps_moves_that_work(tmp_path, backup_file, capsys, monkeypatch):
    changes = tmp_path / "game1.txt"
    changes.write_text(f"backup {datetime.now().astimezone():%H:%M}\n")
    typed = iter(["Beta request Liverpool Street", "Alpha request Nowhere", "", "Alpha coins +2", "quit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(typed))

    assert admin.live(backup_file, changes, out_dir=tmp_path / "save", map_path=tmp_path / "m.png") == 0

    lines = changes.read_text().splitlines()
    assert [line.split(" ", 1)[1] for line in lines[1:]] == ["Beta request Liverpool Street", "Alpha coins +2"]
    assert all(admin._parse_clock(line.split()[0]) for line in lines[1:])  # each was stamped with a time
    out = capsys.readouterr().out
    assert "easier:" in out
    assert "Not done, and not added to the file. There's no station called 'Nowhere'." in out
    assert "Nothing new." in out
    game = GameState.load(tmp_path / "save", GAME_ID)
    assert game.get_snake(game.teams[0]).coins == 7


def test_live_mode_stops_at_end_of_input(tmp_path, backup_file, monkeypatch):
    changes = tmp_path / "game1.txt"
    changes.write_text(f"backup {datetime.now().astimezone():%H:%M}\n")

    def no_more(prompt=""):
        raise EOFError

    monkeypatch.setattr("builtins.input", no_more)
    assert admin.live(backup_file, changes, out_dir=tmp_path / "save", map_path=tmp_path / "m.png") == 0


# --- a fuller round trip: the moves the first one doesn't make -----------------------------------


async def _round_trip_more(tmp_path) -> tuple[GameState, GameState]:
    """Good Service, a free veto, Jump, Retreat, passing an objective, a declaration and a crash, all lost."""
    live = _new()
    a, b = _ids(live)
    await _bot(live, 1 * MIN, lambda g: g.complete_challenge(a, "Jubilee"))
    await _bot(live, 2 * MIN, lambda g: g.complete_challenge(b, "Jubilee"))

    def stock(g):
        g.admin_set_coins(a, 40)
        for card in ("efficiency", "jump", "retreat"):
            g.admin_give(a, card)
        g.admin_add_objective("Finchley Road")

    await _bot(live, 3 * MIN, stock)
    await _bot(live, 5 * MIN)
    (tmp_path / "backup").mkdir()
    await live.save(tmp_path / "backup")

    lost = [
        (6 * MIN, "Alpha play Good Service", lambda g: g.play_normal_powerup(a, "efficiency")),
        (7 * MIN, "Alpha request Bond Street", lambda g: g.request_challenge(a, "Bond Street")),
        (8 * MIN, "Alpha veto", lambda g: g.veto_challenges(a)),
        (9 * MIN, "Alpha jump Holborn", lambda g: g.play_jump(a, station="Holborn")),
        (10 * MIN, "Alpha play Retreat", lambda g: g.play_normal_powerup(a, "retreat")),
        (11 * MIN, "Alpha request Baker Street", lambda g: g.request_challenge(a, "Baker Street")),
        (12 * MIN, "Alpha complete Met harder", lambda g: g.complete_challenge(a, "Met", hard=True)),
        (13 * MIN, "Alpha declare", lambda g: g.declare_win(a)),
        (14 * MIN, "Beta request Finchley Road", lambda g: g.request_challenge(b, "Finchley Road")),
    ]
    for t, _, move in lost:
        await _bot(live, t, move)
    await _bot(live, 40 * MIN)

    _, game = admin._load_save(tmp_path / "backup" / f"{GAME_ID}.json", GAME_ID)
    await replay(game, [Change(t, text, i) for i, (t, text, _) in enumerate(lost, 1)], 40 * MIN, str)
    (tmp_path / "out").mkdir()
    await game.save(tmp_path / "out")
    return live, GameState.load(tmp_path / "out", GAME_ID)


@pytest.fixture
def fuller_round_trip(tmp_path) -> tuple[GameState, GameState]:
    async def go():
        return await _round_trip_more(tmp_path)

    return asyncio.run(go())


def test_fuller_rebuilt_save_matches_the_live_game(fuller_round_trip):
    live, rebuilt = fuller_round_trip
    assert _state(rebuilt) == _state(live)
    assert _timers(rebuilt) == _timers(live)
    _alpha, beta = (rebuilt.get_snake(t) for t in rebuilt.teams)
    assert beta.crashed  # through Baker Street, which Alpha claimed in the catch-up
    assert "Finchley Road" not in rebuilt.objectives  # passed through in Alpha's Neck, which ends it
    assert "Holborn" in rebuilt.jumped_stations
    assert rebuilt.status == Status.END  # Beta's crash left Alpha the last team standing
    assert not _timers_of(rebuilt, "resolve_win_declaration")  # the declaration was still settled


def test_looking_at_a_save_describes_every_kind_of_move(fuller_round_trip, tmp_path, capsys):
    assert admin.main([str(tmp_path / "out" / f"{GAME_ID}.json")]) == 0
    out = capsys.readouterr().out
    for said in (
        "fix: Alpha set to 40 coins",
        "fix: gave Alpha Jump",
        "fix: objective added at Finchley Road",
        "Alpha played Good Service",
        "Alpha jumped Holborn",
        "Alpha played Retreat",
        "Alpha completed (harder), now on the Met",
        "Alpha declared a win",
        "GAME OVER: Alpha won",
    ):
        assert said in out, said


# --- smaller things admin.py does -----------------------------------------------------------------


def test_completion_says_what_was_earned(capsys):
    game = _new()
    game.bonus_interchanges = set()
    _apply(game, "Alpha complete Jubilee", "Alpha request Bond Street", "Alpha complete Central harder")
    assert f"+{HARDER_REWARD} coins, {5 + HARDER_REWARD} in all" in capsys.readouterr().out


def test_summary_says_when_the_game_is_over():
    game = _new()
    _fix(game, "Beta out")
    assert "  GAME OVER: Alpha won" in admin._summary(game, str)


def test_a_settled_declaration_says_whether_it_won():
    game = _new()
    a, _ = _ids(game)
    game.complete_challenge(a, "Jubilee")
    game.get_snake(game.teams[0]).coins = DECLARE_WIN_COST
    game.declare_win(a)  # a lead of one station, nowhere near enough
    result = game.resolve_win_declaration.call_special(True, False, a)
    labels = admin._Labels({a: "Alpha"}, {})
    said = admin._describe(labels, "resolve_win_declaration", [a], {}, result)
    assert said == "Alpha's declared win was settled: failed"


def _last_team_standing(tmp_path):
    """History: #1 Alpha completes, #2 Beta completes, #3 Beta crashes into Alpha, #4 the bot ends the game."""
    game = _new()
    a, b = _ids(game)

    async def go():
        await _bot(game, 1 * MIN, lambda g: g.complete_challenge(a, "Jubilee"))
        await _bot(game, 2 * MIN, lambda g: g.complete_challenge(b, "Jubilee"))
        await _bot(game, 3 * MIN, lambda g: g.request_challenge(b, "Wembley Park"))

    asyncio.run(go())
    assert game.status == Status.END
    return _saved(game, tmp_path)


def test_a_catch_up_move_that_leaves_one_team_standing_ends_the_game(capsys):
    game = _new()
    _apply(game, "Alpha complete Jubilee", "Beta complete Jubilee", "Beta request Wembley Park")
    assert game.status == Status.END
    assert game.event_log[-1].__type__ == "won_game"
    assert "GAME OVER: Alpha won" in capsys.readouterr().out


def test_undoing_the_move_that_won_the_game_reopens_it(tmp_path, capsys):
    save = _last_team_standing(tmp_path)
    code, _ = _run(tmp_path, save, "undo 3")
    assert code == 1
    assert "Undo #4 as well" in capsys.readouterr().err  # the game would otherwise stay over, with nobody winning

    code, game = _run(tmp_path, save, "undo 3", "undo 4")
    assert code == 0
    assert game.status == Status.RUNNING
    assert not game.get_snake(game.teams[1]).crashed


def test_giving_a_curse_from_the_draw_keeps_it_out_of_the_deck():
    game = _new()
    _apply(game, "Beta complete Central", "Beta buy Curse")
    kept, *others = game.get_snake(game.teams[1]).curse_choice
    _fix(game, f"Beta give curse {kept.name}")
    snake = game.get_snake(game.teams[1])
    assert (snake.hand, snake.held_curses, snake.curse_choice) == (["curse"], [kept], [])
    assert kept not in game.curse_deck.all()
    assert all(c in game.curse_deck.all() for c in others)


def test_reload_check_notices_a_parked_detour():
    game = _new()
    before = admin._facts(game)
    game.get_snake(game.teams[0]).pending_detour = "Victoria"
    assert admin._facts(game) != before


def test_a_failed_timer_points_to_the_guide_and_writes_nothing(tmp_path, backup_file, capsys):
    raw = json.loads(backup_file.read_text())
    raw["event_log"].append(  # a timer that can only fail: the veto period of a team that doesn't exist
        {"__type__": "unveto", "__time__": raw["last_update"] + 5 * MIN, "args": [999], "kwargs": {}}
    )
    backup_file.write_text(json.dumps(raw))
    at = datetime.now().astimezone().replace(second=0, microsecond=0) - timedelta(minutes=20)
    (tmp_path / "changes.txt").write_text(f"backup {at:%H:%M}\nuntil now\n")
    out = tmp_path / "save"
    assert admin.main([str(backup_file), str(tmp_path / "changes.txt"), "--out", str(out)]) == 1
    err = capsys.readouterr().err
    assert "The 'unveto' timer" in err and "failed" in err
    assert "If the script won't work for a game" in err
    assert not (out / f"{GAME_ID}.json").exists()


# --- live mode, continued --------------------------------------------------------------------------


def _typing(monkeypatch, *lines: str):
    typed = iter(lines)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(typed))


def test_live_mode_shows_what_came_due_while_it_was_off(tmp_path, backup_file, capsys, monkeypatch):
    at = datetime.now().astimezone().replace(second=0, microsecond=0) - timedelta(minutes=20)
    changes = tmp_path / "game1.txt"
    changes.write_text(f"backup {at:%H:%M}\n{at:%H:%M} Alpha veto\n")
    _typing(monkeypatch, "quit")
    assert admin.live(backup_file, changes, out_dir=tmp_path / "save", map_path=tmp_path / "m.png") == 0
    out = capsys.readouterr().out
    assert "Came due since the last move:" in out
    assert "(timer) Alpha's veto period ended" in out


def test_live_mode_explains_how_to_add_a_late_move(tmp_path, backup_file, capsys, monkeypatch):
    now = datetime.now().astimezone().replace(second=0, microsecond=0)
    changes = tmp_path / "game1.txt"
    changes.write_text(f"backup {now - timedelta(minutes=10):%H:%M}\n")
    _typing(monkeypatch, "Beta request Liverpool Street", f"{now - timedelta(minutes=5):%H:%M} Alpha veto", "quit")
    admin.live(backup_file, changes, out_dir=tmp_path / "save", map_path=tmp_path / "m.png")
    assert "To add a late move, type 'quit', put it in the right place" in capsys.readouterr().out
    assert "Alpha veto" not in changes.read_text()


def test_live_mode_needs_the_changes_file(tmp_path, backup_file, capsys):
    assert admin.live(backup_file, tmp_path / "missing.txt", out_dir=tmp_path / "save") == 1
    assert "There's no changes file" in capsys.readouterr().out


def test_live_mode_from_the_command_line(tmp_path, backup_file, monkeypatch):
    changes = tmp_path / "game1.txt"
    changes.write_text(f"backup {datetime.now().astimezone():%H:%M}\n")
    _typing(monkeypatch, "quit")
    args = [str(backup_file), str(changes), "--live", "--out", str(tmp_path / "save"), "--map", str(tmp_path / "m.png")]
    assert admin.main(args) == 0
    with pytest.raises(SystemExit):
        admin.main([str(backup_file), "--live"])


# --- times, to the minute ---------------------------------------------------------------------


def _veto_minutes() -> int:
    """How long the engine's veto period is, however that's been set."""
    game = _new()
    a, _ = _ids(game)
    game.complete_challenge(a, "Jubilee")
    game.request_challenge(a, "Bond Street")
    _at(game, 0)
    game.veto_challenges(a)
    (ends,) = [e.__time__ for e in game.scheduled_events if e.__type__ == "unveto"]
    return ends // MIN


def test_a_move_in_the_minute_a_veto_ends_comes_after_it(tmp_path, backup_file):
    period = _veto_minutes()
    at = datetime.now().astimezone().replace(second=0, microsecond=0) - timedelta(minutes=period + 1)
    later = at + timedelta(minutes=period)
    code, game = _run(
        tmp_path,
        backup_file,
        f"backup {at:%H:%M}",
        f"{at:%H:%M} Alpha veto",
        f"{later:%H:%M} Alpha complete Central harder",
    )
    assert code == 0  # refused, or a save that won't reload, if the completion tied with the veto's end
    assert game.get_snake(game.teams[0]).anchor == "Bond Street"
