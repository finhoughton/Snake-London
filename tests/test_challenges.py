from __future__ import annotations

import json
import random

import pytest

from challenges import Challenge, ChallengePool
from config import HARDER_REWARD, INITIAL_DIFFICULTY_MAX, INITIAL_DIFFICULTY_MIN, STARTING_COINS
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


def _wide_pool(tmp_path) -> ChallengePool:
    """A pool with several candidates inside [INITIAL_DIFFICULTY_MIN, INITIAL_DIFFICULTY_MAX]."""
    data = {
        "challenges": [
            {"id": "very_easy", "name": "VeryEasy", "description": "a", "difficulty": 1.0},
            {"id": "easy", "name": "Easy", "description": "b", "difficulty": 2.0},
            {"id": "mid_low", "name": "MidLow", "description": "c", "difficulty": 3.0},
            {"id": "mid_high", "name": "MidHigh", "description": "d", "difficulty": 5.0},
            {"id": "edge", "name": "Edge", "description": "e", "difficulty": 5.5},
            {"id": "hard", "name": "Hard", "description": "f", "difficulty": 7.0},
        ]
    }
    path = tmp_path / "wide_challenges.json"
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


# --- initial challenge: shared, drawn from a fixed band, no neck involved ---


def test_pick_in_range_only_returns_candidates_within_band(tmp_path):
    pool = _wide_pool(tmp_path)
    rng = random.Random(0)
    for _ in range(20):
        picked = pool.pick_in_range(INITIAL_DIFFICULTY_MIN, INITIAL_DIFFICULTY_MAX, rng=rng)
        assert INITIAL_DIFFICULTY_MIN <= picked.difficulty <= INITIAL_DIFFICULTY_MAX


def test_pick_in_range_falls_back_to_nearest_when_band_is_empty(tmp_path):
    pool = _pool(tmp_path)  # difficulties 1.0, 3.0, 6.0
    picked = pool.pick_in_range(3.6, 3.9)  # nothing qualifies in this narrow band
    assert picked.id == "mid"  # 3.0 is the closest challenge to the band's midpoint


def test_initial_challenge_is_drawn_once_in_new_game(tmp_path):
    game = new_game(
        {"A": "Wembley Park"},
        bonus_interchanges=set(),
        challenge_pool=_wide_pool(tmp_path),
        rng=random.Random(3),
    )
    assert game.initial_challenge is not None
    assert INITIAL_DIFFICULTY_MIN <= game.initial_challenge.difficulty <= INITIAL_DIFFICULTY_MAX


def test_all_teams_get_the_same_initial_challenge(tmp_path):
    game = new_game(
        {"A": "Wembley Park", "B": "Stratford"},
        bonus_interchanges=set(),
        challenge_pool=_wide_pool(tmp_path),
        rng=random.Random(3),
    )
    game.initial_request_challenge("A")
    game.initial_request_challenge("B")

    offer_a = game.current_challenges("A")
    offer_b = game.current_challenges("B")
    assert offer_a == offer_b
    # No easier/harder split yet — both slots are literally the same challenge.
    assert offer_a[0] is offer_a[1] is game.initial_challenge


def test_veto_during_initial_only_changes_the_vetoing_team(tmp_path):
    game = new_game(
        {"A": "Wembley Park", "B": "Stratford"},
        bonus_interchanges=set(),
        challenge_pool=_wide_pool(tmp_path),
        rng=random.Random(3),
    )
    game.initial_request_challenge("A")
    game.initial_request_challenge("B")
    shared_before = game.initial_challenge
    b_offer_before = game.current_challenges("B")

    game.veto_challenges("A")

    # B never vetoed -> still on the game's original shared initial challenge.
    assert game.initial_challenge == shared_before
    assert game.current_challenges("B") == b_offer_before
    assert game.current_challenges("B")[0] is game.initial_challenge

    # A now has its own replacement, no longer tied to GameState.initial_challenge.
    a_offer = game.current_challenges("A")
    assert a_offer is not None
    assert a_offer[0] is a_offer[1]  # still no easier/harder split, just a new single challenge


def test_veto_during_initial_does_not_disturb_a_team_past_the_initial_phase(tmp_path):
    game = new_game(
        {"A": "Wembley Park", "B": "Stratford"},
        bonus_interchanges=set(),
        challenge_pool=_wide_pool(tmp_path),
        rng=random.Random(3),
    )
    game.initial_request_challenge("A")
    game.complete_challenge("A", "Jubilee")  # A is past the initial phase
    game.request_challenge("A", "Bond Street")
    a_offer_before = game.current_challenges("A")

    game.initial_request_challenge("B")
    game.veto_challenges("B")  # B is still mid-initial; must not touch A's unrelated offer

    assert game.current_challenges("A") == a_offer_before
