from datetime import date
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class RaceType(str, Enum):
    marathon = "marathon"               # 42.195 km
    half_marathon = "half_marathon"     # 21.0975 km
    ten_k = "10k"
    five_k = "5k"
    ultra = "ultra"                     # distance varies


RACE_DISTANCES_KM: dict[RaceType, float] = {
    RaceType.marathon: 42.195,
    RaceType.half_marathon: 21.0975,
    RaceType.ten_k: 10.0,
    RaceType.five_k: 5.0,
}


class Location(BaseModel):
    city: str
    country: str
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    # Altitude above sea level — used to adapt pace targets between
    # high-altitude training (e.g. Bengaluru ~900m) and sea-level races (e.g. Mumbai ~10m)
    altitude_m: float = Field(default=0, ge=0, le=9000)


class RaceGoal(BaseModel):
    race_name: str                                  # e.g. "Mumbai Marathon 2026"
    race_type: RaceType
    race_date: date
    race_location: Location
    training_location: Location

    # None means the goal is just to finish
    target_finish_seconds: Optional[int] = Field(default=None, gt=0)

    # Total elevation gain on the race course — different from city altitude
    course_elevation_gain_m: Optional[float] = Field(default=None, ge=0)

    @property
    def race_distance_km(self) -> Optional[float]:
        return RACE_DISTANCES_KM.get(self.race_type)

    @property
    def is_complete(self) -> bool:
        return date.today() > self.race_date

    @property
    def altitude_drop_m(self) -> float:
        """Positive means racing lower than training — generally improves pace."""
        return self.training_location.altitude_m - self.race_location.altitude_m
