from __future__ import annotations

import json
import random

import pytest

from challenges import Challenge, ChallengePool
from config import HARDER_REWARD, STARTING_COINS
from game import new_game


def _pool(tmp_path) -> ChallengePool:
    data = {
        "challenges": [
            {"id": "easy", "name": "Easy", "description": "e", "difficulty": 1.0},
            {"id": "mid", "name": "Mid", "description": "m", "difficulty": 3.0},
            {"id": "hard", "name": "Hard", "description": "h", "difficulty": 6.0},
        ]
    }
    path = tmp_path / "challenges.json"
    path.write_text(json.dumps(data))
    return ChallengePool(str(path))


def _game(tmp_path):
    return new_game(
        {"A": "Wembley Park"},
        bonus_interchanges=set(),
        challenge_pool=_pool(tmp_path),
        rng=random.Random(0),
    )


def test_requesting_a_challenge_offers_a_pair(tmp_path):
    game = _game(tmp_path)
    game.initial_request_challenge("A")
    game.complete_challenge("A", "Jubilee")
    game.request_challenge("A", "Bond Street")

    offer = game.current_challenges("A")
    assert offer is not None
    easier, harder = offer
    assert isinstance(easier, Challenge) and isinstance(harder, Challenge)
    assert easier.difficulty <= harder.difficulty


def test_initial_challenge_also_offers_a_pair(tmp_path):
    game = _game(tmp_path)
    game.initial_request_challenge("A")
    assert game.current_challenges("A") is not None


def test_completing_clears_the_offer_and_awards_role_coins(tmp_path):
    game = _game(tmp_path)
    game.initial_request_challenge("A")
    game.complete_challenge("A", "Jubilee", hard=True)  # completed the harder challenge
    assert game.current_challenges("A") is None
    assert game.get_snake("A").coins == STARTING_COINS + HARDER_REWARD


def test_veto_keeps_an_offer(tmp_path):
    game = _game(tmp_path)
    game.initial_request_challenge("A")
    game.complete_challenge("A", "Jubilee")
    game.request_challenge("A", "Bond Street")

    game.veto_challenges("A")
    refreshed = game.current_challenges("A")
    assert refreshed is not None
    assert refreshed[0].difficulty <= refreshed[1].difficulty


def test_veto_requires_an_active_challenge(tmp_path):
    game = _game(tmp_path)
    with pytest.raises(ValueError, match="no active challenge"):
        game.veto_challenges("A")


def test_crashed_request_draws_no_offer(tmp_path):
    # A requests a neck through B's claimed Bond Street -> crashes -> no offer.
    game = new_game(
        {"A": "Baker Street", "B": "Bond Street"},
        bonus_interchanges=set(),
        challenge_pool=_pool(tmp_path),
        rng=random.Random(0),
    )
    game.initial_request_challenge("A")
    game.complete_challenge("A", "Jubilee")
    game.initial_request_challenge("B")
    game.complete_challenge("B", "Jubilee")

    game.request_challenge("A", "Green Park")
    assert game.get_snake("A").crashed
    assert game.current_challenges("A") is None


def test_no_pool_degrades_gracefully(tmp_path):
    game = new_game(
        {"A": "Wembley Park"},
        bonus_interchanges=set(),
        challenge_pool=None,
        challenges_path=str(tmp_path / "missing.json"),  # does not exist
    )
    assert game.challenges is None
    game.initial_request_challenge("A")
    assert game.current_challenges("A") is None
    game.complete_challenge("A", "Jubilee")  # still works without a pool


def test_offers_are_reproducible_with_a_seeded_rng(tmp_path):
    def draw():
        g = new_game(
            {"A": "Wembley Park"},
            bonus_interchanges=set(),
            challenge_pool=_pool(tmp_path),
            rng=random.Random(7),
        )
        g.initial_request_challenge("A")
        g.complete_challenge("A", "Jubilee")
        g.request_challenge("A", "Bond Street")
        return tuple(c.id for c in game_offer(g))

    def game_offer(g):
        offer = g.current_challenges("A")
        assert offer is not None
        return offer

    assert draw() == draw()
