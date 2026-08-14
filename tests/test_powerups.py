"""Executable specification for the powerups system (see PLAN_POWERUPS.md).

Written ahead of the implementation: another model implements powerups to make
this file pass. The whole module skips until a top-level ``powerups`` module
exists, so the existing suite stays green today — creating ``powerups.py`` is
the first implementation step, and doing so activates this spec.

Map facts used below (verified against map/connections.json):
  - Jubilee runs Baker Street — Bond Street — Green Park — Westminster (consecutive).
  - Baker Street is also on the Bakerloo line, adjacent to Oxford Circus.
  - Baker Street is NOT on the Victoria line.
  - Picc runs Rayners Lane — Park Royal — Ealing Common — Acton Town — Turnham Green.
"""

from __future__ import annotations

import random

import pytest

import config
from game import new_game

powerups = pytest.importorskip("powerups", reason="powerups.py not implemented yet — see PLAN_POWERUPS.md")
Curse = powerups.Curse
CurseDeck = powerups.CurseDeck

EXPECTED_COSTS = {"jump": 8, "efficiency": 4, "double_up": 3, "retreat": 3, "detour": 2, "curse": 3}

_CURSES_JSON = """{
  "curses": [
    {"id": "get_a_melon", "name": "Get a melon",
     "description": "Acquire a melon and keep it with you for the rest of the game."},
    {"id": "egg_partner", "name": "Curse of the Egg partner",
     "description": "Acquire an egg; it is now an official team member."},
    {"id": "pub", "name": "Pub",
     "description": "Go to a pub and drink one pint of liquid per team member."}
  ]
}"""


def _curses_path(tmp_path) -> str:
    path = tmp_path / "curses.json"
    path.write_text(_CURSES_JSON)
    return str(path)


def _game(tmp_path, teams=None, **kwargs):
    """A hermetic game: no challenge pool, no bonuses, a 3-curse deck, seeded rng."""
    teams = teams or {"A": "Baker Street", "B": "Stratford"}
    kwargs.setdefault("bonus_interchanges", set())
    kwargs.setdefault("challenges_path", str(tmp_path / "missing_challenges.json"))
    kwargs.setdefault("curses_path", _curses_path(tmp_path))
    kwargs.setdefault("rng", random.Random(0))
    return new_game(teams, **kwargs)


# --- costs and configuration -------------------------------------------------


def test_powerup_costs_defined():
    for pid, cost in EXPECTED_COSTS.items():
        assert config.POWERUP_COSTS[pid] == cost


def test_all_powerups_enabled_by_default(tmp_path):
    game = _game(tmp_path)
    assert game.enabled_powerups == set(config.POWERUP_COSTS)

    game.get_snake("A").coins = 10000
    for pid in config.POWERUP_COSTS:
        game.buy_powerup("A", pid)
    for pid in config.POWERUP_COSTS:
        assert pid in game.get_snake("A").hand


def test_disabled_powerups_cannot_be_bought(tmp_path):
    game = _game(tmp_path, enabled_powerups={"jump"})
    assert game.enabled_powerups == {"jump"}
    snake = game.get_snake("A")
    snake.coins = 50

    with pytest.raises(ValueError, match="enabled"):
        game.buy_powerup("A", "efficiency")
    game.buy_powerup("A", "jump")  # the enabled one still works
    assert snake.hand == ["jump"]


# --- buying ------------------------------------------------------------------


def test_buy_deducts_coins_and_adds_to_hand(tmp_path):
    game = _game(tmp_path)
    snake = game.get_snake("A")
    snake.coins = 10

    game.buy_powerup("A", "efficiency")

    assert snake.coins == 10 - config.POWERUP_COSTS["efficiency"]
    assert snake.hand == ["efficiency"]


def test_buy_requires_enough_coins(tmp_path):
    game = _game(tmp_path)
    snake = game.get_snake("A")
    snake.coins = config.POWERUP_COSTS["jump"] - 1

    with pytest.raises(ValueError, match="coins"):
        game.buy_powerup("A", "jump")
    assert snake.hand == []
    assert snake.coins == config.POWERUP_COSTS["jump"] - 1  # nothing deducted


