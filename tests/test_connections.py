import json
from collections import deque

import pytest
from map import Map

# Fixtures


@pytest.fixture(scope="session")
def tube_map() -> Map:
    return Map("connections.json")


# Test helpers


def _line_adjacency(tube_map: Map, line_key: str) -> dict[str, set[str]]:
    """Build adjacency dict for one line from station-level neighbour data."""
    sl = list(tube_map.get_line(line_key).stations)
    adj: dict[str, set[str]] = {s: set() for s in sl}
    for station_key in sl:
        if not tube_map.has_station(station_key):
            continue
        station = tube_map.get_station(station_key)
        for nb in station.neighbours(line_key):
            if nb in adj:
                adj[station_key].add(nb)
    return adj


def _find_cycle_on_line(tube_map: Map, line_key: str) -> list[str] | None:
    """Return one cycle path [A, B, C, A], or None if cycle-free."""
    sl = list(tube_map.get_line(line_key).stations)
    if not sl:
        return None

    adj = _line_adjacency(tube_map, line_key)
    visited: set[str] = set()
    path: list[str] = []
    cycle: list[list[str] | None] = [None]

    def dfs(node: str, parent: str | None) -> None:
        if cycle[0]:
            return
        visited.add(node)
        path.append(node)
        for nb in adj[node]:
            if nb not in visited:
                dfs(nb, node)
            elif nb != parent and cycle[0] is None:
                idx = path.index(nb)
                cycle[0] = path[idx:] + [nb]
        if not cycle[0]:
            path.pop()

    dfs(sl[0], None)
    return cycle[0]


def _unreachable_on_line(tube_map: Map, line_key: str) -> list[str]:
    sl = list(tube_map.get_line(line_key).stations)
    if not sl:
        return []

    adj = _line_adjacency(tube_map, line_key)
    visited: set[str] = set()

    def dfs(node: str) -> None:
        visited.add(node)
        for nb in adj[node]:
            if nb not in visited:
                dfs(nb)

    dfs(sl[0])
    return [s for s in sl if s not in visited]


def _isolated_stations(tube_map: Map) -> tuple[str | None, list[str]]:
    station_keys = tube_map.station_keys()
    if not station_keys:
        return None, []

    full_adj: dict[str, set[str]] = {s: set() for s in station_keys}
    for station in tube_map.iter_stations():
        for line_key in station.line_keys():
            for nb in station.neighbours(line_key):
                if nb in full_adj:
                    full_adj[station.key].add(nb)

    first = station_keys[0]
    visited = {first}
    q: deque[str] = deque([first])
    while q:
        node = q.popleft()
        for nb in full_adj[node]:
            if nb not in visited:
                visited.add(nb)
                q.append(nb)

    isolated = [s for s in station_keys if s not in visited]
    return first, isolated


# Tests


def test_no_empty_neighbour_lists(tube_map: Map):
    """No station should have an empty adjacency list for any line."""
    empties = [
        f"{station.key!r} -> {line_key!r}"
        for station in tube_map.iter_stations()
        for line_key in station.line_keys()
        if len(station.neighbours(line_key)) == 0
    ]
    assert not empties, "Empty neighbour lists found:\n" + "\n".join(empties)


def test_line_stations_have_line_entry(tube_map: Map):
    """Every station listed on a line must have that line in its station entry."""
    missing = []
    for line in tube_map.iter_lines():
        for station_key in line.stations:
            if not tube_map.has_station(station_key):
                missing.append(f"Line {line.key!r}: {station_key!r} has no stations entry")
            elif not tube_map.get_station(station_key).has_line(line.key):
                missing.append(f"Line {line.key!r}: {station_key!r} has no {line.key!r} adjacency")
    assert not missing, "\n".join(missing)


def test_neighbours_are_on_their_line(tube_map: Map):
    """A station's neighbours on a line must all appear in that line's stations list."""
    errors = []
    for station in tube_map.iter_stations():
        for line_key in station.line_keys():
            if not tube_map.has_line(line_key):
                continue  # caught by test_no_unknown_lines
            line_station_set = set(tube_map.get_line(line_key).stations)
            for neighbour in station.neighbours(line_key):
                if neighbour not in line_station_set:
                    errors.append(
                        f"{station.key!r} lists {neighbour!r} as {line_key!r} neighbour "
                        f"but {neighbour!r} is not in that line's stations"
                    )
    assert not errors, "\n".join(errors)


