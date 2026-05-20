from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional

from models.athlete import Athlete
from models.plan import GoalProgress, Plan, TrainingLoadTrend
from models.workout import Workout


class RunPhase(str, Enum):
    weekly_replan = "weekly_replan"   # Sunday night → generate full new week plan
    post_run = "post_run"             # After a workout → check for intra-week adaptation


@dataclass
class AnalysisResult:
    """Output of the analyze node. Feeds directly into PlanReasoning in adapt_plan."""
    training_load_trend: TrainingLoadTrend
    goal_progress: GoalProgress
    two_week_volume_km: float
    target_two_week_volume_km: float
    notes: list[str] = field(default_factory=list)  # specific observations, e.g. "HR elevated on 3 runs"


@dataclass
class AgentState:
    # What triggered this cycle and when
    run_phase: RunPhase
    cycle_started_at: datetime
    athlete_id: str = ""   # set by main.py; used by assess to load athlete from DB

    # Core entities — loaded at the start of each cycle
    athlete: Optional[Athlete] = None
    current_plan: Optional[Plan] = None   # this week's active plan
    last_plan: Optional[Plan] = None      # previous week's plan, used for week-over-week diff

    # Only set in post_run phase — the workout that triggered this cycle
    trigger_workout_id: Optional[str] = None

    # Populated by assess node
    cleared_to_train: bool = True
    assessment_notes: list[str] = field(default_factory=list)

    # Populated by analyze node
    analysis: Optional[AnalysisResult] = None

    # Errors accumulate across nodes so the graph can decide whether to halt or continue
    errors: list[str] = field(default_factory=list)