def test_buy_unknown_powerup_raises(tmp_path):
    game = _game(tmp_path)
    game.get_snake("A").coins = 50
    with pytest.raises(ValueError):
        game.buy_powerup("A", "teleport")


def test_hand_is_unlimited_and_allows_duplicates(tmp_path):
    game = _game(tmp_path)
    snake = game.get_snake("A")
    snake.coins = 50
    for _ in range(3):
        game.buy_powerup("A", "double_up")
    assert snake.hand.count("double_up") == 3


def test_eliminated_teams_cannot_buy_or_play(tmp_path):
    game = _game(tmp_path)
    snake = game.get_snake("A")
    snake.coins = 50
    game.buy_powerup("A", "efficiency")  # in hand before the crash

    game.crash("A")
    with pytest.raises(ValueError, match="crashed"):
        game.buy_powerup("A", "efficiency")
    with pytest.raises(ValueError, match="crashed"):
        game.play_powerup("A", "efficiency")

    game.concede("B")
    game.get_snake("B").coins = 50
    with pytest.raises(ValueError, match="conceded"):
        game.buy_powerup("B", "efficiency")


# --- playing (generic) -------------------------------------------------------


def test_play_requires_powerup_in_hand(tmp_path):
    game = _game(tmp_path)
    game.get_snake("A").coins = 50
    with pytest.raises(ValueError, match="hand"):
        game.play_powerup("A", "efficiency")


def test_play_consumes_one_copy_from_hand(tmp_path):
    game = _game(tmp_path)
    snake = game.get_snake("A")
    snake.coins = 50
    game.buy_powerup("A", "efficiency")
    game.buy_powerup("A", "efficiency")

    game.play_powerup("A", "efficiency")
    assert snake.hand.count("efficiency") == 1
    game.play_powerup("A", "efficiency")  # wasted (already armed) but still consumed
    assert snake.hand.count("efficiency") == 0
    assert snake.free_vetoes == 1


def test_failed_play_keeps_the_card(tmp_path):
    game = _game(tmp_path)
    snake = game.get_snake("A")
    snake.coins = 50
    game.buy_powerup("A", "jump")

    with pytest.raises(ValueError):
        game.play_powerup("A", "jump", station="Narnia")
    assert snake.hand == ["jump"]  # invalid target must not consume the powerup


# --- jump --------------------------------------------------------------------


def test_jump_lets_a_neck_pass_through_a_claim_without_stealing_it(tmp_path):
    game = _game(tmp_path, teams={"A": "Baker Street", "B": "Bond Street"})
    game.initial_request_challenge("A")
    game.complete_challenge("A", "Jubilee")  # A claims Baker Street
    game.initial_request_challenge("B")
    game.complete_challenge("B", "Jubilee")  # B claims Bond Street

    snake_a = game.get_snake("A")
    snake_a.coins = 20
    assert "Bond Street" not in game.jumped_stations
    game.buy_powerup("A", "jump")
    game.play_powerup("A", "jump", station="Bond Street")
    assert "Bond Street" in game.jumped_stations

    # Path Baker -> Bond (B's, jumped) -> Green Park: legal AND survivable now.
    game.request_challenge("A", "Green Park")
    assert not snake_a.crashed
    assert game.neck("A") == ["Bond Street", "Green Park"]

    # Completing claims the rest of the neck but never steals B's station.
    game.complete_challenge("A", "Jubilee")
    assert game.map.get_claim("Bond Street") == "B"
    assert game.map.get_claim("Green Park") == "A"
    assert snake_a.anchor == "Green Park"
    assert not game.get_snake("B").crashed


def test_jump_benefits_all_players(tmp_path):
    # Jump is global: a *different* team may also travel through (and front at)
    # the jumped station for the rest of the game.
    game = _game(tmp_path, teams={"A": "Baker Street", "B": "Bond Street", "C": "Westminster"})
    for team in ("A", "B", "C"):
        game.initial_request_challenge(team)
        game.complete_challenge(team, "Jubilee")

    game.get_snake("A").coins = 20
    game.buy_powerup("A", "jump")
    game.play_powerup("A", "jump", station="Bond Street")

    # C's path Westminster -> Green Park -> Bond Street fronts AT the jumped claim.
    game.request_challenge("C", "Bond Street")
    assert not game.get_snake("C").crashed


