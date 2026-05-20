from datetime import date, datetime
from typing import Optional

import structlog
from sqlalchemy import Boolean, Column, Date, DateTime, Float, Integer, JSON, String, create_engine, desc, text
from sqlalchemy.orm import DeclarativeBase, Session

from models.athlete import Athlete, FitnessLevel
from models.goal import Location, RaceGoal, RaceType
from models.plan import (
    AdaptationStatus,
    GoalProgress,
    IntraWeekAdaptation,
    Plan,
    PlannedActivityType,
    PlannedWorkout,
    PlanReasoning,
    TrainingLoadTrend,
    WorkoutChange,
)

logger = structlog.get_logger()

DATABASE_URL = "sqlite:///db/running_coach.db"
engine = create_engine(DATABASE_URL, echo=False)


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Table definitions
# ---------------------------------------------------------------------------

class AthleteRow(Base):
    __tablename__ = "athletes"

    id                      = Column(String,  primary_key=True)
    name                    = Column(String,  nullable=False)
    date_of_birth           = Column(Date,    nullable=True)
    resting_heart_rate      = Column(Integer, nullable=True)
    max_heart_rate          = Column(Integer, nullable=True)
    fitness_level           = Column(String,  nullable=False)
    weekly_mileage_target_km = Column(Float,  nullable=False)
    injury_flags            = Column(JSON,    nullable=False, default=list)
    goal                    = Column(JSON,    nullable=False)   # serialized RaceGoal
    strava_athlete_id       = Column(String,  nullable=True)
    garmin_user_id          = Column(String,  nullable=True)

    def to_athlete(self) -> Athlete:
        g = self.goal
        goal = RaceGoal(
            race_name=g["race_name"],
            race_type=RaceType(g["race_type"]),
            race_date=date.fromisoformat(g["race_date"]),
            race_location=Location(**g["race_location"]),
            training_location=Location(**g["training_location"]),
            target_finish_seconds=g.get("target_finish_seconds"),
            course_elevation_gain_m=g.get("course_elevation_gain_m"),
        )
        return Athlete(
            id=self.id,
            name=self.name,
            date_of_birth=self.date_of_birth,
            resting_heart_rate=self.resting_heart_rate,
            max_heart_rate=self.max_heart_rate,
            fitness_level=FitnessLevel(self.fitness_level),
            weekly_mileage_target_km=self.weekly_mileage_target_km,
            injury_flags=self.injury_flags or [],
            goal=goal,
            strava_athlete_id=self.strava_athlete_id,
            garmin_user_id=self.garmin_user_id,
        )


class PlanRow(Base):
    __tablename__ = "plans"

    id                  = Column(String,   primary_key=True)
    athlete_id          = Column(String,   nullable=False)
    created_at          = Column(DateTime, nullable=False)
    week_start_date     = Column(Date,     nullable=False)
    workouts            = Column(JSON,     nullable=False)
    reasoning           = Column(JSON,     nullable=False)
    pending_adaptation  = Column(JSON,     nullable=True)
    is_current          = Column(Boolean,  nullable=False, default=True)

    def to_plan(self) -> Plan:
        workouts = [PlannedWorkout.model_validate(w) for w in self.workouts]

        r = self.reasoning
        reasoning = PlanReasoning(
            training_load_trend=TrainingLoadTrend(r["training_load_trend"]),
            goal_progress=GoalProgress(r["goal_progress"]),
            two_week_volume_km=r["two_week_volume_km"],
            target_two_week_volume_km=r["target_two_week_volume_km"],
            changes_from_last_week=[
                WorkoutChange(
                    date=date.fromisoformat(c["date"]),
                    from_activity=PlannedActivityType(c["from_activity"]),
                    to_activity=PlannedActivityType(c["to_activity"]),
                    reason=c["reason"],
                )
                for c in r.get("changes_from_last_week", [])
            ],
            summary=r["summary"],
        )

        pending = None
        if self.pending_adaptation:
            pa = self.pending_adaptation
            pending = IntraWeekAdaptation(
                proposed_at=datetime.fromisoformat(pa["proposed_at"]),
                trigger=pa["trigger"],
                affected_dates=[date.fromisoformat(d) for d in pa["affected_dates"]],
                original_workouts=[PlannedWorkout.model_validate(w) for w in pa["original_workouts"]],
                proposed_workouts=[PlannedWorkout.model_validate(w) for w in pa["proposed_workouts"]],
                status=AdaptationStatus(pa["status"]),
            )

        return Plan(
            id=self.id,
            created_at=self.created_at,
            week_start_date=self.week_start_date,
            workouts=workouts,
            reasoning=reasoning,
            pending_adaptation=pending,
        )


# ---------------------------------------------------------------------------
# Public functions called by tools and nodes
# ---------------------------------------------------------------------------

def init_db() -> None:
    """Create all tables. Call once at startup."""
    Base.metadata.create_all(engine)
    logger.info("db_initialised", url=DATABASE_URL)


def load_athlete(athlete_id: str) -> Optional[Athlete]:
    with Session(engine) as session:
        row = session.get(AthleteRow, athlete_id)
        return row.to_athlete() if row else None


def save_plan_to_db(plan: Plan, athlete_id: str) -> None:
    with Session(engine) as session:
        # Demote previous current plan before inserting the new one
        session.execute(
            text("UPDATE plans SET is_current = 0 WHERE athlete_id = :aid AND is_current = 1"),
            {"aid": athlete_id},
        )
        row = PlanRow(
            id=plan.id,
            athlete_id=athlete_id,
            created_at=plan.created_at,
            week_start_date=plan.week_start_date,
            workouts=[w.model_dump(mode="json") for w in plan.workouts],
            reasoning=plan.reasoning.model_dump(mode="json"),
            pending_adaptation=(
                plan.pending_adaptation.model_dump(mode="json")
                if plan.pending_adaptation else None
            ),
            is_current=True,
        )
        session.add(row)
        session.commit()
        logger.info("plan_saved", plan_id=plan.id, athlete_id=athlete_id)


def load_plan_from_db(athlete_id: str, current: bool) -> Optional[Plan]:
    with Session(engine) as session:
        query = session.query(PlanRow).filter_by(athlete_id=athlete_id, is_current=current)
        if not current:
            # Most recent previous plan by week start date
            query = query.order_by(desc(PlanRow.week_start_date))
        row = query.first()
        return row.to_plan() if row else None
