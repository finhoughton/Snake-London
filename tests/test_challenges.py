from __future__ import annotations

import json
import random
import statistics
from pathlib import Path

import pytest

from challenges import Challenge, ChallengePool, get_difficulty, neck_weights
from config import HARDER_REWARD, INITIAL_DIFFICULTY_MAX, INITIAL_DIFFICULTY_MIN, STARTING_COINS
from game import GameState
from map import Map
from new_game import new_game


def _pool(tmp_path: Path) -> ChallengePool:
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


def _wide_pool(tmp_path: Path) -> ChallengePool:
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


def _game(tmp_path: Path):
    return new_game(
        {"A": "Wembley Park"},
        bonus_interchanges=set(),
        challenge_pool=_pool(tmp_path),
        rng=random.Random(0),
    )


def test_requesting_a_challenge_offers_a_pair(tmp_path: Path):
    game = _game(tmp_path)
    A = game.teams[0]
    game.complete_challenge(A.role_id, "Jubilee")
    game.request_challenge(A.role_id, "Bond Street")

    offer = game.current_challenges(A)
    assert offer is not None
    easier, harder = offer
    assert isinstance(easier, Challenge) and isinstance(harder, Challenge)
    assert easier.difficulty <= harder.difficulty


def test_initial_challenge_also_offers_a_pair(tmp_path: Path):
    game = _game(tmp_path)
    A = game.teams[0]
    assert game.current_challenges(A) is not None


def test_completing_clears_the_offer_and_awards_role_coins(tmp_path: Path):
    game = _game(tmp_path)
    A = game.teams[0]
    game.complete_challenge(A.role_id, "Jubilee")  # the initial challenge pays nothing
    assert game.get_snake(A).coins == STARTING_COINS

    game.request_challenge(A.role_id, "Bond Street")
    game.complete_challenge(A.role_id, "Jubilee", hard=True)  # completed the harder challenge
    assert game.current_challenges(A) is None
    assert game.get_snake(A).coins == STARTING_COINS + HARDER_REWARD


def test_veto_keeps_an_offer(tmp_path: Path):
    game = _game(tmp_path)
    A = game.teams[0]
    game.complete_challenge(A.role_id, "Jubilee")
    game.request_challenge(A.role_id, "Bond Street")

    game.veto_challenges(A.role_id)
    refreshed = game.current_challenges(A)
    assert refreshed is not None
    assert refreshed[0].difficulty <= refreshed[1].difficulty


def test_veto_requires_an_active_challenge(tmp_path: Path):
    game = _game(tmp_path)
    A = game.teams[0]
    # Start arms the initial challenge, so clear it before testing the guard.
    game.complete_challenge(A.role_id, "Jubilee")
    with pytest.raises(ValueError, match="no active challenge"):
        game.veto_challenges(A.role_id)


def test_crashed_request_draws_no_offer(tmp_path: Path):
    # A requests a neck through B's claimed Bond Street -> crashes -> no offer.
    game = new_game(
        {"A": "Baker Street", "B": "Bond Street"},
        bonus_interchanges=set(),
        challenge_pool=_pool(tmp_path),
        rng=random.Random(0),
    )
    A, B = game.teams
    game.complete_challenge(A.role_id, "Jubilee")
    game.complete_challenge(B.role_id, "Jubilee")

    game.request_challenge(A.role_id, "Green Park")
    assert game.get_snake(A).crashed
    assert game.current_challenges(A) is None


# def test_no_pool_degrades_gracefully(tmp_path: Path):
#     game = new_game(
#         {"A": "Wembley Park"},
#         bonus_interchanges=set(),
#         challenge_pool=None,
#         challenges_path=str(tmp_path / "missing.json"),  # does not exist
#     )
#     assert game.challenges is None
#     game.initial_request_challenge(A)
#     assert game.current_challenges(A) is None
#     game.complete_challenge(A, "Jubilee")  # still works without a pool


def test_offers_are_reproducible_with_a_seeded_rng(tmp_path: Path):
    def draw():
        g = new_game(
            {"A": "Wembley Park"},
            bonus_interchanges=set(),
            challenge_pool=_pool(tmp_path),
            rng=random.Random(7),
        )
        A = g.teams[0]
        g.complete_challenge(A.role_id, "Jubilee")
        g.request_challenge(A.role_id, "Bond Street")
        return tuple(c.id for c in game_offer(g))

    def game_offer(g: GameState):
        A = g.teams[0]
        offer = g.current_challenges(A)
        assert offer is not None
        return offer

    assert draw() == draw()


# --- initial challenge: shared, drawn from a fixed band, no neck involved ---


def test_pick_in_range_only_returns_candidates_within_band(tmp_path: Path):
    pool = _wide_pool(tmp_path)
    rng = random.Random(0)
    for _ in range(20):
        picked = pool.pick_in_range(INITIAL_DIFFICULTY_MIN, INITIAL_DIFFICULTY_MAX, rng=rng)
        assert INITIAL_DIFFICULTY_MIN <= picked.difficulty <= INITIAL_DIFFICULTY_MAX


