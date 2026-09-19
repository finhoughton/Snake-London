"""The powerups system: buying, playing, and each powerup's effect.

Map facts used below (verified against map/connections.json):
  - Jubilee runs Baker Street — Bond Street — Green Park — Westminster (consecutive).
  - Baker Street is also on the Bakerloo line, adjacent to Oxford Circus.
  - Baker Street is NOT on the Victoria line.
  - Picc runs Rayners Lane — Park Royal — Ealing Common — Acton Town — Turnham Green.
"""

from __future__ import annotations

import random
import re
from pathlib import Path
from typing import Any

import pytest

import config
from new_game import new_game
from powerups import Curse, CurseDeck

EXPECTED_COSTS = {"jump": 13, "efficiency": 8, "retreat": 5, "detour": 4, "curse": 5}

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


def _curses_path(tmp_path: Path) -> str:
    path = tmp_path / "curses.json"
    path.write_text(_CURSES_JSON)
    return str(path)


def _game(tmp_path: Path, teams: dict[str, str] | None = None, **kwargs: Any):
    """A hermetic game: no challenge pool, no bonuses, a 3-curse deck, seeded rng."""
    teams = teams or {"A": "Baker Street", "B": "Stratford"}
    kwargs.setdefault("bonus_interchanges", set())
    kwargs.setdefault("curse_deck", CurseDeck(_curses_path(tmp_path)))
    kwargs.setdefault("rng", random.Random(0))
    return new_game(teams, **kwargs)


def _buy_curse(game: Any, team: Any, index: int = 0):
    """Buy a curse and keep one of the options drawn (the first, unless told otherwise)."""
    options = game.buy_powerup(team.role_id, "curse")
    assert options is not None
    return game.choose_curse(team.role_id, options[index].id)


# --- costs and configuration -------------------------------------------------


def test_powerup_costs_defined():
    for pid, cost in EXPECTED_COSTS.items():
        assert config.POWERUP_COSTS[pid] == cost


def test_every_powerup_has_a_display_name():
    # The ids are internal (they travel in the event log); this is what players are shown.
    assert set(config.POWERUP_NAMES) == set(config.POWERUP_COSTS)
    assert config.POWERUP_NAMES["efficiency"] == "Good Service"


def test_command_names_follow_the_display_names():
    assert config.POWERUP_COMMANDS["efficiency"] == "good-service"
    for powerup, command in config.POWERUP_COMMANDS.items():
        assert re.fullmatch(r"[a-z0-9_-]{1,32}", command), f"{powerup} makes an invalid command: {command}"

    # jump, detour and curse are hand-written commands in main.py, named after their Python
    # function rather than from this table — so renaming one of those three renames the map
    # entry and leaves the command behind. Rename the function in main.py too, or they drift.
    for powerup in ("jump", "detour", "curse"):
        assert config.POWERUP_COMMANDS[powerup] == powerup, f"rename the {powerup} command in main.py"


def test_all_powerups_enabled_by_default(tmp_path: Path):
    game = _game(tmp_path)
    A = game.teams[0]
    assert game.enabled_powerups == set(config.POWERUP_COSTS)

    game.get_snake(A).coins = 10000
    for pid in config.POWERUP_COSTS:
        game.buy_powerup(A.role_id, pid)
    for pid in config.POWERUP_COSTS:
        assert pid in game.get_snake(A).hand


def test_disabled_powerups_cannot_be_bought(tmp_path: Path):
    game = _game(tmp_path, enabled_powerups={"jump"})
    A = game.teams[0]
    assert game.enabled_powerups == {"jump"}
    snake = game.get_snake(A)
    snake.coins = 50

    with pytest.raises(ValueError, match="enabled"):
        game.buy_powerup(A.role_id, "efficiency")
    game.buy_powerup(A.role_id, "jump")  # the enabled one still works
    assert snake.hand == ["jump"]


# --- buying ------------------------------------------------------------------


