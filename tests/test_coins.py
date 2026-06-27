from __future__ import annotations

import random

from game import (
    BONUS_AT_FRONT,
    BONUS_CLAIMED,
    EASIER_REWARD,
    HARDER_REWARD,
    STARTING_COINS,
    new_game,
)
from map import Map


def test_teams_start_with_starting_coins():
    game = new_game({"A": "Wembley Park", "B": "Stratford"}, bonus_interchanges=set())
    assert game.get_snake("A").coins == STARTING_COINS
    assert game.get_snake("B").coins == STARTING_COINS


def test_easier_challenge_awards_easier_reward():
    game = new_game({"A": "Wembley Park"}, bonus_interchanges=set())
    game.initial_request_challenge("A")
    game.complete_challenge("A", "Jubilee")  # easier (default)
    assert game.get_snake("A").coins == STARTING_COINS + EASIER_REWARD


def test_harder_challenge_awards_harder_reward():
    game = new_game({"A": "Wembley Park"}, bonus_interchanges=set())
    game.initial_request_challenge("A")
    game.complete_challenge("A", "Jubilee", hard=True)
    assert game.get_snake("A").coins == STARTING_COINS + HARDER_REWARD


def test_completing_at_a_bonus_interchange_pays_front_bonus():
    # The Front of a non-initial challenge is a bonus interchange -> front bonus.
    game = new_game({"A": "Wembley Park"}, bonus_interchanges={"Bond Street"})
    game.initial_request_challenge("A")
    game.complete_challenge("A", "Jubilee")  # claims the origin (never a bonus): +EASIER_REWARD
    game.request_challenge("A", "Bond Street")
    game.complete_challenge("A", "Jubilee")  # Front = Bond Street (bonus): +EASIER_REWARD +BONUS_AT_FRONT
    expected = STARTING_COINS + EASIER_REWARD + (EASIER_REWARD + BONUS_AT_FRONT)
    assert game.get_snake("A").coins == expected


def test_origins_are_never_bonus():
    # Excluded even at 100% chance...
    game = new_game({"A": "Wembley Park", "B": "Stratford"}, bonus_chance=1.0)
    assert "Wembley Park" not in game.bonus_interchanges
    assert "Stratford" not in game.bonus_interchanges
    # ...and stripped from an explicit set too.
    game2 = new_game({"A": "Wembley Park"}, bonus_interchanges={"Wembley Park", "Bond Street"})
    assert game2.bonus_interchanges == frozenset({"Bond Street"})


def test_claiming_a_bonus_interchange_from_elsewhere_pays_claim_bonus():
    # A bonus interchange that lands in the neck (not the Front) pays the smaller bonus.
    path = Map("map/connections.json")._path_between_on_line("Jubilee", "Wembley Park", "Bond Street")
    intermediate = path[1]  # in the neck, but not the Front (Bond Street)
    assert intermediate != "Bond Street"

    game = new_game({"A": "Wembley Park"}, bonus_interchanges={intermediate})
    game.initial_request_challenge("A")
    game.complete_challenge("A", "Jubilee")  # claims Wembley Park (not a bonus): +EASIER_REWARD
    game.request_challenge("A", "Bond Street")
    game.complete_challenge("A", "Jubilee")  # claims the neck incl. the bonus intermediate

    expected = STARTING_COINS + EASIER_REWARD + (EASIER_REWARD + BONUS_CLAIMED)
    assert game.get_snake("A").coins == expected


def test_bonus_chance_zero_selects_none():
    game = new_game({"A": "Wembley Park"}, bonus_chance=0.0)
    assert game.bonus_interchanges == frozenset()


def test_bonus_chance_one_selects_all_non_origins():
    game = new_game({"A": "Wembley Park"}, bonus_chance=1.0)
    assert game.bonus_interchanges == frozenset(game.map.station_keys()) - {"Wembley Park"}


def test_bonus_selection_is_reproducible_with_a_seeded_rng():
    a = new_game({"A": "Wembley Park"}, rng=random.Random(42)).bonus_interchanges
    b = new_game({"A": "Wembley Park"}, rng=random.Random(42)).bonus_interchanges
    assert a == b
