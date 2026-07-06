from __future__ import annotations

import pytest

from config import WINNING_THRESHOLD
from game import new_game


def _claim(game, team, stations):
    for station in stations:
        game.map.claim(station, team)


def _others(game, exclude, count):
    return [s for s in game.map.station_keys() if s != exclude][:count]


# --- concede ---------------------------------------------------------------


def test_concede_eliminates_and_blocks_actions():
    game = new_game({"A": "Wembley Park", "B": "Stratford"}, bonus_interchanges=set())
    game.concede("A")
    snake = game.get_snake("A")
    assert snake.conceded and snake.eliminated
    assert "A" not in game.active_teams()
    with pytest.raises(ValueError, match="conceded"):
        game.initial_request_challenge("A")


def test_conceding_when_already_out_raises():
    game = new_game({"A": "Wembley Park"}, bonus_interchanges=set())
    game.concede("A")
    with pytest.raises(ValueError, match="already out"):
        game.concede("A")


def test_last_team_wins_when_the_other_concedes():
    game = new_game({"A": "Wembley Park", "B": "Stratford"}, bonus_interchanges=set())
    game.concede("B")
    assert game.winner() == "A"


# --- "most claimed stations" tiebreaker ------------------------------------


def test_tiebreak_winner_is_the_largest_body():
    game = new_game({"A": "Wembley Park", "B": "Stratford"}, bonus_interchanges=set())
    _claim(game, "A", _others(game, "Stratford", 3))
    _claim(game, "B", ["Stratford"])
    assert game.tiebreak_winner() == "A"  # 3 > 1


def test_tiebreak_winner_none_on_exact_tie():
    game = new_game({"A": "Wembley Park", "B": "Stratford"}, bonus_interchanges=set())
    _claim(game, "A", ["Wembley Park"])
    _claim(game, "B", ["Stratford"])
    assert game.tiebreak_winner() is None  # 1 == 1


def test_tiebreak_excludes_eliminated_teams():
    game = new_game({"A": "Wembley Park", "B": "Stratford"}, bonus_interchanges=set())
    _claim(game, "B", _others(game, "Wembley Park", 3))  # B has more claimed...
    _claim(game, "A", ["Wembley Park"])
    game.concede("B")  # ...but is out, so A wins the tiebreak
    assert game.tiebreak_winner() == "A"


# --- win-lead condition: your Body vs opponents' Body + Neck ----------------


def test_win_lead_counts_your_body_only_not_your_neck():
    game = new_game({"A": "Baker Street", "B": "Stratford"}, bonus_interchanges=set())
    _claim(game, "B", ["Stratford"])  # opponent total_controlled = 1

    # A's body equals opponent-total + threshold — a tie on the boundary, NOT a
    # strict lead by more than the threshold, so no win yet.
    not_enough = 1 + WINNING_THRESHOLD
    _claim(game, "A", _others(game, "Stratford", not_enough))
    assert game.winner() is None

    # Give A a (large) active neck: under the old body+neck rule this would win,
    # but the neck must NOT count toward A's own total — still no win.
    snake = game.get_snake("A")
    snake.declared_line = "Jubilee"
    snake.anchor = "Wembley Park"
    snake.front = "Green Park"
    snake.neck_active = True
    assert len(game.neck("A")) > 0
    assert game.total_controlled("A") > not_enough  # body + neck would clear the bar
    assert game.winner() is None  # ...but body alone does not

    # One more claimed station tips A's *body* over the threshold -> win.
    extra = next(s for s in game.map.station_keys() if not game.map.is_claimed(s))
    game.map.claim(extra, "A")
    assert game.winner() == "A"
