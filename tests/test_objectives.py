from __future__ import annotations

import asyncio
import statistics
from pathlib import Path

import pytest

from config import (
    EASIER_REWARD,
    OBJECTIVE_COINS,
    OBJECTIVE_INTERVAL_MINUTES,
    OBJECTIVE_PASS_COINS,
    OBJECTIVE_STATIONS,
    WINNING_THRESHOLD,
)
from game import GameState
from jloxgame.state import Status
from new_game import new_game
from objectives import centrality, choose_objective, objective_score, team_costs

# Map facts used below (verified against map/connections.json):
#   - Jubilee runs Baker Street — Bond Street — Green Park (consecutive).
#   - Rayners Lane's only neighbours are Park Royal (Picc) and Kenton (Met).


def _game(teams: dict[str, str] | None = None) -> GameState:
    return new_game(teams or {"A": "Wembley Park", "B": "Stratford"}, bonus_interchanges=set())


def _scheduled(game: GameState, event_type: str) -> list:
    return [e for e in game.scheduled_events if e.__type__ == event_type]


def _minutes_away(game: GameState, scheduled) -> float:
    return (scheduled.__time__ - game.game_time_now()) / 60_000


# --- choosing where it goes ---------------------------------------------------


def test_the_objective_is_open_ground_nobody_is_standing_on():
    game = _game()
    station = choose_objective(game)
    assert station is not None
    assert not game.map.is_claimed(station)
    assert station not in {game.get_snake(t).anchor for t in game.teams}


def test_every_team_needs_the_same_number_of_legs():
    game = _game()
    station = choose_objective(game)
    routes = [costs[station] for costs in team_costs(game).values()]
    assert len({(legs, jumps) for _, legs, jumps, _ in routes}) == 1


def test_it_prefers_central_stations():
    game = _game()
    central = centrality(game.map)
    assert central[choose_objective(game)] > statistics.median(central.values())


def test_fairness_outweighs_centrality():
    fair_but_outer = objective_score([(2.0, 2, 0, 5), (2.0, 2, 0, 6)], central=0.3)
    unfair_but_central = objective_score([(2.0, 2, 0, 5), (4.0, 4, 0, 9)], central=1.0)
    assert fair_but_outer < unfair_but_central


def test_closer_stop_counts_are_fairer():
    even = objective_score([(2.0, 2, 0, 5), (2.0, 2, 0, 5)], central=0.7)
    uneven = objective_score([(2.0, 2, 0, 3), (2.0, 2, 0, 7)], central=0.7)
    assert even < uneven


def test_legs_still_outweigh_stops():
    a_leg_behind = objective_score([(2.0, 2, 0, 5), (3.0, 3, 0, 5)], central=0.7)
    three_stops_behind = objective_score([(2.0, 2, 0, 3), (2.0, 2, 0, 6)], central=0.7)
    assert three_stops_behind < a_leg_behind


def test_a_clear_route_beats_one_that_needs_a_jump():
    clear = objective_score([(2.0, 2, 0, 4), (2.0, 2, 0, 4)], central=0.7)
    needs_jumps = objective_score([(3.0, 1, 1, 2), (3.0, 1, 1, 2)], central=0.95)
    assert clear < needs_jumps


def test_a_walled_in_team_isnt_compensated_at_the_others_expense():
    # The end of example.py's game, where Delta needs a Jump to reach anything.
    kings_cross = objective_score([(2.0, 2, 0, 3), (1.0, 1, 0, 3), (3.0, 3, 0, 7), (3.0, 1, 1, 4)], central=0.95)
    caledonian_road = objective_score([(2.0, 2, 0, 3), (2.0, 2, 0, 4), (2.0, 2, 0, 5), (4.0, 2, 1, 4)], central=0.68)
    assert caledonian_road < kings_cross


