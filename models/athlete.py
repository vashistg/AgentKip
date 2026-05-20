from datetime import date
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from models.goal import RaceGoal
from models.workout import Workout


class FitnessLevel(str, Enum):
    beginner = "beginner"
    intermediate = "intermediate"
    advanced = "advanced"


class Athlete(BaseModel):
    id: str
    name: str
    date_of_birth: Optional[date] = None

    # Physiological baselines used for HR zone calculations in analyze node
    resting_heart_rate: Optional[int] = Field(default=None, ge=30, le=100)
    max_heart_rate: Optional[int] = Field(default=None, ge=100, le=220)

    fitness_level: FitnessLevel
    weekly_mileage_target_km: float = Field(gt=0, le=300)
    goal: RaceGoal

    # Checked by assess node before allowing training to proceed
    injury_flags: list[str] = Field(default_factory=list)

    # Rolling window of recent workouts — populated by fetch_data node
    recent_workouts: list[Workout] = Field(default_factory=list)

    # External account IDs for Strava/Garmin tools
    strava_athlete_id: Optional[str] = None
    garmin_user_id: Optional[str] = None