def test_buy_deducts_coins_and_adds_to_hand(tmp_path: Path):
    game = _game(tmp_path)
    A = game.teams[0]
    snake = game.get_snake(A)
    snake.coins = 10

    game.buy_powerup(A.role_id, "efficiency")

    assert snake.coins == 10 - config.POWERUP_COSTS["efficiency"]
    assert snake.hand == ["efficiency"]


def test_buy_requires_enough_coins(tmp_path: Path):
    game = _game(tmp_path)
    A = game.teams[0]
    snake = game.get_snake(A)
    snake.coins = config.POWERUP_COSTS["jump"] - 1

    with pytest.raises(ValueError, match="coins"):
        game.buy_powerup(A.role_id, "jump")
    assert snake.hand == []
    assert snake.coins == config.POWERUP_COSTS["jump"] - 1  # nothing deducted


def test_buy_unknown_powerup_raises(tmp_path: Path):
    game = _game(tmp_path)
    A = game.teams[0]
    game.get_snake(A).coins = 50
    with pytest.raises(ValueError):
        game.buy_powerup(A.role_id, "teleport")


def test_hand_is_unlimited_and_allows_duplicates(tmp_path: Path):
    game = _game(tmp_path)
    A = game.teams[0]
    snake = game.get_snake(A)
    snake.coins = 50
    for _ in range(3):
        game.buy_powerup(A.role_id, "retreat")
    assert snake.hand.count("retreat") == 3


def test_eliminated_teams_cannot_buy_or_play(tmp_path: Path):
    game = _game(tmp_path)
    A = game.teams[0]
    B = game.teams[1]
    snake = game.get_snake(A)
    snake.coins = 50
    game.buy_powerup(A.role_id, "efficiency")  # in hand before the crash

    game.crash(A)
    with pytest.raises(ValueError, match="crashed"):
        game.buy_powerup(A.role_id, "efficiency")
    with pytest.raises(ValueError, match="crashed"):
        game.play_normal_powerup(A.role_id, "efficiency")

    game.concede(B)
    game.get_snake(B).coins = 50
    with pytest.raises(ValueError, match="conceded"):
        game.buy_powerup(B.role_id, "efficiency")


# --- playing (generic) -------------------------------------------------------


def test_play_requires_powerup_in_hand(tmp_path: Path):
    game = _game(tmp_path)
    A = game.teams[0]
    game.get_snake(A).coins = 50
    with pytest.raises(ValueError, match="hand"):
        game.play_normal_powerup(A.role_id, "efficiency")


def test_play_consumes_one_copy_from_hand(tmp_path: Path):
    game = _game(tmp_path)
    A = game.teams[0]
    snake = game.get_snake(A)
    snake.coins = 50
    game.buy_powerup(A.role_id, "efficiency")
    game.buy_powerup(A.role_id, "efficiency")

    game.play_normal_powerup(A.role_id, "efficiency")
    assert snake.hand.count("efficiency") == 1
    game.play_normal_powerup(A.role_id, "efficiency")  # wasted (already armed) but still consumed
    assert snake.hand.count("efficiency") == 0
    assert snake.free_vetoes == 1


def test_failed_play_keeps_the_card(tmp_path: Path):
    game = _game(tmp_path)
    A = game.teams[0]
    snake = game.get_snake(A)
    snake.coins = 50
    game.buy_powerup(A.role_id, "jump")

    with pytest.raises(ValueError):
        game.play_jump(A.role_id, station="Narnia")
    assert snake.hand == ["jump"]  # invalid target must not consume the powerup


def test_playing_a_powerup_you_do_not_hold_raises(tmp_path: Path):
    # Each parameterised play event has its own hand check; an empty hand must stop all of them.
    game = _game(tmp_path)
    A, B = game.teams
    snake = game.get_snake(A)
    assert snake.hand == []

    with pytest.raises(ValueError, match="jump is not in"):
        game.play_jump(A.role_id, station="Bond Street")
    with pytest.raises(ValueError, match="detour is not in"):
        game.play_detour(A.role_id, line="Bakerloo")
    with pytest.raises(ValueError, match="curse is not in"):
        game.play_curse(A.role_id, target_team_id=B.role_id, curse_id="pub")

    assert game.jumped_stations == set()
    assert snake.pending_detour is None
    assert game.get_snake(B).curses == []