def test_an_objective_is_still_placed_when_a_team_is_walled_in():
    game = _game({"A": "Rayners Lane", "B": "Stratford"})
    A, B = game.teams
    rayners = game.map.get_station("Rayners Lane")
    for line in rayners.line_keys():
        for neighbour in rayners.neighbours(line):
            game.map.claim(neighbour, B)
    station = choose_objective(game)
    assert station is not None
    assert team_costs(game)[A][station][2] >= 1  # A needs a Jump to get there


def test_a_jumped_claim_is_never_chosen():
    game = _game()
    _A, B = game.teams
    first = choose_objective(game)
    game.map.claim(first, B)
    game.jumped_stations.add(first)
    assert choose_objective(game) != first


def _neighbours(game: GameState, station: str) -> set[str]:
    here = game.map.get_station(station)
    return {n for line in here.line_keys() for n in here.neighbours(line)}


def test_an_objective_is_never_next_to_a_live_one():
    game = _game()
    first = choose_objective(game)
    game.objectives.append(first)
    second = choose_objective(game)
    assert second != first
    assert second not in _neighbours(game, first)


def test_piled_up_objectives_never_touch():
    game = _game()
    for _ in range(6):
        game.new_objective()
    live = set(game.objectives)
    assert len(live) == 6
    for station in live:
        assert not (_neighbours(game, station) & live)


def test_no_objective_once_only_one_team_is_left():
    game = _game()
    game.concede(game.teams[1])
    assert choose_objective(game) is None


# --- when they appear ---------------------------------------------------------


def test_the_first_objective_comes_one_interval_after_the_start():
    game = _game()
    [first] = _scheduled(game, "new_objective")
    assert _minutes_away(game, first) == pytest.approx(OBJECTIVE_INTERVAL_MINUTES, abs=0.1)


def test_a_new_objective_goes_live_and_the_next_is_scheduled():
    game = _game()
    station = game.new_objective()
    assert station is not None
    assert game.objectives == [station]
    scheduled = _scheduled(game, "new_objective")
    assert len(scheduled) == 2  # the one from the start, plus the next one after this
    latest = max(scheduled, key=lambda e: e.__time__)
    assert _minutes_away(game, latest) == pytest.approx(OBJECTIVE_INTERVAL_MINUTES, abs=0.1)


def test_unclaimed_objectives_stay_live_and_pile_up():
    game = _game()
    first, second = game.new_objective(), game.new_objective()
    assert first != second
    assert game.objectives == [first, second]


def test_no_new_objectives_once_the_game_is_over():
    game = _game()
    game.status = Status.END
    before = len(game.scheduled_events)
    assert game.new_objective() is None
    assert game.objectives == []
    assert len(game.scheduled_events) == before


# --- winning them -------------------------------------------------------------


def _one_stop_from_the_objective():
    """A at Baker Street with its Jubilee line declared, and Bond Street a live objective."""
    game = _game({"A": "Baker Street", "B": "Stratford"})
    A, _B = game.teams
    game.complete_challenge(A.role_id, "Jubilee")
    game.objectives.append("Bond Street")
    return game, A


def test_completing_a_challenge_at_the_objective_wins_it():
    game, A = _one_stop_from_the_objective()
    snake = game.get_snake(A)
    coins, score = snake.coins, game.score(A)
    game.request_challenge(A.role_id, "Bond Street")
    game.complete_challenge(A.role_id, "Jubilee")
    assert snake.coins == coins + EASIER_REWARD + OBJECTIVE_COINS
    assert snake.objectives_won == 1
    assert game.score(A) == score + 1 + OBJECTIVE_STATIONS  # Bond Street itself, plus the prize
    assert game.objectives == []


def test_claiming_the_objective_in_passing_pays_less_and_ends_it():
    game, A = _one_stop_from_the_objective()
    snake = game.get_snake(A)
    coins, score = snake.coins, game.score(A)
    game.request_challenge(A.role_id, "Green Park")  # the neck runs through Bond Street
    game.complete_challenge(A.role_id, "Jubilee")
    assert snake.coins == coins + EASIER_REWARD + OBJECTIVE_PASS_COINS
    assert snake.objectives_won == 0
    assert game.score(A) == score + 2  # just the two stations claimed
    assert game.objectives == []


