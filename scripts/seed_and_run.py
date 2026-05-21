#!/usr/bin/env python3
"""
Seed script — creates a test athlete and runs one full agent cycle.
Verifies the entire pipeline is wired correctly before writing formal tests.

Usage:
    source .venv/bin/activate
    python scripts/seed_and_run.py
"""
import os
import sys
import time as _time

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Patch sleep globally before any node imports it — wait node must not block
_time.sleep = lambda s: None

from datetime import date, datetime, timedelta
from unittest.mock import patch

from dotenv import load_dotenv
load_dotenv()

import structlog
structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ]
)

# ---------------------------------------------------------------------------
# Sample data — replaces real Strava/Garmin API calls
# ---------------------------------------------------------------------------

from models.weather import WeatherCondition
from models.workout import ActivityType, DataSource, Workout
from tools.weather import DailyForecast, WeeklyForecast

_today = date.today()

SAMPLE_WORKOUTS = [
    Workout(id="w1", date=_today - timedelta(days=2),  activity_type=ActivityType.easy,     source=DataSource.strava, distance_km=8.0,  duration_seconds=2700, avg_heart_rate=145, max_heart_rate=162, elevation_gain_m=80.0),
    Workout(id="w2", date=_today - timedelta(days=4),  activity_type=ActivityType.tempo,    source=DataSource.strava, distance_km=10.0, duration_seconds=3000, avg_heart_rate=165, max_heart_rate=178, elevation_gain_m=50.0),
    Workout(id="w3", date=_today - timedelta(days=7),  activity_type=ActivityType.long_run, source=DataSource.strava, distance_km=18.0, duration_seconds=6300, avg_heart_rate=150, max_heart_rate=170, elevation_gain_m=150.0),
    Workout(id="w4", date=_today - timedelta(days=9),  activity_type=ActivityType.easy,     source=DataSource.strava, distance_km=8.0,  duration_seconds=2800, avg_heart_rate=148, max_heart_rate=160, elevation_gain_m=80.0),
    Workout(id="w5", date=_today - timedelta(days=11), activity_type=ActivityType.strength, source=DataSource.strava, distance_km=None, duration_seconds=3600, avg_heart_rate=130, max_heart_rate=155, elevation_gain_m=None),
]

SAMPLE_WEATHER = WeeklyForecast(
    city_lat=12.9716,
    city_lng=77.5946,
    forecasts=[
        DailyForecast(
            date=_today + timedelta(days=i),
            condition=WeatherCondition(
                temperature_c=28.0, feels_like_c=30.0,
                humidity_pct=65, wind_speed_kmh=12.0,
                description="Partly cloudy",
            ),
        )
        for i in range(7)
    ],
)

# ---------------------------------------------------------------------------
# Seed
# ---------------------------------------------------------------------------

ATHLETE_ID = "seed_athlete_001"


def seed_athlete() -> None:
    from db.schema import AthleteRow, engine
    from sqlalchemy.orm import Session

    with Session(engine) as session:
        existing = session.get(AthleteRow, ATHLETE_ID)
        if existing:
            session.delete(existing)
            session.commit()

        session.add(AthleteRow(
            id=ATHLETE_ID,
            name="Shagun Vashistha",
            fitness_level="intermediate",
            weekly_mileage_target_km=45.0,
            resting_heart_rate=52,
            max_heart_rate=185,
            injury_flags=[],
            strava_athlete_id="strava_test_123",
            goal={
                "race_name": "Mumbai Marathon 2026",
                "race_type": "marathon",
                "race_date": (_today + timedelta(weeks=16)).isoformat(),
                "race_location": {
                    "city": "Mumbai", "country": "India",
                    "latitude": 19.0760, "longitude": 72.8777, "altitude_m": 10.0,
                },
                "training_location": {
                    "city": "Bengaluru", "country": "India",
                    "latitude": 12.9716, "longitude": 77.5946, "altitude_m": 920.0,
                },
                "target_finish_seconds": 4 * 3600,
                "course_elevation_gain_m": 120.0,
            },
        ))
        session.commit()

    print("✓ Athlete seeded")


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