# --- jump --------------------------------------------------------------------


def test_jump_lets_a_neck_pass_through_a_claim_without_stealing_it(tmp_path: Path):
    game = _game(tmp_path, teams={"A": "Baker Street", "B": "Bond Street"})
    A = game.teams[0]
    B = game.teams[1]
    game.complete_challenge(A.role_id, "Jubilee")  # A claims Baker Street
    game.complete_challenge(B.role_id, "Jubilee")  # B claims Bond Street

    snake_a = game.get_snake(A)
    snake_a.coins = 20
    assert "Bond Street" not in game.jumped_stations
    game.buy_powerup(A.role_id, "jump")
    game.play_jump(A.role_id, station="Bond Street")
    assert "Bond Street" in game.jumped_stations

    # Path Baker -> Bond (B's, jumped) -> Green Park: legal AND survivable now.
    game.request_challenge(A.role_id, "Green Park")
    assert not snake_a.crashed
    assert game.neck(A) == ["Bond Street", "Green Park"]

    # Completing claims the rest of the neck but never steals B's station.
    game.complete_challenge(A.role_id, "Jubilee")
    assert game.map.get_claim("Bond Street") == B
    assert game.map.get_claim("Green Park") == A
    assert snake_a.anchor == "Green Park"
    assert not game.get_snake(B).crashed


def test_jump_benefits_all_players(tmp_path: Path):
    # Jump is global: a *different* team may also travel through (and front at)
    # the jumped station for the rest of the game.
    game = _game(tmp_path, teams={"A": "Baker Street", "B": "Bond Street", "C": "Westminster"})
    A = game.teams[0]
    B = game.teams[1]
    C = game.teams[2]
    for team in (A, B, C):
        game.complete_challenge(team.role_id, "Jubilee")

    game.get_snake(A).coins = 20
    game.buy_powerup(A.role_id, "jump")
    game.play_jump(A.role_id, station="Bond Street")

    # C's path Westminster -> Green Park -> Bond Street fronts AT the jumped claim.
    game.request_challenge(C.role_id, "Bond Street")
    assert not game.get_snake(C).crashed


def test_jump_protects_own_neck_preemptively(tmp_path: Path):
    game = _game(tmp_path, teams={"A": "Baker Street", "B": "Westminster"})
    A = game.teams[0]
    B = game.teams[1]
    game.complete_challenge(A.role_id, "Jubilee")
    game.complete_challenge(B.role_id, "Jubilee")

    game.request_challenge(A.role_id, "Green Park")  # A's neck = [Bond Street, Green Park]
    snake_a = game.get_snake(A)
    snake_a.coins = 20
    game.buy_powerup(A.role_id, "jump")
    game.play_jump(A.role_id, station="Green Park")  # pre-emptive protection

    # B claims Green Park out from under A's neck — normally a crash for A.
    game.request_challenge(B.role_id, "Green Park")
    game.complete_challenge(B.role_id, "Jubilee")
    assert game.map.get_claim("Green Park") == B
    assert not snake_a.crashed
    assert game.is_neck_safe(A)


def test_jump_allows_travel_through_own_claim(tmp_path: Path):
    game = _game(tmp_path, teams={"A": "Baker Street"})
    A = game.teams[0]
    game.complete_challenge(A.role_id, "Jubilee")
    game.map.claim("Bond Street", A)  # simulate an earlier body extension

    snake = game.get_snake(A)
    snake.coins = 20
    game.buy_powerup(A.role_id, "jump")
    game.play_jump(A.role_id, station="Bond Street")

    # Without the jump, requesting through your own claim crashes you.
    game.request_challenge(A.role_id, "Green Park")
    assert not snake.crashed
    assert game.neck(A) == ["Bond Street", "Green Park"]


