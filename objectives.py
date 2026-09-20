"""Contested objectives: choosing where the next one goes.

Every team still in the game should need the same number of legs (rides on one line)
to reach it, and similar numbers of stops. Passing an un-jumped claim needs a Jump.
Central stations are preferred. No randomness, so a restart replays the same choices.
"""

import heapq
from collections import deque
from functools import lru_cache
from statistics import mean
from typing import TYPE_CHECKING

from network import Map

if TYPE_CHECKING:
    from game import GameState, Snake
    from jloxgame.state import Team

JUMP_LEGS = 2.0  # a Jump is only taken if it saves more than this many legs
JUMP_PENALTY = 1.0
STOP_WEIGHT = 0.25
CENTRAL_WEIGHT = 1.5
IDEAL_LEGS = (2, 4)  # ~17 minutes per leg in the playtest

# (legs with each Jump counted as JUMP_LEGS more, legs, Jumps, stops)
Cost = tuple[float, int, int, int]
_UNREACHED: Cost = (float("inf"), 0, 0, 0)


@lru_cache(maxsize=4)
def centrality(game_map: Map) -> dict[str, float]:
    """1.0 for the station with the fewest average stops to everywhere, 0.0 for the most."""

    def average_stops(start: str) -> float:
        dist, queue = {start: 0}, deque([start])
        while queue:
            here = queue.popleft()
            station = game_map.get_station(here)
            for line in station.line_keys():
                for there in station.neighbours(line):
                    if there not in dist:
                        dist[there] = dist[here] + 1
                        queue.append(there)
        return mean(dist.values())

    averages = {s: average_stops(s) for s in game_map.station_keys()}
    lo, hi = min(averages.values()), max(averages.values())
    span = (hi - lo) or 1.0
    return {s: 1 - (a - lo) / span for s, a in averages.items()}


def route_costs(game_map: Map, start: str, first_line: str | None, blocked: set[str]) -> dict[str, Cost]:
    """The cheapest route from ``start`` to every station, riding ``first_line`` first if given."""
    best: dict[str, Cost] = {start: (0.0, 0, 0, 0)}
    queue: list[tuple[float, int, int, int, str, bool]] = [(0.0, 0, 0, 0, start, True)]
    while queue:
        cost, legs, jumps, stops, here, at_start = heapq.heappop(queue)
        if (cost, legs, jumps, stops) > best.get(here, _UNREACHED):
            continue
        lines = [first_line] if at_start and first_line else game_map.get_station(here).line_keys()
        for line in lines:
            ride, seen = deque([(here, 0, 0)]), {here}
            while ride:
                stop, rode, jumped = ride.popleft()
                for nxt in game_map.get_station(stop).neighbours(line):
                    if nxt in seen:
                        continue
                    seen.add(nxt)
                    nxt_jumped = jumped + (nxt in blocked)
                    ride.append((nxt, rode + 1, nxt_jumped))
                    total_jumps = jumps + nxt_jumped
                    candidate = (legs + 1 + JUMP_LEGS * total_jumps, legs + 1, total_jumps, stops + rode + 1)
                    if candidate < best.get(nxt, _UNREACHED):
                        best[nxt] = candidate
                        heapq.heappush(queue, (*candidate, nxt, False))
    return best


def _blocked(game: GameState) -> set[str]:
    """Un-jumped claims, plus live necks."""
    blocked = {s for s in game.map.all_claims() if s not in game.jumped_stations}
    for team in game.active_teams():
        if game.get_snake(team).neck_active:
            blocked.update(game.neck(team))
    return blocked


def _position(snake: Snake) -> str:
    return snake.front if snake.neck_active else snake.anchor


def _neighbours(game_map: Map, station: str) -> set[str]:
    here = game_map.get_station(station)
    return {n for line in here.line_keys() for n in here.neighbours(line)}


def team_costs(game: GameState) -> dict[Team, dict[str, Cost]]:
    """Each active team's cheapest route to every station, from its Front if mid-challenge,
    else from its Anchor on its declared line."""
    blocked = _blocked(game)
    costs: dict[Team, dict[str, Cost]] = {}
    for team in game.active_teams():
        snake = game.get_snake(team)
        position = _position(snake)
        first_line = None if snake.neck_active else snake.travel_line
        costs[team] = route_costs(game.map, position, first_line, blocked - {position})
    return costs


def objective_score(costs: list[Cost], central: float) -> float:
    """Lower is better."""
    legs = [c[1] for c in costs]
    stops = [c[3] for c in costs]
    average = mean(legs)
    lo, hi = IDEAL_LEGS
    return (
        (max(legs) - min(legs))
        + STOP_WEIGHT * (max(stops) - min(stops))
        + max(0.0, lo - average)
        + max(0.0, average - hi)
        + CENTRAL_WEIGHT * (1 - central)
        + JUMP_PENALTY * sum(c[2] for c in costs)
    )


def choose_objective(game: GameState) -> str | None:
    """The next objective, or None. Never a claimed station (a jumped claim can't be won),
    a live neck, a team's position, or a live objective or its neighbour."""
    if len(game.active_teams()) < 2:
        return None
    costs = list(team_costs(game).values())
    positions = {_position(game.get_snake(t)) for t in game.active_teams()}
    live = set(game.objectives)
    next_to_live = {n for s in live for n in _neighbours(game.map, s)}
    unavailable = set(game.map.all_claims()) | _blocked(game) | positions | live | next_to_live
    central = centrality(game.map)
    candidates = [s for s in game.map.station_keys() if s not in unavailable and all(s in c for c in costs)]
    if not candidates:
        return None
    return min(candidates, key=lambda s: objective_score([c[s] for c in costs], central[s]))
