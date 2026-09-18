import random
from pathlib import Path

from challenges import get_difficulty, neck_weights
from config import EASIER_REWARD, HARDER_REWARD, OBJECTIVE_COINS, OBJECTIVE_STATIONS
from new_game import new_game
from objectives import team_costs
from render import render_map, svg_to_png

# Five-team game exercising all five powerups — colours assigned automatically
# from DEFAULT_TEAM_COLORS. Seeded so the bonus interchanges, the challenge
# offers and the curse draw are the same on every run.
#
# Alpha:   Jubilee, Wembley Park → Westminster, then JUMPS through Gamma to Bank
# Beta:    Elizabeth → Tottenham Court Road, then DETOURS off its declared line
# Gamma:   Bank Branch + Thameslink south arc, with a free (EFFICIENCY) veto
# Delta:   Met + Bakerloo out to Paddington, and CURSES Alpha on the way
# Epsilon: Central then S Circle + Picc; RETREATS once, then crashes into Alpha

game = new_game(
    start_positions={
        "Alpha": "Wembley Park",
        "Beta": "Abbey Wood",
        "Gamma": "Tooting Broadway",
        "Delta": "Rayners Lane",
        "Epsilon": "Ealing Broadway",
    },
    rng=random.Random(7),
)

Alpha, Beta, Gamma, Delta, Epsilon = game.teams

# new_game arms every team's initial challenge. Completing it claims the Origin and
# unlocks the first line; it pays no coins, so everyone is still on STARTING_COINS.

game.complete_challenge(Alpha.role_id, "Jubilee")
game.complete_challenge(Beta.role_id, "Elizabeth")
game.complete_challenge(Gamma.role_id, "Bank Branch")
game.complete_challenge(Delta.role_id, "Met")
game.complete_challenge(Epsilon.role_id, "Central")

# Alpha — heads down the Jubilee, taking the harder challenge each time.

game.request_challenge(Alpha.role_id, "Bond Street")
game.complete_challenge(Alpha.role_id, "Jubilee", hard=True)

game.request_challenge(Alpha.role_id, "Westminster")
game.complete_challenge(Alpha.role_id, "S Circle", hard=True)

# Beta — plays Detour *during* a challenge. It is validated against the Front
# (Charing Cross, where Beta boards next), parks on Snake.pending_detour, and
# then silently overrides the line declared on completion: Beta announces the
# CX Branch and actually boards the Bakerloo.

game.request_challenge(Beta.role_id, "Tottenham Court Road")
game.complete_challenge(Beta.role_id, "CX Branch", hard=True)

game.request_challenge(Beta.role_id, "Charing Cross")
game.buy_powerup(Beta.role_id, "detour")
game.play_detour(Beta.role_id, line="Bakerloo")
game.complete_challenge(Beta.role_id, "CX Branch", hard=True)

# Oxford Circus is not on the CX Branch at all — only the detour makes this legal.
game.request_challenge(Beta.role_id, "Oxford Circus")

# Gamma — builds the southern arc, then buys Efficiency so its veto is free.

game.request_challenge(Gamma.role_id, "Elephant and Castle")
game.complete_challenge(Gamma.role_id, "Thameslink", hard=True)

game.request_challenge(Gamma.role_id, "Blackfriars")
game.complete_challenge(Gamma.role_id, "Thameslink", hard=True)

game.request_challenge(Gamma.role_id, "London Bridge")
game.complete_challenge(Gamma.role_id, "Thameslink", hard=True)

game.buy_powerup(Gamma.role_id, "efficiency")
game.play_normal_powerup(Gamma.role_id, "efficiency")

game.request_challenge(Gamma.role_id, "Woolwich Arsenal")
gamma_veto_was_free = game.veto_challenges(Gamma.role_id)  # True -> no 15-minute wait

# Delta — buys a Curse (two are drawn at buy time, it keeps one) and
# plays it on Alpha before heading down the Bakerloo.

game.request_challenge(Delta.role_id, "Kenton")
game.complete_challenge(Delta.role_id, "Bakerloo", hard=True)

delta_options = game.buy_powerup(Delta.role_id, "curse")
assert delta_options
delta_curse = game.choose_curse(Delta.role_id, delta_options[0].id)  # the other goes back in the deck
game.play_curse(Delta.role_id, target_team_id=Alpha.role_id, curse_id=delta_curse.id)