def test_a_won_objective_cannot_be_taken_again_with_a_jump():
    # A wins Bond Street, so the objective is spent and the station is A's. B jumps it and
    # completes a challenge there: the station stays A's, and there is no prize left to win.
    game = _game({"A": "Baker Street", "B": "Green Park"})
    A, B = game.teams
    game.complete_challenge(A.role_id, "Jubilee")
    game.complete_challenge(B.role_id, "Jubilee")
    game.objectives.append("Bond Street")

    game.request_challenge(A.role_id, "Bond Street")
    game.complete_challenge(A.role_id, "Jubilee")  # A wins it
    assert game.objectives == []

    b = game.get_snake(B)
    b.coins = 50
    game.buy_powerup(B.role_id, "jump")
    game.play_jump(B.role_id, station="Bond Street")  # passable, but still A's
    coins, score = b.coins, game.score(B)

    game.request_challenge(B.role_id, "Bond Street")
    game.complete_challenge(B.role_id, "Jubilee")

    assert b.coins == coins + EASIER_REWARD  # the challenge reward only, no objective prize
    assert b.objectives_won == 0
    assert game.score(B) == score  # Bond Street stays in A's Body
    assert game.map.get_claim("Bond Street") == A


def test_an_objective_on_someone_elses_claim_pays_nothing():
    # Placement never does this (choose_objective skips claims), but the payout must follow the
    # claim: fronting at a jumped station another team owns wins no prize and spends no objective.
    game = _game({"A": "Baker Street", "B": "Green Park"})
    A, B = game.teams
    game.complete_challenge(A.role_id, "Jubilee")
    game.complete_challenge(B.role_id, "Jubilee")
    game.map.claim("Bond Street", A)
    game.jumped_stations.add("Bond Street")
    game.objectives.append("Bond Street")  # live, but A owns it

    b = game.get_snake(B)
    coins, score = b.coins, game.score(B)
    game.request_challenge(B.role_id, "Bond Street")
    game.complete_challenge(B.role_id, "Jubilee")

    assert b.coins == coins + EASIER_REWARD  # the challenge reward only
    assert b.objectives_won == 0
    assert game.score(B) == score  # Bond Street stays in A's Body
    assert game.objectives == ["Bond Street"]  # nothing was claimed, so nothing was spent


def test_objectives_count_toward_the_lead():
    game = _game()
    A, B = game.teams
    game.map.claim("Stratford", B)  # B's score: 1
    # A's body is short of the winning lead on its own, but not with an objective.
    for station in [s for s in game.map.station_keys() if s != "Stratford"][
        : 2 + WINNING_THRESHOLD - OBJECTIVE_STATIONS
    ]:
        game.map.claim(station, A)
    assert not game.has_winning_lead(A)
    game.get_snake(A).objectives_won = 1
    assert game.has_winning_lead(A)


def test_objectives_count_in_the_tiebreak():
    game = _game()
    A, B = game.teams
    game.map.claim("Wembley Park", A)
    game.map.claim("Stratford", B)
    game.map.claim("Bond Street", B)  # B has more stations...
    game.get_snake(A).objectives_won = 1  # ...but A's objective is worth OBJECTIVE_STATIONS
    assert game.tiebreak_winner() == A


# --- restarts -----------------------------------------------------------------


def test_live_objectives_and_the_next_timer_survive_a_restart(tmp_path: Path):
    game = _game()
    station = game.new_objective()
    asyncio.run(game.schedule_tick())  # the bot's once-a-second tick, bringing last_update up to date
    asyncio.run(game.save(tmp_path))

    reloaded = GameState.load(tmp_path, game.thread_id)  # what the bot does on restart
    assert reloaded.objectives == [station]
    assert len(_scheduled(reloaded, "new_objective")) == len(_scheduled(game, "new_objective"))
