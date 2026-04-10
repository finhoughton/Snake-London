from __future__ import annotations

import json
import random
import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Challenge:
    id: str
    name: str
    description: str
    difficulty: float


class ChallengePool:
    def __init__(self, path: str = "challenges.json"):
        with open(path) as f:
            data = json.load(f)
        self._challenges: list[Challenge] = [Challenge(**entry) for entry in data["challenges"]]
        self._challenges.sort(key=lambda c: c.difficulty)

    def all(self) -> list[Challenge]:
        return list(self._challenges)

    def get(self, challenge_id: str) -> Challenge:
        for c in self._challenges:
            if c.id == challenge_id:
                return c
        raise KeyError(f"Unknown challenge: {challenge_id!r}")

    def pair_for(self, target_difficulty: float) -> tuple[Challenge, Challenge]:
        """Return an (easier, harder) pair of challenges near the given difficulty.

        The easier challenge is picked randomly from challenges with difficulty in [target - 1.5, target]. Similarly for harder
        """
        BAND = 1.5

        below = [c for c in self._challenges if target_difficulty - BAND <= c.difficulty <= target_difficulty]
        above = [c for c in self._challenges if target_difficulty < c.difficulty <= target_difficulty + BAND]

        if not below:
            below = [min(self._challenges, key=lambda c: abs(c.difficulty - target_difficulty))]
        if not above:
            above = [max(self._challenges, key=lambda c: c.difficulty if c.difficulty > target_difficulty else -1)]
            if above[0].difficulty <= target_difficulty:
                above = [self._challenges[-1]]

        easier = random.choice(below)
        harder = random.choice(above)
        return easier, harder


def get_difficulty(weights: list[int]) -> float:
    # made up, but is increasing in n, m, s, a and is in [0, 10), and gives reasonable values
    n = len(weights)
    if n == 0:
        return 0.0
    m = max(weights) - 2
    s = sum(weights) - 1.5 * n
    a = s / n
    raw = 7 * n + 3 * m + 3 * s + a + 8 * (n == 1)
    return 10.0 * (1 - math.exp(-raw / 100))


def neck_weights(game_map, line: str, neck: list[str]) -> list[int]:
    return [game_map.get_station(s).weight for s in neck]
