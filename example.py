from game import new_game
from render import render_map, svg_to_png

# --- Set up a two-team game ---

game = new_game(
    start_positions={"red": "Baker Street", "blue": "Canary Wharf"},
    team_colors={"red": "#e63946", "blue": "#457b9d"},
)

# --- Red team moves ---

# At game start each team requests an initial challenge (no travel yet)
game.initial_request_challenge("red")
# Once the challenge is completed, declare a line to travel on
game.complete_challenge("red", "Jubilee")

# Travel to a new station and request a challenge there
game.request_challenge("red", "Westminster")
game.complete_challenge("red", "District")

# Travel further — neck is now active (challenge in progress)
game.request_challenge("red", "Tower Hill")

# --- Blue team moves ---

game.initial_request_challenge("blue")
game.complete_challenge("blue", "Elizabeth")
game.request_challenge("blue", "Farringdon")
game.complete_challenge("blue", "Thameslink")
game.request_challenge("blue", "Finsbury Park")

# --- Inspect state ---

print("Red body stations: ", game.body_stations("red"))
print("Red neck (in progress):", game.neck("red"))
print("Blue body stations: ", game.body_stations("blue"))
print("Winner so far:", game.winner())

# --- Render the current map ---

render_map(game, "current_map.svg")
svg_to_png("current_map.svg", "current_map.png")
print("Map rendered to current_map.png")
