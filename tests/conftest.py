"""Shared fixtures and builder helpers for all test modules."""
import pytest
from datetime import date, timedelta
from sqlalchemy import create_engine as _create_engine
from sqlalchemy.orm import Session

from db.schema import AthleteRow, Base
from models.athlete import Athlete, FitnessLevel
from models.goal import Location, RaceGoal, RaceType
from models.workout import ActivityType, DataSource, Workout


# ---------------------------------------------------------------------------
# DB fixture — fresh in-memory SQLite per test
# ---------------------------------------------------------------------------

@pytest.fixture()
def engine(monkeypatch):
    """Isolated in-memory SQLite DB; patched into both db.schema and memory.episodic."""
    eng = _create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    monkeypatch.setattr("db.schema.engine", eng)
    monkeypatch.setattr("memory.episodic.engine", eng)
    return eng


# ---------------------------------------------------------------------------
# Canonical test locations
# ---------------------------------------------------------------------------

BENGALURU = Location(city="Bengaluru", country="India",
                     latitude=12.97, longitude=77.59, altitude_m=920)
MUMBAI    = Location(city="Mumbai",    country="India",
                     latitude=19.07,  longitude=72.87,  altitude_m=10)


# ---------------------------------------------------------------------------
# Object builders
# ---------------------------------------------------------------------------

def make_athlete(
    *,
    athlete_id: str = "test_athlete",
    name: str = "Test Runner",
    max_heart_rate: int = 185,
    weekly_target_km: float = 45.0,
    race_date: date | None = None,
    injury_flags: list[str] | None = None,
    workouts: list[Workout] | None = None,
    strava_id: str = "strava_test",
) -> Athlete:
    return Athlete(
        id=athlete_id,
        name=name,
        fitness_level=FitnessLevel.intermediate,
        max_heart_rate=max_heart_rate,
        resting_heart_rate=52,
        weekly_mileage_target_km=weekly_target_km,
        injury_flags=injury_flags or [],
        recent_workouts=workouts or [],
        strava_athlete_id=strava_id,
        goal=RaceGoal(
            race_name="Test Marathon",
            race_type=RaceType.marathon,
            race_date=race_date or (date.today() + timedelta(weeks=20)),
            race_location=MUMBAI,
            training_location=BENGALURU,
            target_finish_seconds=4 * 3600,
        ),
    )


def make_workout(
    *,
    wid: str = "w1",
    activity_type: ActivityType = ActivityType.easy,
    distance_km: float | None = 10.0,
    avg_heart_rate: int | None = 145,
    max_heart_rate: int | None = 165,
    days_ago: int = 1,
) -> Workout:
    return Workout(
        id=wid,
        date=date.today() - timedelta(days=days_ago),
        activity_type=activity_type,
        source=DataSource.strava,
        distance_km=distance_km,
        duration_seconds=3600,
        avg_heart_rate=avg_heart_rate,
        max_heart_rate=max_heart_rate,
    )


def seed_athlete(eng, athlete: Athlete) -> None:
    """Insert an Athlete into the test DB as an AthleteRow."""
    with Session(eng) as session:
        session.add(AthleteRow(
            id=athlete.id,
            name=athlete.name,
            fitness_level=athlete.fitness_level.value,
            weekly_mileage_target_km=athlete.weekly_mileage_target_km,
            resting_heart_rate=athlete.resting_heart_rate,
            max_heart_rate=athlete.max_heart_rate,
            injury_flags=athlete.injury_flags,
            strava_athlete_id=athlete.strava_athlete_id,
            goal={
                "race_name": athlete.goal.race_name,
                "race_type": athlete.goal.race_type.value,
                "race_date": athlete.goal.race_date.isoformat(),
                "race_location": athlete.goal.race_location.model_dump(),
                "training_location": athlete.goal.training_location.model_dump(),
                "target_finish_seconds": athlete.goal.target_finish_seconds,
                "course_elevation_gain_m": athlete.goal.course_elevation_gain_m,
            },
        ))
        session.commit()