def test_pick_in_range_falls_back_to_nearest_when_band_is_empty(tmp_path: Path):
    pool = _pool(tmp_path)  # difficulties 1.0, 3.0, 6.0
    picked = pool.pick_in_range(3.6, 3.9)  # nothing qualifies in this narrow band
    assert picked.id == "mid"  # 3.0 is the closest challenge to the band's midpoint


def test_initial_challenge_is_drawn_once_in_new_game(tmp_path: Path):
    game = new_game(
        {"A": "Wembley Park"},
        bonus_interchanges=set(),
        challenge_pool=_wide_pool(tmp_path),
        rng=random.Random(3),
    )
    assert game.initial_challenge is not None
    assert INITIAL_DIFFICULTY_MIN <= game.initial_challenge.difficulty <= INITIAL_DIFFICULTY_MAX


def test_all_teams_get_the_same_initial_challenge(tmp_path: Path):
    game = new_game(
        {"A": "Wembley Park", "B": "Stratford"},
        bonus_interchanges=set(),
        challenge_pool=_wide_pool(tmp_path),
        rng=random.Random(3),
    )
    A, B = game.teams

    offer_a = game.current_challenges(A)
    offer_b = game.current_challenges(B)
    assert offer_a is not None
    assert offer_a == offer_b
    # No easier/harder split yet — both slots are literally the same challenge.
    assert offer_a[0] is offer_a[1] is game.initial_challenge


def test_veto_during_initial_only_changes_the_vetoing_team(tmp_path: Path):
    game = new_game(
        {"A": "Wembley Park", "B": "Stratford"},
        bonus_interchanges=set(),
        challenge_pool=_wide_pool(tmp_path),
        rng=random.Random(3),
    )
    A, B = game.teams
    shared_before = game.initial_challenge
    b_offer_before = game.current_challenges(B)

    game.veto_challenges(A.role_id)

    # B never vetoed -> still on the game's original shared initial challenge.
    assert game.initial_challenge == shared_before
    b_offer = game.current_challenges(B)
    assert b_offer == b_offer_before
    assert b_offer is not None
    assert b_offer[0] is game.initial_challenge

    # A now has its own replacement, no longer tied to GameState.initial_challenge.
    a_offer = game.current_challenges(A)
    assert a_offer is not None
    assert a_offer[0] is a_offer[1]  # still no easier/harder split, just a new single challenge


def test_veto_during_initial_does_not_disturb_a_team_past_the_initial_phase(tmp_path: Path):
    game = new_game(
        {"A": "Wembley Park", "B": "Stratford"},
        bonus_interchanges=set(),
        challenge_pool=_wide_pool(tmp_path),
        rng=random.Random(3),
    )
    A, B = game.teams
    game.complete_challenge(A.role_id, "Jubilee")  # A is past the initial phase
    game.request_challenge(A.role_id, "Bond Street")
    a_offer_before = game.current_challenges(A)

    game.veto_challenges(B.role_id)  # B is still mid-initial; must not touch A's unrelated offer

    assert game.current_challenges(A) == a_offer_before


# --- no repeats: a team is never shown the same challenge twice --------------


def _numbered_pool(tmp_path: Path, difficulties: list[float]) -> ChallengePool:
    """Challenges c0, c1, ... with the given difficulties, in that order."""
    data = {
        "challenges": [
            {"id": f"c{i}", "name": f"C{i}", "description": "x", "difficulty": d} for i, d in enumerate(difficulties)
        ]
    }
    path = tmp_path / "numbered_challenges.json"
    path.write_text(json.dumps(data))
    return ChallengePool(str(path))


def _pair_for_before_exclusion(pool: ChallengePool, target: float, rng: random.Random):
    """ChallengePool.pair_for exactly as it was before ``exclude`` existed."""
    everything = pool.all()
    below = [c for c in everything if target - 1.5 <= c.difficulty <= target]
    above = [c for c in everything if target < c.difficulty <= target + 1.5]
    if not below:
        below = [min(everything, key=lambda c: abs(c.difficulty - target))]
    if not above:
        above = [max(everything, key=lambda c: c.difficulty if c.difficulty > target else -1)]
        if above[0].difficulty <= target:
            above = [everything[-1]]
    return rng.choice(below), rng.choice(above)


def test_with_nothing_excluded_offers_are_unchanged(tmp_path: Path):
    pool = _wide_pool(tmp_path)
    for target in (0.5, 2.0, 3.7, 5.2, 6.9, 9.0):  # includes targets whose bands are empty
        for seed in range(10):
            assert pool.pair_for(target, rng=random.Random(seed)) == _pair_for_before_exclusion(
                pool, target, random.Random(seed)
            )


def test_offers_skip_challenges_the_team_has_seen(tmp_path: Path):
    pool = _numbered_pool(tmp_path, [2.0, 2.5, 3.0, 3.5, 4.0, 4.5])
    # target 3.0: easier band [1.5, 3.0] is c0-c2, harder band (3.0, 4.5] is c3-c5
    for seed in range(30):
        easier, harder = pool.pair_for(3.0, rng=random.Random(seed), exclude={"c0", "c1", "c3", "c4"})
        assert (easier.id, harder.id) == ("c2", "c5")


