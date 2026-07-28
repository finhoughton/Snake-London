import random

from challenges import get_difficulty, neck_weights
from config import EASIER_REWARD, HARDER_REWARD
from game import new_game
from render import render_map, svg_to_png

# Five-team game exercising all six powerups — colours assigned automatically
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

# The initial challenge claims the Origin and unlocks the first line. It pays no
# coins, so everyone is still on STARTING_COINS after this.

game.initial_request_challenge("Alpha")
game.complete_challenge("Alpha", "Jubilee")

game.initial_request_challenge("Beta")
game.complete_challenge("Beta", "Elizabeth")

game.initial_request_challenge("Gamma")
game.complete_challenge("Gamma", "Bank Branch")

game.initial_request_challenge("Delta")
game.complete_challenge("Delta", "Met")

game.initial_request_challenge("Epsilon")
game.complete_challenge("Epsilon", "Central")

# Alpha — opens with Double up, so the next two challenges pay 6 instead of 3.

game.buy_powerup("Alpha", "double_up")
game.play_powerup("Alpha", "double_up")

game.request_challenge("Alpha", "Bond Street")
game.complete_challenge("Alpha", "Jubilee", hard=True)

game.request_challenge("Alpha", "Westminster")
game.complete_challenge("Alpha", "S Circle", hard=True)

# Beta — plays Detour *during* a challenge. It is validated against the Front
# (Charing Cross, where Beta boards next), parks on Snake.pending_detour, and
# then silently overrides the line declared on completion: Beta announces the
# CX Branch and actually boards the Bakerloo.

game.request_challenge("Beta", "Tottenham Court Road")
game.complete_challenge("Beta", "CX Branch", hard=True)

game.request_challenge("Beta", "Charing Cross")
game.buy_powerup("Beta", "detour")
game.play_powerup("Beta", "detour", line="Bakerloo")
game.complete_challenge("Beta", "CX Branch", hard=True)

# Oxford Circus is not on the CX Branch at all — only the detour makes this legal.
game.request_challenge("Beta", "Oxford Circus")

# Gamma — builds the southern arc, then buys Efficiency so its veto is free.

game.request_challenge("Gamma", "Elephant and Castle")
game.complete_challenge("Gamma", "Thameslink", hard=True)

game.request_challenge("Gamma", "Blackfriars")
game.complete_challenge("Gamma", "Thameslink", hard=True)

game.request_challenge("Gamma", "London Bridge")
game.complete_challenge("Gamma", "Thameslink", hard=True)

game.buy_powerup("Gamma", "efficiency")
game.play_powerup("Gamma", "efficiency")

game.request_challenge("Gamma", "Woolwich Arsenal")
gamma_veto_was_free = game.veto_challenges("Gamma")  # True -> no 15-minute wait

# Delta — buys a Curse (drawn at buy time) and
# plays it on Alpha before heading down the Bakerloo.

game.request_challenge("Delta", "Kenton")
game.complete_challenge("Delta", "Bakerloo", hard=True)

delta_curse = game.buy_powerup("Delta", "curse")
game.play_powerup("Delta", "curse", target_team="Alpha")

game.buy_powerup("Delta", "curse")  # a second one, kept in hand for later
game.request_challenge("Delta", "Paddington")

# Epsilon — requests a challenge, thinks better of it and Retreats, which blocks
# only its *next* request. It then walks into Alpha's Green Park and crashes.

game.request_challenge("Epsilon", "Notting Hill Gate")
game.complete_challenge("Epsilon", "S Circle", hard=True)

game.request_challenge("Epsilon", "Gloucester Road")
game.buy_powerup("Epsilon", "retreat")
game.play_powerup("Epsilon", "retreat")
epsilon_blocked = game.get_snake("Epsilon").blocked_station  # cleared by the next request

game.request_challenge("Epsilon", "South Kensington")  # a different interchange: allowed
game.complete_challenge("Epsilon", "Picc")

game.request_challenge("Epsilon", "Piccadilly Circus")  # path via Alpha's Green Park → crash

# Later — Alpha spends its Double up winnings on a Jump. Blackfriars is
# Gamma's, so the S Circle run Westminster → Embankment → Blackfriars → Bank
# would normally crash Alpha; jumping it makes the interchange passable for
# everyone, permanently, without taking it off Gamma.

game.buy_powerup("Alpha", "jump")
game.play_powerup("Alpha", "jump", station="Blackfriars")

game.request_challenge("Alpha", "Bank")
game.complete_challenge("Alpha", "Central", hard=True)

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
    print(f"  {team:8} {snake.coins:2} coins   in hand: {', '.join(snake.hand) or '-'}")
    print(f"           curses held: {held}   inflicted on it: {inflicted}")
print()
print("  Jumped (passable for everyone, forever):", sorted(game.jumped_stations))
print("  Blackfriars is still owned by:", game.map.get_claim("Blackfriars"))
print(f"  Gamma's veto was free (Efficiency): {gamma_veto_was_free}")
print(f"  Delta drew and played: {delta_curse.name if delta_curse else '-'}")
beta = game.get_snake("Beta")
print(f"  Beta announced {beta.announced_line!r} but is really on {beta.travel_line!r}")
print(f"  Epsilon retreated from {epsilon_blocked!r}, blocking only its next request")

# challenges currently on offer (teams mid-challenge)

print()
print("Challenges offered:")
for team in game.snakes:
    offer = game.current_challenges(team)
    if offer is None:
        continue
    easier, harder = offer
    snake = game.get_snake(team)
    target = get_difficulty(neck_weights(game.map, snake.travel_line or "", game.neck(team)))
    print(f"  {team} @ {snake.front}  (target difficulty {target:.2f}):")
    print(f"    easier ({EASIER_REWARD} coin,  diff {easier.difficulty}): {easier.name} — {easier.description}")
    print(f"    harder ({HARDER_REWARD} coins, diff {harder.difficulty}): {harder.name} — {harder.description}")

# render

render_map(game, "current_map.svg")
svg_to_png("current_map.svg", "current_map.png")
print("Map rendered to current_map.png")