def test_jumping_through_a_claim_does_not_steal_its_segments(tmp_path: Path):
    # Jumping grants passage, never ownership — and that has to hold for the track
    # between two interchanges as well as for the interchanges themselves. This is
    # the only way two teams can ever travel the same segment.
    game = _game(tmp_path, teams={"A": "Rayners Lane", "B": "Turnham Green"})
    A = game.teams[0]
    B = game.teams[1]
    for team in (A, B):
        game.complete_challenge(team.role_id, "Picc")

    game.request_challenge(A.role_id, "Acton Town")
    game.complete_challenge(A.role_id, "Picc")
    assert game.map.get_segment_claim("Picc", "Ealing Common", "Acton Town") == A

    # B jumps both of A's interchanges, then re-travels the track between them.
    game.get_snake(B).coins = 50
    for station in ("Acton Town", "Ealing Common"):
        game.buy_powerup(B.role_id, "jump")
        game.play_jump(B.role_id, station=station)
    game.request_challenge(B.role_id, "Ealing Common")
    assert not game.get_snake(B).crashed
    game.complete_challenge(B.role_id, "Picc")

    # The stretch running between two of A's interchanges is still A's...
    assert game.map.get_segment_claim("Picc", "Ealing Common", "Acton Town") == A
    assert game.map.get_claim("Ealing Common") == A
    assert game.map.get_claim("Acton Town") == A
    # ...but the one B opened up from its own station is B's.
    assert game.map.get_segment_claim("Picc", "Turnham Green", "Acton Town") == B
    # B fronted at a jumped interchange it does not own: Anchor without Body.
    assert game.get_snake(B).anchor == "Ealing Common"
    assert game.body_stations(B) == ["Turnham Green"]


# --- efficiency --------------------------------------------------------------


def test_efficiency_makes_the_next_veto_free(tmp_path: Path):
    game = _game(tmp_path)
    A = game.teams[0]
    snake = game.get_snake(A)
    snake.coins = 10
    game.buy_powerup(A.role_id, "efficiency")
    game.play_normal_powerup(A.role_id, "efficiency")
    assert snake.free_vetoes == 1

    assert game.veto_challenges(A.role_id) is True  # free — bot skips the veto period
    assert snake.free_vetoes == 0
    assert game.veto_challenges(A.role_id) is False  # back to a normal veto


def test_efficiency_does_not_stack(tmp_path: Path):
    # Playing a second copy while one is armed is wasted: consumed, no effect.
    game = _game(tmp_path)
    A = game.teams[0]
    snake = game.get_snake(A)
    snake.coins = 20
    for _ in range(2):
        game.buy_powerup(A.role_id, "efficiency")
        game.play_normal_powerup(A.role_id, "efficiency")
    assert snake.free_vetoes == 1
    assert snake.hand == []  # the wasted copy was still consumed

    assert game.veto_challenges(A.role_id) is True
    assert game.veto_challenges(A.role_id) is False  # only ONE veto was free


# --- retreat -----------------------------------------------------------------


def test_retreat_cancels_the_active_request(tmp_path: Path):
    game = _game(tmp_path, teams={"A": "Baker Street"})
    A = game.teams[0]
    game.complete_challenge(A.role_id, "Jubilee")
    game.request_challenge(A.role_id, "Bond Street")

    snake = game.get_snake(A)
    snake.coins = 20
    game.buy_powerup(A.role_id, "retreat")
    game.play_normal_powerup(A.role_id, "retreat")

    assert snake.neck_active is False
    assert snake.front == snake.anchor == "Baker Street"
    assert snake.offer is None
    assert game.neck(A) == []
    assert snake.blocked_station == "Bond Street"