def test_jump_protects_own_neck_preemptively(tmp_path):
    game = _game(tmp_path, teams={"A": "Baker Street", "B": "Westminster"})
    game.initial_request_challenge("A")
    game.complete_challenge("A", "Jubilee")
    game.initial_request_challenge("B")
    game.complete_challenge("B", "Jubilee")

    game.request_challenge("A", "Green Park")  # A's neck = [Bond Street, Green Park]
    snake_a = game.get_snake("A")
    snake_a.coins = 20
    game.buy_powerup("A", "jump")
    game.play_powerup("A", "jump", station="Green Park")  # pre-emptive protection

    # B claims Green Park out from under A's neck — normally a crash for A.
    game.request_challenge("B", "Green Park")
    game.complete_challenge("B", "Jubilee")
    assert game.map.get_claim("Green Park") == "B"
    assert not snake_a.crashed
    assert game.is_neck_safe("A")


def test_jump_allows_travel_through_own_claim(tmp_path):
    game = _game(tmp_path, teams={"A": "Baker Street"})
    game.initial_request_challenge("A")
    game.complete_challenge("A", "Jubilee")
    game.map.claim("Bond Street", "A")  # simulate an earlier body extension

    snake = game.get_snake("A")
    snake.coins = 20
    game.buy_powerup("A", "jump")
    game.play_powerup("A", "jump", station="Bond Street")

    # Without the jump, requesting through your own claim crashes you.
    game.request_challenge("A", "Green Park")
    assert not snake.crashed
    assert game.neck("A") == ["Bond Street", "Green Park"]


def test_jumping_through_a_claim_does_not_steal_its_segments(tmp_path):
    # Jumping grants passage, never ownership — and that has to hold for the track
    # between two interchanges as well as for the interchanges themselves. This is
    # the only way two teams can ever travel the same segment.
    game = _game(tmp_path, teams={"A": "Rayners Lane", "B": "Turnham Green"})
    for team in ("A", "B"):
        game.initial_request_challenge(team)
        game.complete_challenge(team, "Picc")

    game.request_challenge("A", "Acton Town")
    game.complete_challenge("A", "Picc")
    assert game.map.get_segment_claim("Picc", "Ealing Common", "Acton Town") == "A"

    # B jumps both of A's interchanges, then re-travels the track between them.
    game.get_snake("B").coins = 20
    for station in ("Acton Town", "Ealing Common"):
        game.buy_powerup("B", "jump")
        game.play_powerup("B", "jump", station=station)
    game.request_challenge("B", "Ealing Common")
    assert not game.get_snake("B").crashed
    game.complete_challenge("B", "Picc")

    # The stretch running between two of A's interchanges is still A's...
    assert game.map.get_segment_claim("Picc", "Ealing Common", "Acton Town") == "A"
    assert game.map.get_claim("Ealing Common") == "A"
    assert game.map.get_claim("Acton Town") == "A"
    # ...but the one B opened up from its own station is B's.
    assert game.map.get_segment_claim("Picc", "Turnham Green", "Acton Town") == "B"
    # B fronted at a jumped interchange it does not own: Anchor without Body.
    assert game.get_snake("B").anchor == "Ealing Common"
    assert game.body_stations("B") == ["Turnham Green"]


# --- efficiency --------------------------------------------------------------


def test_efficiency_makes_the_next_veto_free(tmp_path):
    game = _game(tmp_path)
    snake = game.get_snake("A")
    snake.coins = 10
    game.buy_powerup("A", "efficiency")
    game.play_powerup("A", "efficiency")
    assert snake.free_vetoes == 1

    game.initial_request_challenge("A")
    assert game.veto_challenges("A") is True  # free — bot skips the veto period
    assert snake.free_vetoes == 0
    assert game.veto_challenges("A") is False  # back to a normal veto


def test_efficiency_does_not_stack(tmp_path):
    # Playing a second copy while one is armed is wasted: consumed, no effect.
    game = _game(tmp_path)
    snake = game.get_snake("A")
    snake.coins = 20
    for _ in range(2):
        game.buy_powerup("A", "efficiency")
        game.play_powerup("A", "efficiency")
    assert snake.free_vetoes == 1
    assert snake.hand == []  # the wasted copy was still consumed

    game.initial_request_challenge("A")
    assert game.veto_challenges("A") is True
    assert game.veto_challenges("A") is False  # only ONE veto was free


