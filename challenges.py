import json
import math
import random
from collections.abc import Callable, Collection
from dataclasses import dataclass

from network import Map


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

    # How far (in difficulty points) a band may stretch to avoid showing a team a
    # challenge it has already seen, before a repeat is allowed instead.
    _MAX_WIDEN = 3

    def _unseen(self, in_band: Callable[[float, int], bool], exclude: Collection[str]) -> list[Challenge] | None:
        """Challenges in a band that the team hasn't been shown yet, or None to allow a repeat.

        ``in_band(difficulty, widen)`` describes the band stretched by ``widen`` points.
        It only stretches when exclusion is what emptied it: a band with nothing in it
        at all is a gap in the pool, and is left to the caller's usual fallback.
        """
        if not any(in_band(c.difficulty, 0) for c in self._challenges):
            return None
        for widen in range(self._MAX_WIDEN + 1):
            fresh = [c for c in self._challenges if in_band(c.difficulty, widen) and c.id not in exclude]
            if fresh:
                return fresh
        return None

    def pair_for(
        self,
        target_difficulty: float,
        rng: random.Random | None = None,
        exclude: Collection[str] = frozenset(),
    ) -> tuple[Challenge, Challenge]:
        """Return an (easier, harder) pair of challenges near the given difficulty.

        The easier challenge is picked randomly from challenges with difficulty in
        [target - 1.5, target]; the harder from (target, target + 1.5]. Pass ``rng``
        (a ``random.Random``) for reproducible draws.

        ``exclude`` holds the ids of challenges this team has already been shown, which
        are skipped: if that empties a band, the band stretches away from the target
        (by up to ``_MAX_WIDEN`` points) before a repeat is allowed, so an offer is
        always made. With nothing excluded the draw is identical to what it was before
        ``exclude`` existed.
        """
        BAND = 1.5
        picker = rng if rng is not None else random
        t = target_difficulty

        below = self._unseen(lambda d, widen: t - BAND - widen <= d <= t, exclude)
        above = self._unseen(lambda d, widen: t < d <= t + BAND + widen, exclude)
        if below is None:
            below = [c for c in self._challenges if t - BAND <= c.difficulty <= t]
        if above is None:
            above = [c for c in self._challenges if t < c.difficulty <= t + BAND]

        if not below:
            below = [min(self._challenges, key=lambda c: abs(c.difficulty - target_difficulty))]
        if not above:
            above = [max(self._challenges, key=lambda c: c.difficulty if c.difficulty > target_difficulty else -1)]
            if above[0].difficulty <= target_difficulty:
                above = [self._challenges[-1]]

        easier = picker.choice(below)
        harder = picker.choice(above)
        return easier, harder

    def pick_in_range(
        self,
        low: float,
        high: float,
        rng: random.Random | None = None,
        exclude: Collection[str] = frozenset(),
    ) -> Challenge:
        """Return a single challenge chosen uniformly at random from difficulty [low, high].

        Falls back to the challenge closest to the range's midpoint if none qualify.
        Used for the initial challenge, which has no neck to size a target difficulty
        from (see ``get_difficulty``) — every team gets this same single challenge
        instead of an (easier, harder) pair. ``exclude`` skips challenges the team has
        already been shown, stretching the range before repeating, as in ``pair_for``.
        """
        picker = rng if rng is not None else random
        candidates = self._unseen(lambda d, widen: low - widen <= d <= high + widen, exclude)
        if candidates is None:
            candidates = [c for c in self._challenges if low <= c.difficulty <= high]
        if not candidates:
            mid = (low + high) / 2
            candidates = [min(self._challenges, key=lambda c: abs(c.difficulty - mid))]
        return picker.choice(candidates)


# The raw score in get_difficulty ranks routes; this curve maps it onto the challenge
# pool. Calibrated on the 23 Aug 2026 playtest: weighted to that game's mix of hop
# lengths, 5% of plausible routes score <= 3, half <= 6 (the pool's median) and 95%
# <= 9, and nothing reaches 9.7 — so the harder band (target, target + 1.5] still
# reaches the hardest challenges. Before this, real routes scored 2.1-6.3 and about a
# quarter of the pool could never be offered.
_DIFFICULTY_OFFSET = -2.6
_DIFFICULTY_SCALE = 12.3
_DIFFICULTY_RATE = 40.0


def get_difficulty(weights: list[int]) -> float:
    """Target difficulty for a neck, from the weights (importance) of its interchanges.

    Increasing in neck length n, heaviest station m, total weight s and average weight
    a; never negative, and below _DIFFICULTY_OFFSET + _DIFFICULTY_SCALE (~9.7).
    """
    n = len(weights)
    if n == 0:
        return 0.0
    m = max(weights) - 2
    s = sum(weights) - 1.5 * n
    a = s / n
    raw = 7 * n + 3 * m + 3 * s + a + 8 * (n == 1)
    return max(0.0, _DIFFICULTY_OFFSET + _DIFFICULTY_SCALE * (1 - math.exp(-raw / _DIFFICULTY_RATE)))


def neck_weights(game_map: Map, neck: list[str]) -> list[int]:
    return [game_map.get_station(s).weight for s in neck]
