from challenges import get_difficulty, neck_weights
from config import EASIER_REWARD, HARDER_REWARD
from game import new_game
from render import render_map, svg_to_png

# Five-team game — colours assigned automatically from DEFAULT_TEAM_COLORS.
#
# Alpha:   Jubilee line, Wembley Park → Westminster via Green Park (NW arc)
# Beta:    Elizabeth line, Abbey Wood → Tottenham Court Road (east → centre)
# Gamma:   Bank Branch + Thameslink, Tooting Broadway → Greenwich (south arc)
# Delta:   Met + Bakerloo, Rayners Lane → Paddington (NW)
# Epsilon: Central then S Circle + Picc, Ealing Broadway east — crashes into Alpha's Green Park (greyed out)

game = new_game(
    start_positions={
        "Alpha": "Wembley Park",
        "Beta": "Abbey Wood",
        "Gamma": "Tooting Broadway",
        "Delta": "Rayners Lane",
        "Epsilon": "Ealing Broadway",
    }
)

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

# team alpha

game.request_challenge("Alpha", "Bond Street")
game.complete_challenge("Alpha", "Jubilee")

game.request_challenge("Alpha", "Westminster")
game.complete_challenge("Alpha", "S Circle")

# beta

game.request_challenge("Beta", "Tottenham Court Road")
game.complete_challenge("Beta", "CX Branch")

game.request_challenge("Beta", "Charing Cross")

# Gamma

game.request_challenge("Gamma", "Elephant and Castle")
game.complete_challenge("Gamma", "Thameslink")

game.request_challenge("Gamma", "Blackfriars")
game.complete_challenge("Gamma", "Thameslink")

game.request_challenge("Gamma", "London Bridge")
game.complete_challenge("Gamma", "Thameslink")

game.request_challenge("Gamma", "Woolwich Arsenal")

# Delta

game.request_challenge("Delta", "Kenton")
game.complete_challenge("Delta", "Bakerloo")

game.request_challenge("Delta", "Paddington")

# Epsilon — builds a body east then south, then requests a challenge whose neck
# runs through Alpha's claimed Green Park: a legal move that crashes it.

game.request_challenge("Epsilon", "Notting Hill Gate")
game.complete_challenge("Epsilon", "S Circle")

game.request_challenge("Epsilon", "South Kensington")
game.complete_challenge("Epsilon", "Picc")

game.request_challenge("Epsilon", "Piccadilly Circus")  # path via Alpha's Green Park → crash

# state summary

print("Alpha body:", game.body_stations("Alpha"))
print("Alpha neck:", game.neck("Alpha"))
print()
print("Beta  body:", game.body_stations("Beta"))
print("Beta  neck:", game.neck("Beta"))
print()
print("Gamma body:", game.body_stations("Gamma"))
print("Gamma neck:", game.neck("Gamma"))
print()
print("Delta body:", game.body_stations("Delta"))
print("Delta neck:", game.neck("Delta"))
print()
print("Epsilon body:", game.body_stations("Epsilon"))
print("Epsilon crashed:", game.get_snake("Epsilon").crashed)

# challenges currently on offer (teams mid-challenge)

print()
print("Challenges offered:")
for team in game.snakes:
    offer = game.current_challenges(team)
    if offer is None:
        continue
    easier, harder = offer
    snake = game.get_snake(team)
    target = get_difficulty(neck_weights(game.map, snake.declared_line or "", game.neck(team)))
    print(f"  {team} @ {snake.front}  (target difficulty {target:.2f}):")
    print(f"    easier ({EASIER_REWARD} coin,  diff {easier.difficulty}): {easier.name} — {easier.description}")
    print(f"    harder ({HARDER_REWARD} coins, diff {harder.difficulty}): {harder.name} — {harder.description}")

# render

render_map(game, "current_map.svg")
svg_to_png("current_map.svg", "current_map.png")
print("Map rendered to current_map.png")