def test_a_band_stretches_away_from_the_target_before_repeating(tmp_path: Path):
    pool = _numbered_pool(tmp_path, [1.0, 3.0, 4.0, 6.5])
    # Both bands' only challenges (c1, c2) have been seen: the easier band stretches
    # down to c0, the harder band up to c3, rather than showing either again.
    easier, harder = pool.pair_for(3.0, rng=random.Random(0), exclude={"c1", "c2"})
    assert (easier.id, harder.id) == ("c0", "c3")


def test_repeats_rather_than_failing_when_everything_has_been_seen(tmp_path: Path):
    pool = _numbered_pool(tmp_path, [2.0, 4.0])
    easier, harder = pool.pair_for(3.0, rng=random.Random(0), exclude={"c0", "c1"})
    assert (easier.id, harder.id) == ("c0", "c1")
    assert pool.pick_in_range(1.0, 5.0, rng=random.Random(0), exclude={"c0", "c1"}).id in {"c0", "c1"}


def test_pick_in_range_skips_seen_challenges(tmp_path: Path):
    pool = _wide_pool(tmp_path)
    band = [c for c in pool.all() if INITIAL_DIFFICULTY_MIN <= c.difficulty <= INITIAL_DIFFICULTY_MAX]
    assert len(band) > 1
    seen = {c.id for c in band[:-1]}
    for seed in range(20):
        picked = pool.pick_in_range(
            INITIAL_DIFFICULTY_MIN, INITIAL_DIFFICULTY_MAX, rng=random.Random(seed), exclude=seen
        )
        assert picked.id == band[-1].id


def test_a_team_is_never_offered_the_same_challenge_twice(tmp_path: Path):
    # 96 challenges from 1.0 to 10.5 and a one-stop hop, so there's room on both sides of
    # the target: a long run of vetoes has to stretch its bands, but never repeats. (A
    # five-stop route targets ~9, where only 15 challenges sit above it; once a team has
    # seen all of those, repeating is the right answer — see the test above.)
    pool = _numbered_pool(tmp_path, [1.0 + 0.1 * i for i in range(96)])
    game = new_game({"A": "Wembley Park"}, bonus_interchanges=set(), challenge_pool=pool, rng=random.Random(0))
    A = game.teams[0]
    shown = [game.initial_challenge.id]
    game.complete_challenge(A.role_id, "Jubilee")
    game.request_challenge(A.role_id, "West Hampstead")
    for _ in range(15):
        shown += [c.id for c in game.current_challenges(A)]
        game.veto_challenges(A.role_id)
    shown += [c.id for c in game.current_challenges(A)]
    assert len(shown) == len(set(shown)), "a challenge was offered to the same team twice"
    assert set(shown) == game.get_snake(A).seen_challenges


def test_the_shared_initial_challenge_counts_as_seen_but_only_per_team(tmp_path: Path):
    game = new_game(
        {"A": "Wembley Park", "B": "Stratford"},
        bonus_interchanges=set(),
        challenge_pool=_wide_pool(tmp_path),
        rng=random.Random(3),
    )
    A, B = game.teams
    shared = game.initial_challenge.id
    game.veto_challenges(A.role_id)  # A draws its own replacement initial challenge
    assert game.current_challenges(A)[0].id != shared  # never the one A has already been shown
    assert game.get_snake(A).seen_challenges >= {shared, game.current_challenges(A)[0].id}
    assert game.get_snake(B).seen_challenges == {shared}  # A's veto doesn't touch B


# --- difficulty calibration --------------------------------------------------


def test_typical_routes_are_sized_to_the_challenge_pool():
    """Guards get_difficulty's calibration against drifting away from challenges.json.

    Before it was recalibrated, real routes scored 2.1-6.3 against a pool centred on
    6.0, and about a quarter of the pool could never be offered.
    """
    game_map = Map("map/connections.json")
    pool = sorted(c.difficulty for c in ChallengePool("challenges.json").all())
    short_hops, every_route = [], []
    for line in game_map.line_keys():
        stations = game_map.get_line(line).stations
        for a in stations:
            for b in stations:
                if a == b:
                    continue
                path = game_map.path_between_on_line(line, a, b)
                target = get_difficulty(neck_weights(game_map, line, path[1:]))
                every_route.append(target)
                if len(path) - 1 <= 2:
                    short_hops.append(target)

    assert abs(statistics.median(short_hops) - statistics.median(pool)) <= 1.5  # everyday hops sit mid-pool
    assert max(every_route) + 1.5 >= pool[-1]  # the hardest challenge can still be offered...
    assert min(every_route) - 1.5 <= pool[0]  # ...and so can the easiest
    assert 0 <= min(every_route) and max(every_route) < 10


def test_difficulty_rises_with_neck_length_and_station_importance():
    assert get_difficulty([]) == 0.0
    assert get_difficulty([2]) < get_difficulty([2, 2]) < get_difficulty([2, 2, 2])
    assert get_difficulty([2, 2]) < get_difficulty([2, 8]) < get_difficulty([8, 8])