def test_retreat_blocks_the_same_station_until_a_different_request(tmp_path: Path):
    game = _game(tmp_path, teams={"A": "Baker Street"})
    A = game.teams[0]
    game.complete_challenge(A.role_id, "Jubilee")
    game.request_challenge(A.role_id, "Bond Street")

    snake = game.get_snake(A)
    snake.coins = 20
    game.buy_powerup(A.role_id, "retreat")
    game.play_normal_powerup(A.role_id, "retreat")

    with pytest.raises(ValueError, match="different"):
        game.request_challenge(A.role_id, "Bond Street")

    game.request_challenge(A.role_id, "Green Park")  # a different station is fine
    assert snake.blocked_station is None
    assert game.neck(A) == ["Bond Street", "Green Park"]


def test_retreat_requires_an_active_normal_request(tmp_path: Path):
    game = _game(tmp_path)
    A = game.teams[0]
    snake = game.get_snake(A)
    snake.coins = 20
    game.buy_powerup(A.role_id, "retreat")

    # Start leaves the initial challenge active, and that cannot be retreated.
    with pytest.raises(ValueError, match="initial"):
        game.play_normal_powerup(A.role_id, "retreat")
    assert "retreat" in snake.hand  # failed play keeps the card

    # Completing it leaves no active neck at all.
    game.complete_challenge(A.role_id, "Jubilee")
    with pytest.raises(ValueError, match="active"):
        game.play_normal_powerup(A.role_id, "retreat")
    assert "retreat" in snake.hand


# --- detour ------------------------------------------------------------------


def test_detour_switches_the_line_travelled_but_not_the_one_announced(tmp_path: Path):
    game = _game(tmp_path, teams={"A": "Baker Street"})
    A = game.teams[0]
    game.complete_challenge(A.role_id, "Jubilee")  # declared Jubilee

    snake = game.get_snake(A)
    snake.coins = 20
    game.buy_powerup(A.role_id, "detour")
    game.play_detour(A.role_id, line="Bakerloo")  # Baker Street is on Bakerloo
    assert snake.travel_line == "Bakerloo"
    assert snake.announced_line == "Jubilee", "Detour is not announced"

    game.request_challenge(A.role_id, "Oxford Circus")  # travels the NEW line
    assert game.neck(A) == ["Oxford Circus"]


def test_detour_validates_the_new_line(tmp_path: Path):
    game = _game(tmp_path, teams={"A": "Baker Street"})
    A = game.teams[0]
    game.complete_challenge(A.role_id, "Jubilee")
    snake = game.get_snake(A)
    snake.coins = 50
    game.buy_powerup(A.role_id, "detour")

    with pytest.raises(ValueError):
        game.play_detour(A.role_id, line="Victoria")  # not at Baker Street
    with pytest.raises(ValueError):
        game.play_detour(A.role_id, line="Hogwarts Express")  # unknown
    assert "detour" in snake.hand
    assert snake.travel_line == "Jubilee"  # untouched by the failed plays


def test_detour_requires_a_declared_line(tmp_path: Path):
    game = _game(tmp_path, teams={"A": "Baker Street", "B": "Stratford"})
    B = game.teams[1]
    game.get_snake(B).coins = 20
    game.buy_powerup(B.role_id, "detour")
    with pytest.raises(ValueError, match="declared"):
        game.play_detour(B.role_id, line="Jubilee")  # pre-initial: no line yet


def test_detour_mid_challenge_overrides_the_next_declared_line(tmp_path: Path):
    # Powerups are playable "at any time while not on transport", which includes
    # mid-challenge. The team boards next at the Front, so the detour is parked
    # until this challenge completes, then silently replaces what was declared.
    game = _game(tmp_path, teams={"A": "Baker Street"})
    A = game.teams[0]
    game.complete_challenge(A.role_id, "Jubilee")
    game.request_challenge(A.role_id, "Bond Street")  # neck now active

    snake = game.get_snake(A)
    snake.coins = 20
    game.buy_powerup(A.role_id, "detour")
    game.play_detour(A.role_id, line="Central")  # Bond Street is on Central
    assert snake.pending_detour == "Central"
    assert snake.travel_line == "Jubilee"  # the current neck is untouched
    assert game.neck(A) == ["Bond Street"]

    game.complete_challenge(A.role_id, "Jubilee")  # declares Jubilee publicly...
    assert snake.travel_line == "Central"  # ...but actually boards Central
    assert snake.announced_line == "Jubilee", "the announcement stands — that is the secret"
    assert snake.pending_detour is None

    game.request_challenge(A.role_id, "Oxford Circus")  # only reachable on Central
    assert game.neck(A) == ["Oxford Circus"]


