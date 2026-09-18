import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


def scheduled(game, event_type: str) -> list:
    """The game's pending scheduled events of one type (the bot's timers)."""
    return [e for e in game.scheduled_events if e.__type__ == event_type]


def minutes_away(game, event) -> float:
    """How far in the future a scheduled event is, in game minutes."""
    return (event.__time__ - game.game_time_now()) / 60_000
