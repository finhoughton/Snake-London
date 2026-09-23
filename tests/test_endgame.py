import asyncio
from pathlib import Path

import pytest
from conftest import minutes_away, scheduled

from config import DECLARE_WIN_COOLDOWN_MINUTES, DECLARE_WIN_COST, DECLARE_WIN_WINDOW_MINUTES, WINNING_THRESHOLD
from game import GameState
from jloxgame.state import Status, Team
from new_game import new_game


def _claim(game: GameState, team: Team, stations: list[str]):
    for station in stations:
        game.map.claim(station, team)


def _others(game: GameState, exclude: str, count: int):
    return [s for s in game.map.station_keys() if s != exclude][:count]


# --- concede ---------------------------------------------------------------


def test_concede_eliminates_and_blocks_actions():
    game = new_game({"A": "Wembley Park", "B": "Stratford"}, bonus_interchanges=set())
    A, _B = game.teams
    game.concede(A)
    snake = game.get_snake(A)
    assert snake.conceded and snake.eliminated
    assert A not in game.active_teams()
    with pytest.raises(ValueError, match="conceded"):
        game.initial_request_challenge(A)


def test_conceding_when_already_out_raises():
    game = new_game({"A": "Wembley Park"}, bonus_interchanges=set())
    A = game.teams[0]
    game.concede(A)
    with pytest.raises(ValueError, match="already out"):
        game.concede(A)


def test_last_team_wins_when_the_other_concedes():
    game = new_game({"A": "Wembley Park", "B": "Stratford"}, bonus_interchanges=set())
    A, B = game.teams
    game.concede(B)
    assert game.winner() == A


# --- "most claimed stations" tiebreaker ------------------------------------


def test_tiebreak_winner_is_the_largest_body():
    game = new_game({"A": "Wembley Park", "B": "Stratford"}, bonus_interchanges=set())
    A, B = game.teams
    _claim(game, A, _others(game, "Stratford", 3))
    _claim(game, B, ["Stratford"])
    assert game.tiebreak_winner() == A  # 3 > 1


def test_tiebreak_winner_none_on_exact_tie():
    game = new_game({"A": "Wembley Park", "B": "Stratford"}, bonus_interchanges=set())
    A, B = game.teams
    _claim(game, A, ["Wembley Park"])
    _claim(game, B, ["Stratford"])
    assert game.tiebreak_winner() is None  # 1 == 1


def test_tiebreak_excludes_eliminated_teams():
    game = new_game({"A": "Wembley Park", "B": "Stratford"}, bonus_interchanges=set())
    A, B = game.teams
    _claim(game, B, _others(game, "Wembley Park", 3))  # B has more claimed...
    _claim(game, A, ["Wembley Park"])
    game.concede(B)  # ...but is out, so A wins the tiebreak
    assert game.tiebreak_winner() == A


# --- the time limit ---------------------------------------------------------


def test_the_time_limit_ends_the_game_and_records_the_tiebreak_winner():
    game = new_game({"A": "Wembley Park", "B": "Stratford"}, bonus_interchanges=set())
    A, B = game.teams
    _claim(game, A, _others(game, "Stratford", 3))
    _claim(game, B, ["Stratford"])

    game.time_limit()

    assert game.status == Status.END
    assert game.declared_winner == A
    assert game.winner() == A  # the result is in state, not just printed


def test_the_time_limit_leaves_an_exact_tie_unwon():
    game = new_game({"A": "Wembley Park", "B": "Stratford"}, bonus_interchanges=set())
    A, B = game.teams
    _claim(game, A, ["Wembley Park"])
    _claim(game, B, ["Stratford"])

    game.time_limit()

    assert game.status == Status.END
    assert game.winner() is None  # no secondary tiebreak


def test_the_time_limit_does_nothing_once_the_game_is_over():
    game = new_game({"A": "Wembley Park", "B": "Stratford"}, bonus_interchanges=set())
    A, _B = game.teams
    _claim(game, A, _others(game, "Stratford", 3))
    game.status = Status.END

    game.time_limit()

    assert game.declared_winner is None  # it ended some other way; don't overwrite the result


