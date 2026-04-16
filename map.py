from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True)
class Line:
    key: str
    display_name: str
    has_branches: bool
    stations: tuple[str, ...]

    @classmethod
    def from_dict(cls, key: str, data: dict) -> Line:
        return cls(
            key=key,
            display_name=data["display_name"],
            has_branches=data.get("has_branches", False),
            stations=tuple(data["stations"]),
        )

    def contains_station(self, station_key: str) -> bool:
        return station_key in self.stations


@dataclass(frozen=True)
class Station:
    key: str
    display_name: str
    weight: int
    _adjacency: dict[str, list[str]]

    @classmethod
    def from_dict(cls, key: str, data: dict) -> Station:
        adjacency = {
            line_key: neighbours for line_key, neighbours in data.items() if line_key not in ("display_name", "weight")
        }
        if "weight" in data:
            weight = data["weight"]
        else:
            weight = len({n for neighbours in adjacency.values() for n in neighbours})
        return cls(
            key=key,
            display_name=data["display_name"],
            weight=weight,
            _adjacency=adjacency,
        )

    def line_keys(self) -> list[str]:
        return list(self._adjacency.keys())

    def has_line(self, line_key: str) -> bool:
        return line_key in self._adjacency

    def neighbours(self, line_key: str) -> list[str]:
        return self._adjacency.get(line_key, [])


class Map:
    def __init__(self, path: str = "map/connections.json"):
        with open(path, "r") as f:
            data = json.load(f)
        self._lines: dict[str, Line] = {key: Line.from_dict(key, line_data) for key, line_data in data["lines"].items()}
        self._stations: dict[str, Station] = {
            key: Station.from_dict(key, station_data) for key, station_data in data["stations"].items()
        }
        self._claims: dict[str, str] = {}  # station_key -> team
        self._claimed_segments: dict[tuple[str, str, str], str] = {}  # (line, a, b) -> team

    # claims:

    def claim(self, station_key: str, team: str) -> None:
        """Claim a station for a team. Raises ValueError if already claimed by another team."""
        current = self._claims.get(station_key)
        if current is not None and current != team:
            raise ValueError(f"{station_key!r} is already claimed by {current!r}")
        self._claims[station_key] = team

    def claim_line(
        self,
        start_station_key: str,
        end_station_key: str,
        team: str,
        line: str,
    ) -> list[str]:
        """
        Claim the stations on the path between two stations on the same line.

        The start station is excluded. The end station is included.
        """
        if not self.has_station(start_station_key):
            raise ValueError(f"Unknown station: {start_station_key!r}")
        if not self.has_station(end_station_key):
            raise ValueError(f"Unknown station: {end_station_key!r}")

        if not self.has_line(line):
            raise ValueError(f"Unknown line: {line!r}")
        if not self.get_station(start_station_key).has_line(line):
            raise ValueError(f"{start_station_key!r} is not on line {line!r}")
        if not self.get_station(end_station_key).has_line(line):
            raise ValueError(f"{end_station_key!r} is not on line {line!r}")

        path = self._path_between_on_line(
            line,
            start_station_key,
            end_station_key,
        )
        segment = path[1:]

        for station_key in segment:
            current = self._claims.get(station_key)
            if current is not None:
                raise ValueError(f"{station_key!r} is already claimed by {current!r}")
            self._claims[station_key] = team

        return segment

    def unclaim(self, station_key: str) -> None:
        self._claims.pop(station_key, None)

    def get_claim(self, station_key: str) -> str | None:
        """Return the team that has claimed a station, or None."""
        return self._claims.get(station_key)

    def is_claimed(self, station_key: str) -> bool:
        return station_key in self._claims

    def stations_claimed_by(self, team: str) -> list[str]:
        return [s for s, t in self._claims.items() if t == team]

    def all_claims(self) -> dict[str, str]:
        return dict(self._claims)

    def claim_segment(self, line_key: str, station_a: str, station_b: str, team: str) -> None:
        key = (line_key, *sorted([station_a, station_b]))
        self._claimed_segments[key] = team

    def segments_claimed_by(self, team: str) -> list[tuple[str, str, str]]:
        return [k for k, v in self._claimed_segments.items() if v == team]

    # data getters:

    def line_keys(self) -> list[str]:
        return list(self._lines.keys())

    def station_keys(self) -> list[str]:
        return list(self._stations.keys())

    def get_line(self, key: str) -> Line:
        return self._lines[key]

    def get_station(self, key: str) -> Station:
        return self._stations[key]

    def has_line(self, key: str) -> bool:
        return key in self._lines

    def has_station(self, key: str) -> bool:
        return key in self._stations

    def iter_lines(self) -> list[Line]:
        return list(self._lines.values())

    def iter_stations(self) -> list[Station]:
        return list(self._stations.values())

    def _path_between_on_line(
        self,
        line_key: str,
        start_station_key: str,
        end_station_key: str,
    ) -> list[str]:
        if start_station_key == end_station_key:
            return [start_station_key]

        queue: deque[str] = deque([start_station_key])
        previous: dict[str, str | None] = {start_station_key: None}

        while queue:
            station_key = queue.popleft()
            for neighbour in self.get_station(station_key).neighbours(line_key):
                if neighbour in previous:
                    continue
                previous[neighbour] = station_key
                if neighbour == end_station_key:
                    queue.clear()
                    break
                queue.append(neighbour)

        if end_station_key not in previous:
            raise ValueError(f"No path found from {start_station_key!r} to {end_station_key!r} on {line_key!r}")

        path: list[str] = []
        current: str | None = end_station_key
        while current is not None:
            path.append(current)
            current = previous[current]
        path.reverse()
        return path
