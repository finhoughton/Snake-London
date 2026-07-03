from __future__ import annotations

import pytest

from game import new_game

# Baker Street — Bond Street — Green Park — Westminster are consecutive on the
# Jubilee line, so two teams starting at Baker Street and Green Park can both aim
# their necks at the shared Bond Street.


def _two_team_game():
    game = new_game({"A": "Baker Street", "B": "Green Park"}, bonus_interchanges=set())
    game.initial_request_challenge("A")
    game.complete_challenge("A", "Jubilee")
    game.initial_request_challenge("B")
    game.complete_challenge("B", "Jubilee")
    return game


def test_claiming_into_another_teams_neck_crashes_them():
    game = _two_team_game()
    game.request_challenge("A", "Bond Street")  # A's neck = [Bond Street]
    game.request_challenge("B", "Bond Street")  # B heads to the same interchange
    assert not game.get_snake("A").crashed

    game.complete_challenge("B", "Jubilee")  # B claims Bond Street first
    assert game.get_snake("A").crashed, "A's neck became claimed and should crash"
    assert not game.get_snake("B").crashed, "the claiming team never crashes itself"


def test_completing_does_not_crash_bystanders():
    game = _two_team_game()
    game.request_challenge("A", "Bond Street")  # A's neck = [Bond Street]
    game.request_challenge("B", "Westminster")  # away from A's neck
    game.complete_challenge("B", "Jubilee")  # claims Westminster only
    assert not game.get_snake("A").crashed
    assert not game.get_snake("B").crashed


def test_crashed_team_cannot_complete():
    game = _two_team_game()
    game.request_challenge("A", "Bond Street")
    game.request_challenge("B", "Bond Street")
    game.complete_challenge("B", "Jubilee")  # crashes A
    assert game.get_snake("A").crashed

    with pytest.raises(ValueError, match="crashed"):
        game.complete_challenge("A", "Jubilee")


def test_crashed_team_cannot_request_or_start():
    game = _two_team_game()
    game.crash("B")  # B has no active neck, just knocked out
    with pytest.raises(ValueError, match="crashed"):
        game.request_challenge("B", "Bond Street")

    fresh = new_game({"C": "Baker Street"}, bonus_interchanges=set())
    fresh.crash("C")
    with pytest.raises(ValueError, match="crashed"):
        fresh.initial_request_challenge("C")


def test_last_snake_standing_wins_after_crash():
    game = _two_team_game()
    game.request_challenge("A", "Bond Street")
    game.request_challenge("B", "Bond Street")
    game.complete_challenge("B", "Jubilee")  # crashes A -> B is the only survivor
    assert game.winner() == "B"


def test_first_to_claim_survives_regardless_of_order():
    # Symmetric to the first test: whoever completes first keeps the interchange.
    game = _two_team_game()
    game.request_challenge("A", "Bond Street")
    game.request_challenge("B", "Bond Street")
    game.complete_challenge("A", "Jubilee")  # A completes first this time
    assert not game.get_snake("A").crashed
    assert game.get_snake("B").crashed


def test_requesting_through_opponent_claim_crashes_the_requester():
    # B owns Bond Street; A's path Baker -> Bond -> Green Park runs through it.
    game = new_game({"A": "Baker Street", "B": "Bond Street"}, bonus_interchanges=set())
    game.initial_request_challenge("A")
    game.complete_challenge("A", "Jubilee")
    game.initial_request_challenge("B")
    game.complete_challenge("B", "Jubilee")  # B claims Bond Street

    game.request_challenge("A", "Green Park")  # legal move, but the neck is claimed
    assert game.get_snake("A").crashed
    assert game.get_snake("A").front == "Green Park"  # the requested Front is recorded
    assert not game.get_snake("B").crashed


def test_requesting_through_own_claim_crashes_the_requester():
    game = new_game({"A": "Baker Street"}, bonus_interchanges=set())
    game.initial_request_challenge("A")
    game.complete_challenge("A", "Jubilee")
    game.request_challenge("A", "Green Park")
    game.complete_challenge("A", "Jubilee")  # A body: Baker -> Bond -> Green Park

    # Backtracking to Baker Street runs the neck back through A's own Bond Street.
    game.request_challenge("A", "Baker Street")
    assert game.get_snake("A").crashed
    assert game.get_snake("A").front == "Baker Street"
