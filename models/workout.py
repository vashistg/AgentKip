from datetime import date
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class ActivityType(str, Enum):
    easy = "easy"
    tempo = "tempo"
    long_run = "long_run"
    interval = "interval"
    race = "race"
    recovery = "recovery"
    cross_training = "cross_training"
    strength = "strength"


class DataSource(str, Enum):
    strava = "strava"
    garmin = "garmin"
    manual = "manual"


class Workout(BaseModel):
    id: str
    date: date
    activity_type: ActivityType
    source: DataSource

    distance_km: Optional[float] = Field(default=None, gt=0, le=200)
    duration_seconds: int = Field(gt=0, le=86400)

    avg_heart_rate: Optional[int] = Field(default=None, ge=30, le=220)
    max_heart_rate: Optional[int] = Field(default=None, ge=30, le=220)
    elevation_gain_m: Optional[float] = Field(default=None, ge=0, le=9000)

    # Training Stress Score — normalized measure of workout load.
    # Higher = more physiological stress on the athlete.
    training_stress_score: Optional[float] = Field(default=None, ge=0)

    @property
    def pace_min_per_km(self) -> Optional[float]:
        if self.distance_km is None:
            return None
        return (self.duration_seconds / 60) / self.distance_km