def test_detour_mid_challenge_validates_against_the_front(tmp_path: Path):
    game = _game(tmp_path, teams={"A": "Baker Street"})
    A = game.teams[0]
    game.complete_challenge(A.role_id, "Jubilee")
    game.request_challenge(A.role_id, "Bond Street")

    snake = game.get_snake(A)
    snake.coins = 20
    game.buy_powerup(A.role_id, "detour")

    # Bakerloo serves the Anchor (Baker Street) but not the Front (Bond Street),
    # and the Front is where this team boards next.
    with pytest.raises(ValueError, match="Bond Street"):
        game.play_detour(A.role_id, line="Bakerloo")
    assert snake.pending_detour is None
    assert "detour" in snake.hand


def test_retreat_lapses_a_parked_detour(tmp_path: Path):
    game = _game(tmp_path, teams={"A": "Baker Street"})
    A = game.teams[0]
    game.complete_challenge(A.role_id, "Jubilee")
    game.request_challenge(A.role_id, "Bond Street")

    snake = game.get_snake(A)
    snake.coins = 50
    game.buy_powerup(A.role_id, "detour")
    game.play_detour(A.role_id, line="Central")
    game.buy_powerup(A.role_id, "retreat")
    game.play_normal_powerup(A.role_id, "retreat")

    # The detour was aimed at boarding from Bond Street, which A never reaches.
    assert snake.pending_detour is None
    game.request_challenge(A.role_id, "Green Park")
    game.complete_challenge(A.role_id, "Jubilee")
    assert snake.travel_line == "Jubilee"
    assert snake.announced_line == "Jubilee"  # nothing secret survived the retreat


# --- curse -------------------------------------------------------------------


def test_curse_deck_loads_from_json(tmp_path: Path):
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


def test_curse_deck_draws_without_replacement_and_resets_when_empty(tmp_path: Path):
    deck = CurseDeck(_curses_path(tmp_path))
    rng = random.Random(2)

    drawn_ids = {deck.draw(rng=rng).id for _ in range(3)}
    assert drawn_ids == {"get_a_melon", "egg_partner", "pub"}  # each curse exactly once per cycle
    assert deck.all() == []

    # An emptied deck resets to full: the next draw succeeds from a fresh cycle.
    fourth = deck.draw(rng=rng)
    assert fourth.id in {"get_a_melon", "egg_partner", "pub"}
    assert len(deck.all()) == 2  # refilled to 3, then one drawn


def test_buying_a_curse_draws_two_to_choose_between(tmp_path: Path):
    game = _game(tmp_path)
    A = game.teams[0]
    B = game.teams[1]
    snake = game.get_snake(A)
    snake.coins = 20

    options = game.buy_powerup(A.role_id, "curse")

    assert options is not None
    assert len(options) == config.CURSE_OPTIONS
    assert len({c.id for c in options}) == len(options)  # never the same curse twice
    assert snake.curse_choice == options  # nothing is held until the team keeps one
    assert snake.held_curses == []
    assert snake.hand == ["curse"]
    assert game.get_snake(B).curses == []


