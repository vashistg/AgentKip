from datetime import date, datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from models.weather import WeatherCondition
from models.workout import ActivityType


class PlannedActivityType(str, Enum):
    # Running sessions
    easy = ActivityType.easy
    tempo = ActivityType.tempo
    long_run = ActivityType.long_run
    interval = ActivityType.interval
    recovery = ActivityType.recovery
    # Non-running
    strength = ActivityType.strength
    rest = "rest"


class TrainingLoadTrend(str, Enum):
    overtraining = "overtraining"
    on_track = "on_track"
    undertraining = "undertraining"


class GoalProgress(str, Enum):
    ahead = "ahead"
    on_track = "on_track"
    behind = "behind"


class WorkoutChange(BaseModel):
    date: date
    from_activity: PlannedActivityType
    to_activity: PlannedActivityType
    reason: str


class PlanReasoning(BaseModel):
    training_load_trend: TrainingLoadTrend
    goal_progress: GoalProgress
    two_week_volume_km: float              # actual volume over last 2 weeks
    target_two_week_volume_km: float       # what it should have been
    changes_from_last_week: list[WorkoutChange]
    summary: str                           # free-text conclusion for the athlete


class AdaptationStatus(str, Enum):
    pending_approval = "pending_approval"
    approved = "approved"
    rejected = "rejected"


class PlannedWorkout(BaseModel):
    date: date
    activity_type: PlannedActivityType

    # Only relevant for running sessions
    target_distance_km: Optional[float] = Field(default=None, gt=0, le=200)
    target_duration_seconds: Optional[int] = Field(default=None, gt=0, le=86400)
    target_hr_zone: Optional[int] = Field(default=None, ge=1, le=5)

    # Fetched at plan-generation time — used to adapt pace/intensity before the day arrives
    weather_forecast: Optional[WeatherCondition] = None

    notes: Optional[str] = None


class IntraWeekAdaptation(BaseModel):
    """A proposed mid-week change requiring user approval before being applied."""
    proposed_at: datetime
    trigger: str                          # e.g. "Elevated HR detected — possible injury"
    affected_dates: list[date]
    original_workouts: list[PlannedWorkout]
    proposed_workouts: list[PlannedWorkout]
    status: AdaptationStatus = AdaptationStatus.pending_approval


class Plan(BaseModel):
    id: str
    created_at: datetime
    week_start_date: date                 # Always a Monday

    # 7 entries: 3 runs + 3 strength + 1 rest, day-specific
    workouts: list[PlannedWorkout] = Field(default_factory=list)

    # Mandatory: adapt_plan node must populate this explaining why this plan was generated
    reasoning: PlanReasoning

    # Mid-week adaptations waiting for user approval
    pending_adaptation: Optional[IntraWeekAdaptation] = None

    @property
    def total_volume_km(self) -> float:
        return sum(w.target_distance_km for w in self.workouts if w.target_distance_km)