# --- double up ---------------------------------------------------------------


def test_double_up_doubles_the_next_two_challenge_rewards(tmp_path):
    game = _game(tmp_path, teams={"A": "Baker Street"})
    snake = game.get_snake("A")
    snake.coins = 50
    game.buy_powerup("A", "double_up")
    game.play_powerup("A", "double_up")
    assert snake.double_up_remaining == 2
    base = snake.coins

    game.initial_request_challenge("A")
    game.complete_challenge("A", "Jubilee")  # pays nothing, so no charge is spent
    assert snake.double_up_remaining == 2
    game.request_challenge("A", "Bond Street")
    game.complete_challenge("A", "Jubilee")  # easier, doubled
    game.request_challenge("A", "Green Park")
    game.complete_challenge("A", "Jubilee", hard=True)  # harder, doubled
    assert snake.double_up_remaining == 0
    game.request_challenge("A", "Westminster")
    game.complete_challenge("A", "Jubilee")  # back to normal

    expected = base + 2 * config.EASIER_REWARD + 2 * config.HARDER_REWARD + config.EASIER_REWARD
    assert snake.coins == expected


def test_double_up_does_not_double_bonus_coins(tmp_path):
    game = _game(tmp_path, teams={"A": "Baker Street"}, bonus_interchanges={"Bond Street"})
    snake = game.get_snake("A")
    snake.coins = 50
    game.buy_powerup("A", "double_up")
    game.play_powerup("A", "double_up")
    base = snake.coins

    game.initial_request_challenge("A")
    game.complete_challenge("A", "Jubilee")  # initial: pays nothing, spends no charge
    game.request_challenge("A", "Bond Street")
    game.complete_challenge("A", "Jubilee", hard=True)  # doubled + UN-doubled front bonus

    expected = base + 2 * config.HARDER_REWARD + config.BONUS_AT_FRONT
    assert snake.coins == expected


def test_double_up_does_not_stack(tmp_path):
    # The counter is SET to 2, never added to: a second play at the full 2 is
    # wasted (consumed, no effect) — it does not become 4.
    game = _game(tmp_path)
    snake = game.get_snake("A")
    snake.coins = 50
    for _ in range(2):
        game.buy_powerup("A", "double_up")
        game.play_powerup("A", "double_up")
    assert snake.double_up_remaining == 2
    assert snake.hand == []  # the wasted copy was still consumed


def test_double_up_refreshes_from_one_back_to_two(tmp_path):
    game = _game(tmp_path, teams={"A": "Baker Street"})
    snake = game.get_snake("A")
    snake.coins = 50
    game.buy_powerup("A", "double_up")
    game.buy_powerup("A", "double_up")
    game.play_powerup("A", "double_up")
    assert snake.double_up_remaining == 2

    game.initial_request_challenge("A")
    game.complete_challenge("A", "Jubilee")  # initial: unpaid, so still 2
    game.request_challenge("A", "Bond Street")
    game.complete_challenge("A", "Jubilee")  # one doubled completion used
    assert snake.double_up_remaining == 1

    game.play_powerup("A", "double_up")  # tops back up to 2, does not add
    assert snake.double_up_remaining == 2


# --- retreat -----------------------------------------------------------------


def test_retreat_cancels_the_active_request(tmp_path):
    game = _game(tmp_path, teams={"A": "Baker Street"})
    game.initial_request_challenge("A")
    game.complete_challenge("A", "Jubilee")
    game.request_challenge("A", "Bond Street")

    snake = game.get_snake("A")
    snake.coins = 20
    game.buy_powerup("A", "retreat")
    game.play_powerup("A", "retreat")

    assert snake.neck_active is False
    assert snake.front == snake.anchor == "Baker Street"
    assert snake.offer is None
    assert game.neck("A") == []
    assert snake.blocked_station == "Bond Street"