def test_no_dangling_neighbour_references(tube_map: Map):
    """Every neighbour reference must exist as a key in 'stations'."""
    errors = []
    for station in tube_map.iter_stations():
        for line_key in station.line_keys():
            for neighbour in station.neighbours(line_key):
                if not tube_map.has_station(neighbour):
                    errors.append(
                        f"{station.key!r} -> {neighbour!r} on {line_key!r}: {neighbour!r} has no stations entry"
                    )
    assert not errors, "\n".join(errors)


def test_neighbour_symmetry(tube_map: Map):
    """If A lists B as a neighbour on line L, B must list A back."""
    errors = []
    for station in tube_map.iter_stations():
        for line_key in station.line_keys():
            for neighbour in station.neighbours(line_key):
                if not tube_map.has_station(neighbour):
                    continue  # caught by test_no_dangling_neighbour_references
                back = tube_map.get_station(neighbour).neighbours(line_key)
                if back is None:
                    errors.append(
                        f"{station.key!r} -> {neighbour!r} on {line_key!r} but {neighbour!r} has no {line_key!r} entry"
                    )
                elif station.key not in back:
                    errors.append(
                        f"{station.key!r} -> {neighbour!r} on {line_key!r} "
                        f"but {neighbour!r} does not list {station.key!r} back"
                    )
    assert not errors, "\n".join(errors)


def test_no_duplicate_stations_in_lines(tube_map: Map):
    """Each line's stations list should contain no duplicates."""
    errors = []
    for line in tube_map.iter_lines():
        seen: set[str] = set()
        for station_key in line.stations:
            if station_key in seen:
                errors.append(f"Line {line.key!r} lists {station_key!r} more than once")
            seen.add(station_key)
    assert not errors, "\n".join(errors)


def test_no_unknown_lines(tube_map: Map):
    """Every line key referenced in stations must exist in 'lines'."""
    errors = [
        f"{station.key!r} references unknown line {line_key!r}"
        for station in tube_map.iter_stations()
        for line_key in station.line_keys()
        if not tube_map.has_line(line_key)
    ]
    assert not errors, "\n".join(errors)


@pytest.mark.parametrize(
    "line_key",
    [k for k, v in json.load(open("connections.json"))["lines"].items() if v["stations"]],
)
def test_line_is_a_tree(line_key: str, tube_map: Map):
    """Each line's adjacency graph must be a tree: connected and cycle-free."""
    cycle = _find_cycle_on_line(tube_map, line_key)
    if cycle:
        cycle_str = " -> ".join(cycle)
        assert False, f"Line {line_key!r} contains a cycle: {cycle_str}"

    unreachable = _unreachable_on_line(tube_map, line_key)
    first_on_line = tube_map.get_line(line_key).stations[0]
    assert not unreachable, (
        f"Line {line_key!r} is not fully connected; unreachable from {first_on_line!r}: {unreachable}"
    )


def test_network_is_connected(tube_map: Map):
    """Every station must be reachable from every other station."""
    first, isolated = _isolated_stations(tube_map)
    assert not isolated, f"Stations not reachable from {first!r}: {isolated}"


def test_claim_line_claims_path_excluding_start():
    tube_map = Map("connections.json")

    claimed = tube_map.claim_line("Kenton", "Oxford Circus", "red", "Bakerloo")

    assert claimed == [
        "Wilsden Junction",
        "Queen's Park",
        "Paddington",
        "Edgware Road",
        "Baker Street",
        "Oxford Circus",
    ]
    assert tube_map.get_claim("Kenton") is None
    assert tube_map.get_claim("Oxford Circus") == "red"


def test_claim_line_requires_line_parameter():
    tube_map = Map("connections.json")

    with pytest.raises(ValueError):
        tube_map.claim_line("Euston", "King's Cross", "blue", "Bakerloo")  # type: ignore[misc]


def test_claim_line_accepts_explicit_line():
    tube_map = Map("connections.json")

    claimed = tube_map.claim_line("Euston", "King's Cross", "blue", line="Met")

    assert claimed == ["King's Cross"]
    assert tube_map.get_claim("King's Cross") == "blue"