# --- acting after the game has ended -----------------------------------------

# Not unveto: it is a timer, so it must never raise (test_crash.py checks it once the game is over).
_AFTER_THE_END = {
    "request_challenge": lambda g, a, b: g.request_challenge(a.role_id, "Bond Street"),
    "complete_challenge": lambda g, a, b: g.complete_challenge(a.role_id, "Jubilee"),
    "veto_challenges": lambda g, a, b: g.veto_challenges(a.role_id),
    "buy_powerup": lambda g, a, b: g.buy_powerup(a.role_id, "jump"),
    "play_normal_powerup": lambda g, a, b: g.play_normal_powerup(a.role_id, "efficiency"),
    "play_jump": lambda g, a, b: g.play_jump(a.role_id, station="Bond Street"),
    "play_detour": lambda g, a, b: g.play_detour(a.role_id, line="Bakerloo"),
    "play_curse": lambda g, a, b: g.play_curse(a.role_id, target_team_id=b.role_id, curse_id="pub"),
}


@pytest.mark.parametrize("name", sorted(_AFTER_THE_END))
def test_no_event_acts_once_the_game_has_ended(name: str):
    # Every one of these used to `raise` bare, which is a RuntimeError, not the
    # ValueError the rest of the engine (and the bot) is written against.
    game = new_game({"A": "Baker Street", "B": "Stratford"}, bonus_interchanges=set())
    A, B = game.teams
    game.complete_challenge(A.role_id, "Jubilee")
    game.status = Status.END

    with pytest.raises(ValueError, match="The game is not running"):
        _AFTER_THE_END[name](game, A, B)


# --- win-lead condition: your Body vs opponents' Body + Neck ----------------


def test_win_lead_counts_your_body_only_not_your_neck():
    game = new_game({"A": "Baker Street", "B": "Stratford"}, bonus_interchanges=set())
    A, B = game.teams
    _claim(game, B, ["Stratford"])  # opponent total_controlled = 1

    # A's body equals opponent-total + threshold — a tie on the boundary, NOT a
    # strict lead by more than the threshold.
    not_enough = 1 + WINNING_THRESHOLD
    _claim(game, A, _others(game, "Stratford", not_enough))
    assert not game.has_winning_lead(A)

    # Give A a (large) active neck: the neck must NOT count toward A's own total.
    snake = game.get_snake(A)
    snake.travel_line = "Jubilee"
    snake.anchor = "Wembley Park"
    snake.front = "Green Park"
    snake.neck_active = True
    assert len(game.neck(A)) > 0
    assert game.total_controlled(A) > not_enough  # body + neck would clear the bar
    assert not game.has_winning_lead(A)  # ...but body alone does not

    # One more claimed station tips A's *body* over the threshold.
    extra = next(s for s in game.map.station_keys() if not game.map.is_claimed(s))
    game.map.claim(extra, A)
    assert game.has_winning_lead(A)


# --- declaring a win -------------------------------------------------------


def _game_with_lead(margin: int):
    """A's Body is `margin` stations past the bare threshold over B's one station.

    margin=1 is a winning lead; margin=0 sits exactly on the bar, which isn't enough.
    """
    game = new_game({"A": "Baker Street", "B": "Stratford"}, bonus_interchanges=set())
    A, B = game.teams
    _claim(game, B, ["Stratford"])
    _claim(game, A, _others(game, "Stratford", 1 + WINNING_THRESHOLD + margin))
    return game, A, B


def test_a_lead_alone_never_wins():
    game, A, _B = _game_with_lead(margin=1)
    assert game.has_winning_lead(A)
    assert game.winner() is None
    assert game.status == Status.RUNNING


def test_declaring_costs_coins_and_schedules_the_check():
    game, A, _B = _game_with_lead(margin=1)
    coins = game.get_snake(A).coins
    game.declare_win(A.role_id)
    assert game.get_snake(A).coins == coins - DECLARE_WIN_COST
    assert game.get_snake(A).win_declared
    [check] = scheduled(game, "resolve_win_declaration")
    assert list(check.args) == [A.role_id]
    assert minutes_away(game, check) == pytest.approx(DECLARE_WIN_WINDOW_MINUTES, abs=0.1)