def test_retreat_blocks_the_same_station_until_a_different_request(tmp_path):
    game = _game(tmp_path, teams={"A": "Baker Street"})
    game.initial_request_challenge("A")
    game.complete_challenge("A", "Jubilee")
    game.request_challenge("A", "Bond Street")

    snake = game.get_snake("A")
    snake.coins = 20
    game.buy_powerup("A", "retreat")
    game.play_powerup("A", "retreat")

    with pytest.raises(ValueError, match="different"):
        game.request_challenge("A", "Bond Street")

    game.request_challenge("A", "Green Park")  # a different station is fine
    assert snake.blocked_station is None
    assert game.neck("A") == ["Bond Street", "Green Park"]


def test_retreat_requires_an_active_normal_request(tmp_path):
    game = _game(tmp_path)
    snake = game.get_snake("A")
    snake.coins = 20
    game.buy_powerup("A", "retreat")

    # No active neck at all.
    with pytest.raises(ValueError, match="active"):
        game.play_powerup("A", "retreat")
    assert "retreat" in snake.hand  # failed play keeps the card

    # The initial challenge cannot be retreated.
    game.initial_request_challenge("A")
    with pytest.raises(ValueError, match="initial"):
        game.play_powerup("A", "retreat")
    assert "retreat" in snake.hand


# --- detour ------------------------------------------------------------------


def test_detour_switches_the_line_travelled_but_not_the_one_announced(tmp_path):
    game = _game(tmp_path, teams={"A": "Baker Street"})
    game.initial_request_challenge("A")
    game.complete_challenge("A", "Jubilee")  # declared Jubilee

    snake = game.get_snake("A")
    snake.coins = 20
    game.buy_powerup("A", "detour")
    game.play_powerup("A", "detour", line="Bakerloo")  # Baker Street is on Bakerloo
    assert snake.travel_line == "Bakerloo"
    assert snake.announced_line == "Jubilee", "Detour is not announced"

    game.request_challenge("A", "Oxford Circus")  # travels the NEW line
    assert game.neck("A") == ["Oxford Circus"]


def test_detour_validates_the_new_line(tmp_path):
    game = _game(tmp_path, teams={"A": "Baker Street"})
    game.initial_request_challenge("A")
    game.complete_challenge("A", "Jubilee")
    snake = game.get_snake("A")
    snake.coins = 50
    game.buy_powerup("A", "detour")

    with pytest.raises(ValueError):
        game.play_powerup("A", "detour", line="Victoria")  # not at Baker Street
    with pytest.raises(ValueError):
        game.play_powerup("A", "detour", line="Hogwarts Express")  # unknown
    assert "detour" in snake.hand
    assert snake.travel_line == "Jubilee"  # untouched by the failed plays


def test_detour_requires_a_declared_line(tmp_path):
    game = _game(tmp_path, teams={"A": "Baker Street", "B": "Stratford"})
    game.get_snake("B").coins = 20
    game.buy_powerup("B", "detour")
    with pytest.raises(ValueError, match="declared"):
        game.play_powerup("B", "detour", line="Jubilee")  # pre-initial: no line yet


def test_detour_mid_challenge_overrides_the_next_declared_line(tmp_path):
    # Powerups are playable "at any time while not on transport", which includes
    # mid-challenge. The team boards next at the Front, so the detour is parked
    # until this challenge completes, then silently replaces what was declared.
    game = _game(tmp_path, teams={"A": "Baker Street"})
    game.initial_request_challenge("A")
    game.complete_challenge("A", "Jubilee")
    game.request_challenge("A", "Bond Street")  # neck now active

    snake = game.get_snake("A")
    snake.coins = 20
    game.buy_powerup("A", "detour")
    game.play_powerup("A", "detour", line="Central")  # Bond Street is on Central
    assert snake.pending_detour == "Central"
    assert snake.travel_line == "Jubilee"  # the current neck is untouched
    assert game.neck("A") == ["Bond Street"]

    game.complete_challenge("A", "Jubilee")  # declares Jubilee publicly...
    assert snake.travel_line == "Central"  # ...but actually boards Central
    assert snake.announced_line == "Jubilee", "the announcement stands — that is the secret"
    assert snake.pending_detour is None

    game.request_challenge("A", "Oxford Circus")  # only reachable on Central
    assert game.neck("A") == ["Oxford Circus"]


