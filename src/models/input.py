from __future__ import annotations

from datetime import UTC, datetime
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..geo.geometry import geodesic_length_nm

DEFAULT_CORRIDOR_NM = 1.0
MIN_CORRIDOR_NM, MAX_CORRIDOR_NM = 0.1, 10.0
MAX_ROUTE_POINTS = 1000
MAX_ROUTE_NM = 2500.0
MIN_ROUTE_NM = 0.01


class Position(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


class ActorInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    route: list[Position]
    corridor_nm: float = Field(default=DEFAULT_CORRIDOR_NM, alias="corridorNm", ge=MIN_CORRIDOR_NM, le=MAX_CORRIDOR_NM)
    at_time: datetime | None = Field(default=None, alias="atTime")

    @field_validator("at_time")
    @classmethod
    def _utc(cls, v: datetime | None) -> datetime | None:
        if v is not None and v.tzinfo is None:
            raise ValueError("atTime must include a timezone, e.g. 2026-10-09T15:00:00Z")
        return v.astimezone(UTC) if v else None

    @model_validator(mode="after")
    def _route(self) -> Self:
        if not 2 <= len(self.route) <= MAX_ROUTE_POINTS:
            raise ValueError(f"route must have between 2 and {MAX_ROUTE_POINTS} points")
        if any(abs(a.lon - b.lon) > 180 for a, b in zip(self.route, self.route[1:], strict=False)):
            raise ValueError("route segments crossing the antimeridian are not supported")
        length = geodesic_length_nm([(p.lon, p.lat) for p in self.route])
        if length < MIN_ROUTE_NM:
            raise ValueError("route has zero length")
        if length > MAX_ROUTE_NM:
            raise ValueError(f"route is longer than {MAX_ROUTE_NM:g} nautical miles")
        return self

    @property
    def lonlat(self) -> list[tuple[float, float]]:
        return [(p.lon, p.lat) for p in self.route]