def test_a_declaration_that_still_leads_wins_and_ends_the_game():
    game, A, _B = _game_with_lead(margin=1)
    game.declare_win(A.role_id)
    assert game.resolve_win_declaration(A.role_id)[0] is True
    assert game.winner() == A
    assert game.status == Status.END


def test_a_rival_can_deny_a_declaration_by_growing_a_neck():
    # The check compares A's Body with B's Body + Neck, so B pushing out a neck while
    # the declaration is pending can take away a narrow lead.
    game, A, B = _game_with_lead(margin=1)
    game.declare_win(A.role_id)
    nxt = game.map.get_station("Stratford").neighbours("Jubilee")[0]
    assert not game.map.is_claimed(nxt)
    b = game.get_snake(B)
    b.travel_line, b.front, b.neck_active = "Jubilee", nxt, True  # anchor is still Stratford
    assert game.resolve_win_declaration(A.role_id)[0] is False
    assert game.winner() is None


def test_a_failed_declaration_blocks_declaring_again_until_the_cooldown_ends():
    game, A, _B = _game_with_lead(margin=0)  # level with the bar, not over it
    game.get_snake(A).coins = 2 * DECLARE_WIN_COST  # a failed declaration still charges, so fund both
    game.declare_win(A.role_id)
    assert game.resolve_win_declaration(A.role_id)[0] is False
    snake = game.get_snake(A)
    assert snake.declare_cooldown and not snake.win_declared
    [cooldown] = scheduled(game, "end_declare_cooldown")
    assert minutes_away(game, cooldown) == pytest.approx(DECLARE_WIN_COOLDOWN_MINUTES, abs=0.1)
    with pytest.raises(ValueError, match="cooldown"):
        game.declare_win(A.role_id)
    game.end_declare_cooldown(A.role_id)
    game.declare_win(A.role_id)  # allowed again


def test_declaring_needs_the_coins_and_only_one_at_a_time():
    game, A, _B = _game_with_lead(margin=1)
    game.get_snake(A).coins = DECLARE_WIN_COST - 1
    with pytest.raises(ValueError, match="coins"):
        game.declare_win(A.role_id)
    game.get_snake(A).coins = 2 * DECLARE_WIN_COST
    game.declare_win(A.role_id)
    with pytest.raises(ValueError, match="already declared"):
        game.declare_win(A.role_id)


def test_a_declarer_who_crashes_during_the_window_does_not_win():
    game, A, B = _game_with_lead(margin=1)
    game.declare_win(A.role_id)
    game.crash(A)
    assert game.resolve_win_declaration(A.role_id)[0] is False  # runs from the scheduler, so never raises
    assert game.declared_winner is None
    assert game.winner() == B  # last team standing instead


def test_the_schedulers_tick_runs_the_check_when_the_window_ends():
    # In a real game nothing calls resolve_win_declaration directly: the bot's once-a-
    # second tick fires it when its time comes. Skip ahead to that moment.
    game, A, _B = _game_with_lead(margin=1)
    game.declare_win(A.role_id)
    [check] = scheduled(game, "resolve_win_declaration")
    check.__time__ = game.game_time_now() - 1
    asyncio.run(game.schedule_tick())
    assert not scheduled(game, "resolve_win_declaration")
    assert game.winner() == A
    assert game.status == Status.END


def test_a_pending_declaration_survives_a_restart_without_doubling_the_check(tmp_path: Path):
    game, A, _B = _game_with_lead(margin=1)
    game.declare_win(A.role_id)
    asyncio.run(game.schedule_tick())  # the bot's once-a-second tick, bringing last_update up to date
    asyncio.run(game.save(tmp_path))

    reloaded = GameState.load(tmp_path, game.thread_id)  # what the bot does on restart
    snake = reloaded.get_snake(reloaded.get_team(A.role_id))
    assert snake.win_declared
    assert snake.coins == game.get_snake(A).coins  # charged once, not twice
    assert len(scheduled(reloaded, "resolve_win_declaration")) == 1
