from game import new_game
from render import render_map, svg_to_png

# Three-team game — colours assigned automatically from DEFAULT_TEAM_COLORS.
#
# Alpha (deep pink):   Jubilee line, Wembley Park → Green Park (NW arc)
# Beta  (ocean teal):  Elizabeth line, Abbey Wood → Tottenham Court Road (east → centre)
# Gamma (burnt sienna):Bank Branch + Thameslink, Tooting Broadway → Greenwich (south arc)

game = new_game(
    start_positions={
        "Alpha": "Wembley Park",
        "Beta": "Abbey Wood",
        "Gamma": "Tooting Broadway",
    }
)

game.initial_request_challenge("Alpha")
game.complete_challenge("Alpha", "Jubilee")

game.initial_request_challenge("Beta")
game.complete_challenge("Beta", "Elizabeth")

game.initial_request_challenge("Gamma")
game.complete_challenge("Gamma", "Bank Branch")

# team alpha

game.request_challenge("Alpha", "Bond Street")
game.complete_challenge("Alpha", "Jubilee")

game.request_challenge("Alpha", "Westminster")

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

# render

render_map(game, "current_map.svg")
svg_to_png("current_map.svg", "current_map.png")
print("Map rendered to current_map.png")