def test_keeping_one_curse_puts_the_other_back(tmp_path: Path):
    game = _game(tmp_path)
    A = game.teams[0]
    assert game.curse_deck is not None
    snake = game.get_snake(A)
    snake.coins = 20

    options = game.buy_powerup(A.role_id, "curse")
    assert options is not None
    assert len(game.curse_deck.all()) == 1  # both drawn out of a three-curse deck

    kept = game.choose_curse(A.role_id, options[1].id)

    assert kept == options[1]
    assert snake.held_curses == [kept]
    assert snake.curse_choice == []
    assert options[0] in game.curse_deck.all()  # the one not kept is back in the deck
    assert kept not in game.curse_deck.all()


def test_a_second_curse_cannot_be_bought_until_one_is_kept(tmp_path: Path):
    game = _game(tmp_path)
    A = game.teams[0]
    snake = game.get_snake(A)
    snake.coins = 20
    before = snake.coins

    game.buy_powerup(A.role_id, "curse")
    with pytest.raises(ValueError, match="keep one"):
        game.buy_powerup(A.role_id, "curse")

    assert snake.coins == before - config.POWERUP_COSTS["curse"]  # the rejected buy cost nothing


def test_keeping_a_curse_that_was_not_offered_raises(tmp_path: Path):
    game = _game(tmp_path)
    A = game.teams[0]
    assert game.curse_deck is not None
    game.get_snake(A).coins = 20

    options = game.buy_powerup(A.role_id, "curse")
    assert options is not None
    not_offered = game.curse_deck.all()[0]  # the one left in the deck

    with pytest.raises(ValueError, match="not offered"):
        game.choose_curse(A.role_id, not_offered.id)
    assert game.get_snake(A).held_curses == []
    assert game.get_snake(A).curse_choice == options  # still waiting to be decided


def test_drawing_options_never_offers_the_same_curse_twice(tmp_path: Path):
    # The cycle empties part-way through the second draw, so the refill must exclude
    # what that draw already took.
    deck = CurseDeck(_curses_path(tmp_path))
    rng = random.Random(3)

    deck.draw_options(2, rng=rng)
    options = deck.draw_options(2, rng=rng)

    assert len({c.id for c in options}) == 2


def test_curse_leaves_the_deck_at_buy_time_not_play_time(tmp_path: Path):
    game = _game(tmp_path)
    A = game.teams[0]
    B = game.teams[1]
    assert game.curse_deck is not None
    game.get_snake(A).coins = 20

    options = game.buy_powerup(A.role_id, "curse")
    assert options is not None
    curse = game.choose_curse(A.role_id, options[0].id)

    # Already gone from the deck, even though it has not been played yet.
    assert curse not in game.curse_deck.all()
    assert len(game.curse_deck.all()) == 2  # three, less the one kept

    game.play_curse(A.role_id, target_team_id=B.role_id, curse_id=curse.id)
    assert len(game.curse_deck.all()) == 2  # playing a held curse never touches the deck


def test_a_rejected_buy_does_not_consume_a_curse_from_the_deck(tmp_path: Path):
    game = _game(tmp_path)
    A = game.teams[0]
    assert game.curse_deck is not None
    snake = game.get_snake(A)
    snake.coins = config.POWERUP_COSTS["curse"] - 1

    with pytest.raises(ValueError, match="coins"):
        game.buy_powerup(A.role_id, "curse")

    assert len(game.curse_deck.all()) == 3  # the draw happens only after every check passes
    assert snake.held_curses == []
    assert snake.hand == []


def test_curses_stay_available_after_the_deck_cycles(tmp_path: Path):
    game = _game(tmp_path)
    A = game.teams[0]
    B = game.teams[1]
    snake = game.get_snake(A)
    snake.coins = 50

    kept = []
    for _ in range(4):  # more buys than the deck has curses
        options = game.buy_powerup(A.role_id, "curse")
        assert options is not None
        kept.append(game.choose_curse(A.role_id, options[0].id))

    assert snake.held_curses == kept
    played = [game.play_curse(A.role_id, target_team_id=B.role_id, curse_id=c.id) for c in kept]

    assert played == kept
    assert game.get_snake(B).curses == kept
    assert snake.hand == []
    assert snake.held_curses == []
    game.buy_powerup(A.role_id, "curse")  # still purchasable — the deck never runs dry


