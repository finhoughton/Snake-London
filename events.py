from dataclasses import dataclass
from typing import Any, Self

from game import GameState
from jloxgame.events import GameEvent, register_event
from jloxgame.state import Status


@register_event
@dataclass
class Request(GameEvent[GameState]):
    team_id: int
    station: str

    @staticmethod
    def event_type() -> str:
        return "request_challenge"

    def to_dict(self) -> dict[str, Any]:
        return {"team_id": self.team_id, "station": self.station}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return cls(data["team_id"], data["station"])

    def update(self, gctx: GameState) -> None:
        team = gctx.get_team(self.team_id)
        assert gctx.status == Status.RUNNING
        assert gctx.map.has_station(self.station)
        gctx.request_challenge(team, self.station)


@register_event
@dataclass
class Complete(GameEvent[GameState]):
    team_id: int
    next_line: str
    hard: bool

    @staticmethod
    def event_type() -> str:
        return "complete"

    def to_dict(self) -> dict[str, Any]:
        return {"team_id": self.team_id, "next_line": self.next_line, "hard": self.hard}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return cls(data["team_id"], data["next_line"], data["hard"])

    def update(self, gctx: GameState) -> None:
        team = gctx.get_team(self.team_id)
        assert gctx.status == Status.RUNNING
        gctx.complete_challenge(team, self.next_line, hard=self.hard)


@register_event
@dataclass
class Veto(GameEvent[GameState]):
    team_id: int

    @staticmethod
    def event_type() -> str:
        return "veto"

    def to_dict(self) -> dict[str, Any]:
        return {"team_id": self.team_id}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return cls(data["team_id"])

    def update(self, gctx: GameState) -> None:
        team = gctx.get_team(self.team_id)
        assert gctx.status == Status.RUNNING
        if not gctx.veto_challenges(team):  # veto period
            gctx.get_snake(team).vetoed = True


@register_event
@dataclass
class Unveto(GameEvent[GameState]):
    team_id: int

    @staticmethod
    def event_type() -> str:
        return "unveto"

    def to_dict(self) -> dict[str, Any]:
        return {"team_id": self.team_id}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return cls(data["team_id"])

    def update(self, gctx: GameState) -> None:
        team = gctx.get_team(self.team_id)
        assert gctx.status == Status.RUNNING
        gctx.get_snake(team).vetoed = False


@register_event
@dataclass
class BuyPowerup(GameEvent[GameState]):
    team_id: int
    powerup: str

    @staticmethod
    def event_type() -> str:
        return "buy_powerup"

    def to_dict(self) -> dict[str, Any]:
        return {"team_id": self.team_id, "powerup": self.powerup}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return cls(data["team_id"], data["powerup"])

    def update(self, gctx: GameState) -> None:
        team = gctx.get_team(self.team_id)
        assert gctx.status == Status.RUNNING
        gctx.buy_powerup(team, self.powerup)


@register_event
@dataclass
class PlayPowerup(GameEvent[GameState]):
    team_id: int
    powerup: str
    target_station: str | None = None
    target_line: str | None = None
    target_team_id: int | None = None
    curse: str | None = None

    @staticmethod
    def event_type() -> str:
        return "play_powerup"

    def to_dict(self) -> dict[str, Any]:
        return {
            "team_id": self.team_id,
            "powerup": self.powerup,
            "target_station": self.target_station,
            "target_line": self.target_line,
            "target_team_id": self.target_team_id,
            "curse": self.curse,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return cls(
            data["team_id"],
            data["powerup"],
            target_station=data["target_station"],
            target_line=data["target_line"],
            target_team_id=data["target_team_id"],
            curse=data["curse"],
        )

    def update(self, gctx: GameState) -> None:
        team = gctx.get_team(self.team_id)
        assert gctx.status == Status.RUNNING

        match self.powerup:
            case "detour":
                gctx.play_powerup(team, self.powerup, line=self.target_line)
            case "jump":
                gctx.play_powerup(team, self.powerup, station=self.target_station)
            case "curse":
                target_team = gctx.get_team(self.target_team_id) if self.target_team_id else None
                gctx.play_powerup(team, self.powerup, target_team=target_team, curse_id=self.curse)
            case _:
                gctx.play_powerup(team, self.powerup)