def test_detour_mid_challenge_validates_against_the_front(tmp_path):
    game = _game(tmp_path, teams={"A": "Baker Street"})
    game.initial_request_challenge("A")
    game.complete_challenge("A", "Jubilee")
    game.request_challenge("A", "Bond Street")

    snake = game.get_snake("A")
    snake.coins = 20
    game.buy_powerup("A", "detour")

    # Bakerloo serves the Anchor (Baker Street) but not the Front (Bond Street),
    # and the Front is where this team boards next.
    with pytest.raises(ValueError, match="Bond Street"):
        game.play_powerup("A", "detour", line="Bakerloo")
    assert snake.pending_detour is None
    assert "detour" in snake.hand


def test_retreat_lapses_a_parked_detour(tmp_path):
    game = _game(tmp_path, teams={"A": "Baker Street"})
    game.initial_request_challenge("A")
    game.complete_challenge("A", "Jubilee")
    game.request_challenge("A", "Bond Street")

    snake = game.get_snake("A")
    snake.coins = 50
    game.buy_powerup("A", "detour")
    game.play_powerup("A", "detour", line="Central")
    game.buy_powerup("A", "retreat")
    game.play_powerup("A", "retreat")

    # The detour was aimed at boarding from Bond Street, which A never reaches.
    assert snake.pending_detour is None
    game.request_challenge("A", "Green Park")
    game.complete_challenge("A", "Jubilee")
    assert snake.travel_line == "Jubilee"
    assert snake.announced_line == "Jubilee"  # nothing secret survived the retreat


# --- curse -------------------------------------------------------------------


def test_curse_deck_loads_from_json(tmp_path):
    deck = CurseDeck(_curses_path(tmp_path))
    assert len(deck.all()) == 3
    assert deck.get("pub").name == "Pub"
    with pytest.raises(KeyError):
        deck.get("nonexistent")

    drawn = deck.draw(rng=random.Random(1))
    assert isinstance(drawn, Curse)
    # Draws deplete the deck: the drawn curse is gone from what remains.
    assert drawn not in deck.all()
    assert len(deck.all()) == 2


def test_curse_deck_draws_without_replacement_and_resets_when_empty(tmp_path):
    deck = CurseDeck(_curses_path(tmp_path))
    rng = random.Random(2)

    drawn_ids = {deck.draw(rng=rng).id for _ in range(3)}
    assert drawn_ids == {"get_a_melon", "egg_partner", "pub"}  # each curse exactly once per cycle
    assert deck.all() == []

    # An emptied deck resets to full: the next draw succeeds from a fresh cycle.
    fourth = deck.draw(rng=rng)
    assert fourth.id in {"get_a_melon", "egg_partner", "pub"}
    assert len(deck.all()) == 2  # refilled to 3, then one drawn


def test_buying_a_curse_draws_a_concrete_curse_into_the_hand(tmp_path):
    game = _game(tmp_path)
    snake = game.get_snake("A")
    snake.coins = 20

    curse = game.buy_powerup("A", "curse")

    # The buyer knows exactly which curse they hold, before picking a target.
    assert isinstance(curse, Curse)
    assert curse.id in {"get_a_melon", "egg_partner", "pub"}
    assert snake.held_curses == [curse]
    assert snake.hand == ["curse"]
    assert game.get_snake("B").curses == []  # nothing inflicted until it is played


def test_curse_leaves_the_deck_at_buy_time_not_play_time(tmp_path):
    game = _game(tmp_path)
    assert game.curse_deck is not None
    game.get_snake("A").coins = 20

    curse = game.buy_powerup("A", "curse")

    # Already gone from the deck, even though it has not been played yet.
    assert curse not in game.curse_deck.all()
    assert len(game.curse_deck.all()) == 2

    game.play_powerup("A", "curse", target_team="B")
    assert len(game.curse_deck.all()) == 2  # playing a held curse never touches the deck


def test_a_rejected_buy_does_not_consume_a_curse_from_the_deck(tmp_path):
    game = _game(tmp_path)
    assert game.curse_deck is not None
    snake = game.get_snake("A")
    snake.coins = config.POWERUP_COSTS["curse"] - 1

    with pytest.raises(ValueError, match="coins"):
        game.buy_powerup("A", "curse")

    assert len(game.curse_deck.all()) == 3  # the draw happens only after every check passes
    assert snake.held_curses == []
    assert snake.hand == []