def run() -> None:
    from db.schema import init_db
    from agent.state import AgentState, RunPhase
    from langgraph.graph import END
    import agent.graph as graph_module
    from agent.nodes.wait import _reset

    print("\n=== Initialising DB ===")
    init_db()
    seed_athlete()

    # Patch wait: skip polling loop, immediately hand back to the router
    def _seed_wait(state: AgentState) -> dict:
        print("\n✓ Reached wait node — one cycle complete")
        return _reset(state, run_phase=RunPhase.post_run)

    # Patch router: always exit after one cycle
    def _seed_route_after_wait(_state: AgentState) -> str:
        return END

    # These must be patched on graph_module BEFORE build_graph() is called,
    # because build_graph() captures the local names at add_node() time
    graph_module.wait = _seed_wait
    graph_module._route_after_wait = _seed_route_after_wait

    print("\n=== Running one agent cycle ===")
    print("(assess → fetch_data → analyze → adapt_plan → wait → END)\n")

    # Patch where the name is used (the importing module), not where it's defined.
    # Python's "from X import Y" creates a local binding — patching X.Y has no effect.
    with patch("agent.nodes.fetch_data.fetch_strava_activities", return_value=SAMPLE_WORKOUTS), \
         patch("agent.nodes.fetch_data.fetch_strava_activity",   return_value=SAMPLE_WORKOUTS[0]), \
         patch("agent.nodes.wait.poll_new_activity",             return_value=None), \
         patch("agent.nodes.wait.poll_garmin_activity",          return_value=None), \
         patch("agent.nodes.adapt_plan.get_forecast",            return_value=SAMPLE_WEATHER):

        graph = graph_module.build_graph()
        final_state = graph.invoke(
            AgentState(
                athlete_id=ATHLETE_ID,
                run_phase=RunPhase.weekly_replan,
                cycle_started_at=datetime.now(),
            ),
            config={"configurable": {"thread_id": ATHLETE_ID}},
        )

    # LangGraph returns a dict for dataclass-based state
    plan = (
        final_state.get("current_plan")
        if isinstance(final_state, dict)
        else getattr(final_state, "current_plan", None)
    )
    errors = (
        final_state.get("errors", [])
        if isinstance(final_state, dict)
        else getattr(final_state, "errors", [])
    )

    print("\n=== Results ===")

    if errors:
        print(f"Errors during cycle: {errors}")

    if plan is None:
        print("✗ No plan generated")
        return

    print(f"\n✓ Plan generated successfully")
    print(f"  ID:            {plan.id}")
    print(f"  Week start:    {plan.week_start_date}")
    print(f"  Total volume:  {plan.total_volume_km:.1f} km")
    print(f"\n  Reasoning:")
    print(f"    Load trend:     {plan.reasoning.training_load_trend}")
    print(f"    Goal progress:  {plan.reasoning.goal_progress}")
    print(f"    Summary:        {plan.reasoning.summary}")
    if plan.reasoning.changes_from_last_week:
        print(f"    Changes:")
        for c in plan.reasoning.changes_from_last_week:
            print(f"      {c.date} {c.from_activity} → {c.to_activity}: {c.reason}")

    print(f"\n  Weekly schedule:")
    for w in plan.workouts:
        dist  = f"{w.target_distance_km:.1f} km" if w.target_distance_km else "—"
        zone  = f"zone {w.target_hr_zone}" if w.target_hr_zone else ""
        notes = f"  ← {w.notes}" if w.notes else ""
        print(f"    {w.date}  {w.date.strftime('%A'):<9}  {w.activity_type.value:<16}  {dist:<9}  {zone}{notes}")


if __name__ == "__main__":
    run()