def test_playing_a_held_curse_attaches_it_to_the_target(tmp_path: Path):
    game = _game(tmp_path)
    A = game.teams[0]
    B = game.teams[1]
    game.get_snake(A).coins = 20
    c = _buy_curse(game, A)  # two are drawn at buy time; this is the one kept

    curse = game.play_curse(A.role_id, target_team_id=B.role_id, curse_id=c.id)

    assert isinstance(curse, Curse)
    assert curse.id in {"get_a_melon", "egg_partner", "pub"}
    assert game.get_snake(B).curses == [curse]
    assert "curse" not in game.get_snake(A).hand


def test_playing_a_chosen_curse_by_id_leaves_the_others_held(tmp_path: Path):
    game = _game(tmp_path)
    A = game.teams[0]
    B = game.teams[1]
    snake = game.get_snake(A)
    snake.coins = 50
    first = _buy_curse(game, A)
    second = _buy_curse(game, A)
    assert first.id != second.id  # a kept curse never goes back, so a later buy can't repeat it

    played = game.play_curse(A.role_id, target_team_id=B.role_id, curse_id=second.id)

    assert played == second  # the chosen one, not the oldest held
    assert game.get_snake(B).curses == [second]
    assert snake.held_curses == [first]  # the unplayed curse is still held
    assert snake.hand == ["curse"]  # and so is its card


def test_playing_a_curse_id_you_do_not_hold_raises(tmp_path: Path):
    game = _game(tmp_path)
    A = game.teams[0]
    B = game.teams[1]
    snake = game.get_snake(A)
    snake.coins = 50
    held = _buy_curse(game, A)

    with pytest.raises(ValueError, match="does not hold"):
        game.play_curse(A.role_id, target_team_id=B.role_id, curse_id="nonexistent")

    assert snake.hand == ["curse"]  # a failed play keeps the card...
    assert snake.held_curses == [held]  # ...and the curse it was holding
    assert game.get_snake(B).curses == []


def test_curse_requires_a_valid_living_opponent(tmp_path: Path):
    game = _game(tmp_path)
    A = game.teams[0]
    B = game.teams[1]
    snake = game.get_snake(A)
    snake.coins = 50
    c = _buy_curse(game, A)

    with pytest.raises(ValueError, match="another"):
        game.play_curse(A.role_id, target_team_id=A.role_id, curse_id=c.id)  # not yourself
    with pytest.raises(ValueError):
        game.play_curse(A.role_id, target_team_id=-1, curse_id=c.id)  # unknown team
    game.crash(B)
    with pytest.raises(ValueError):
        game.play_curse(A.role_id, target_team_id=B.role_id, curse_id=c.id)  # eliminated target
    assert "curse" in snake.hand  # none of the failed plays consumed it


def test_an_empty_curse_deck_disables_the_curse_powerup(tmp_path: Path):
    empty = tmp_path / "empty_curses.json"
    empty.write_text('{"curses": []}')
    game = _game(
        tmp_path,
        curse_deck=CurseDeck(str(empty)),
        enabled_powerups={"curse", "jump"},  # explicit enabling doesn't resurrect it
    )
    A = game.teams[0]
    assert game.curse_deck is None
    assert game.enabled_powerups == {"jump"}

    game.get_snake(A).coins = 50
    with pytest.raises(ValueError):
        game.buy_powerup(A.role_id, "curse")


def test_curse_draw_is_reproducible_with_a_seeded_rng(tmp_path: Path):
    def drawn_curse(seed: int) -> str:
        game = _game(tmp_path, rng=random.Random(seed))
        A = game.teams[0]
        B = game.teams[1]
        game.get_snake(A).coins = 20
        c = _buy_curse(game, A)
        return game.play_curse(A.role_id, target_team_id=B.role_id, curse_id=c.id).id

    assert drawn_curse(9) == drawn_curse(9)