def test_curses_stay_available_after_the_deck_cycles(tmp_path):
    game = _game(tmp_path)
    snake = game.get_snake("A")
    snake.coins = 50
    drawn = [game.buy_powerup("A", "curse") for _ in range(4)]

    assert {c.id for c in drawn[:3]} == {"get_a_melon", "egg_partner", "pub"}  # first cycle
    assert drawn[3].id in {"get_a_melon", "egg_partner", "pub"}  # from the reset deck

    played = [game.play_powerup("A", "curse", target_team="B") for _ in range(4)]

    assert played == drawn  # no curse_id given: played oldest-first, in buy order
    assert game.get_snake("B").curses == drawn
    assert snake.hand == []
    assert snake.held_curses == []
    game.buy_powerup("A", "curse")  # still purchasable — the deck never runs dry


def test_playing_a_held_curse_attaches_it_to_the_target(tmp_path):
    game = _game(tmp_path)
    game.get_snake("A").coins = 20
    game.buy_powerup("A", "curse")  # the concrete curse is drawn here, at buy time

    curse = game.play_powerup("A", "curse", target_team="B")

    assert isinstance(curse, Curse)
    assert curse.id in {"get_a_melon", "egg_partner", "pub"}
    assert game.get_snake("B").curses == [curse]
    assert "curse" not in game.get_snake("A").hand


def test_playing_a_chosen_curse_by_id_leaves_the_others_held(tmp_path):
    game = _game(tmp_path)
    snake = game.get_snake("A")
    snake.coins = 50
    first = game.buy_powerup("A", "curse")
    second = game.buy_powerup("A", "curse")
    assert isinstance(first, Curse) and isinstance(second, Curse)
    assert first.id != second.id  # distinct within a single deck cycle

    played = game.play_powerup("A", "curse", target_team="B", curse_id=second.id)

    assert played == second  # the chosen one, not the oldest held
    assert game.get_snake("B").curses == [second]
    assert snake.held_curses == [first]  # the unplayed curse is still held
    assert snake.hand == ["curse"]  # and so is its card


def test_playing_a_curse_id_you_do_not_hold_raises(tmp_path):
    game = _game(tmp_path)
    snake = game.get_snake("A")
    snake.coins = 50
    held = game.buy_powerup("A", "curse")

    with pytest.raises(ValueError, match="does not hold"):
        game.play_powerup("A", "curse", target_team="B", curse_id="nonexistent")

    assert snake.hand == ["curse"]  # a failed play keeps the card...
    assert snake.held_curses == [held]  # ...and the curse it was holding
    assert game.get_snake("B").curses == []


def test_curse_requires_a_valid_living_opponent(tmp_path):
    game = _game(tmp_path)
    snake = game.get_snake("A")
    snake.coins = 50
    game.buy_powerup("A", "curse")

    with pytest.raises(ValueError, match="another"):
        game.play_powerup("A", "curse", target_team="A")  # not yourself
    with pytest.raises(ValueError):
        game.play_powerup("A", "curse", target_team="Zeta")  # unknown team
    game.crash("B")
    with pytest.raises(ValueError):
        game.play_powerup("A", "curse", target_team="B")  # eliminated target
    assert "curse" in snake.hand  # none of the failed plays consumed it


def test_missing_curses_file_disables_the_curse_powerup(tmp_path):
    game = _game(
        tmp_path,
        curses_path=str(tmp_path / "absent.json"),
        enabled_powerups={"curse", "jump"},  # explicit enabling doesn't resurrect it
    )
    assert game.curse_deck is None
    assert game.enabled_powerups == {"jump"}

    game.get_snake("A").coins = 50
    with pytest.raises(ValueError):
        game.buy_powerup("A", "curse")


def test_curse_draw_is_reproducible_with_a_seeded_rng(tmp_path):
    def drawn_curse(seed: int) -> str:
        game = _game(tmp_path, rng=random.Random(seed))
        game.get_snake("A").coins = 20
        game.buy_powerup("A", "curse")
        return game.play_powerup("A", "curse", target_team="B").id

    assert drawn_curse(9) == drawn_curse(9)