second_options = game.buy_powerup(Delta.role_id, "curse")  # a second one, kept in hand for later
assert second_options
game.choose_curse(Delta.role_id, second_options[-1].id)
game.request_challenge(Delta.role_id, "Paddington")

# Epsilon — requests a challenge, thinks better of it and Retreats, which blocks
# only its *next* request. It then walks into Alpha's Green Park and crashes.

game.request_challenge(Epsilon.role_id, "Notting Hill Gate")
game.complete_challenge(Epsilon.role_id, "S Circle", hard=True)

game.request_challenge(Epsilon.role_id, "Gloucester Road")
game.buy_powerup(Epsilon.role_id, "retreat")
game.play_normal_powerup(Epsilon.role_id, "retreat")
epsilon_blocked = game.get_snake(Epsilon).blocked_station  # cleared by the next request

game.request_challenge(Epsilon.role_id, "South Kensington")  # a different interchange: allowed
game.complete_challenge(Epsilon.role_id, "Picc")

game.request_challenge(Epsilon.role_id, "Piccadilly Circus")  # path via Alpha's Green Park → crash

# Later — Alpha spends its winnings on a Jump. Blackfriars is
# Gamma's, so the S Circle run Westminster → Embankment → Blackfriars → Bank
# would normally crash Alpha; jumping it makes the interchange passable for
# everyone, permanently, without taking it off Gamma.

game.buy_powerup(Alpha.role_id, "jump")
game.play_jump(Alpha.role_id, station="Blackfriars")

game.request_challenge(Alpha.role_id, "Bank")
game.complete_challenge(Alpha.role_id, "Central", hard=True)

# A contested objective goes up, at an interchange every living team needs the same
# number of legs to reach. Delta is walled in, so its route costs it a Jump.

objective = game.new_objective()
assert objective

# state summary

for team in game.snakes:
    snake = game.get_snake(team)
    print(f"{team} body:", game.body_stations(team))
    print(f"{team} neck:", game.neck(team) if not snake.crashed else f"{game.neck(team)} (crashed)")
    print()

print("Powerups:")
for team, snake in game.snakes.items():
    held = ", ".join(f"{c.name}" for c in snake.held_curses) or "-"
    inflicted = ", ".join(f"{c.name}" for c in snake.curses) or "-"
    print(f"  {team.name:8} {snake.coins:2} coins   in hand: {', '.join(snake.hand) or '-'}")
    print(f"           curses held: {held}   inflicted on it: {inflicted}")
print()
print("  Jumped (passable for everyone, forever):", sorted(game.jumped_stations))
print("  Blackfriars is still owned by:", game.map.get_claim("Blackfriars"))
print(f"  Gamma's veto was free (Efficiency): {gamma_veto_was_free}")
print(f"  Delta drew and played: {delta_curse.name if delta_curse else '-'}")
beta = game.get_snake(Beta)
print(f"  Beta announced {beta.announced_line!r} but is really on {beta.travel_line!r}")
print(f"  Epsilon retreated from {epsilon_blocked!r}, blocking only its next request")
print(f"  Objective ({OBJECTIVE_COINS} coins, worth {OBJECTIVE_STATIONS} stations): {objective}")
for team, costs in team_costs(game).items():
    _, legs, jumps, stops = costs[objective]
    print(f"    {team.name:8} {legs} legs, {stops} stops" + (f", {jumps} Jump" if jumps else ""))

# challenges currently on offer (teams mid-challenge)

print()
print("Challenges offered:")
for team in game.snakes:
    offer = game.current_challenges(team)
    if offer is None:
        continue
    easier, harder = offer
    snake = game.get_snake(team)
    target = get_difficulty(neck_weights(game.map, game.neck(team)))
    print(f"  {team} @ {snake.front}  (target difficulty {target:.2f}):")
    print(f"    easier ({EASIER_REWARD} coin,  diff {easier.difficulty}): {easier.name} — {easier.description}")
    print(f"    harder ({HARDER_REWARD} coins, diff {harder.difficulty}): {harder.name} — {harder.description}")

# render

out = Path("out")
out.mkdir(exist_ok=True)
render_map(game, out / "current_map.svg")
svg_to_png(out / "current_map.svg", out / "current_map.png")
print(f"Map rendered to {out / 'current_map.png'}")
