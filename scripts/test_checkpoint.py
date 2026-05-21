#!/usr/bin/env python3
"""
Checkpoint resume test.

Run 1: crashes deliberately inside analyze → checkpoint saved after fetch_data.
Run 2: resumes from analyze — assess + fetch_data do NOT re-run.

Usage:
    python scripts/test_checkpoint.py
"""
import os
import sys
import time as _time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
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

from models.weather import WeatherCondition
from models.workout import ActivityType, DataSource, Workout
from tools.weather import DailyForecast, WeeklyForecast

_today = date.today()

SAMPLE_WORKOUTS = [
    Workout(id="ck1", date=_today - timedelta(days=2), activity_type=ActivityType.easy,
            source=DataSource.strava, distance_km=8.0, duration_seconds=2700,
            avg_heart_rate=145, max_heart_rate=162, elevation_gain_m=80.0),
    Workout(id="ck2", date=_today - timedelta(days=4), activity_type=ActivityType.tempo,
            source=DataSource.strava, distance_km=10.0, duration_seconds=3000,
            avg_heart_rate=165, max_heart_rate=178, elevation_gain_m=50.0),
]

SAMPLE_WEATHER = WeeklyForecast(
    city_lat=12.9716, city_lng=77.5946,
    forecasts=[
        DailyForecast(
            date=_today + timedelta(days=i),
            condition=WeatherCondition(temperature_c=28.0, feels_like_c=30.0,
                                       humidity_pct=65, wind_speed_kmh=12.0,
                                       description="Partly cloudy"),
        )
        for i in range(7)
    ],
)

ATHLETE_ID = "seed_athlete_001"
THREAD_ID  = "checkpoint_test_thread"


def _run_graph(crash_in_analyze: bool, label: str, resume: bool = False) -> None:
    # Re-import to get clean module references each run
    import importlib
    import agent.graph as graph_module
    import agent.nodes.assess as assess_module
    import agent.nodes.fetch_data as fetch_data_module
    import agent.nodes.analyze as analyze_module
    import agent.nodes.adapt_plan as adapt_plan_module
    import agent.nodes.wait as wait_module
    for mod in [graph_module, assess_module, fetch_data_module, analyze_module, adapt_plan_module, wait_module]:
        importlib.reload(mod)

    from db.schema import init_db
    from agent.state import AgentState, RunPhase
    from langgraph.graph import END
    from agent.nodes.wait import _reset

    init_db()

    nodes_started = []

    def _tracking(name, fn):
        def wrapper(state):
            nodes_started.append(name)
            print(f"  → node started: {name}")
            return fn(state)
        return wrapper

    graph_module.assess     = _tracking("assess",     assess_module.assess)
    graph_module.fetch_data = _tracking("fetch_data", fetch_data_module.fetch_data)
    graph_module.adapt_plan = _tracking("adapt_plan", adapt_plan_module.adapt_plan)

    if crash_in_analyze:
        def _crashing_analyze(state):
            nodes_started.append("analyze")
            print("  → node started: analyze")
            print("  💥 crashing deliberately inside analyze …")
            raise RuntimeError("Simulated crash in analyze")
        graph_module.analyze = _crashing_analyze
    else:
        graph_module.analyze = _tracking("analyze", analyze_module.analyze)

    def _test_wait(state: AgentState) -> dict:
        nodes_started.append("wait")
        print("  → node started: wait")
        return _reset(state, run_phase=RunPhase.post_run)

    graph_module.wait = _test_wait
    graph_module._route_after_wait = lambda _: END

    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"{'='*60}")

    # First run: pass full state. Resume: pass None so LangGraph loads checkpoint.
    invoke_input = None if resume else AgentState(
        athlete_id=ATHLETE_ID,
        run_phase=RunPhase.weekly_replan,
        cycle_started_at=datetime.now(),
    )

    with patch("agent.nodes.fetch_data.fetch_strava_activities", return_value=SAMPLE_WORKOUTS), \
         patch("agent.nodes.fetch_data.fetch_strava_activity",   return_value=SAMPLE_WORKOUTS[0]), \
         patch("agent.nodes.wait.poll_new_activity",             return_value=None), \
         patch("agent.nodes.wait.poll_garmin_activity",          return_value=None), \
         patch("agent.nodes.adapt_plan.get_forecast",            return_value=SAMPLE_WEATHER):

        graph = graph_module.build_graph()
        try:
            graph.invoke(
                invoke_input,
                config={"configurable": {"thread_id": THREAD_ID}},
            )
        except RuntimeError as e:
            print(f"\n  ✓ Graph stopped with: {e}")

    print(f"\n  Nodes that ran this invocation: {nodes_started}")


def wipe_checkpoint() -> None:
    import sqlite3
    conn = sqlite3.connect("db/checkpoints.db")
    conn.execute("DELETE FROM checkpoints WHERE thread_id = ?", (THREAD_ID,))
    conn.execute("DELETE FROM writes")
    conn.commit()
    conn.close()
    print(f"✓ Cleared checkpoints for thread '{THREAD_ID}'")


def show_checkpoints() -> None:
    import sqlite3
    conn = sqlite3.connect("db/checkpoints.db")
    rows = conn.execute(
        "SELECT checkpoint_id, metadata FROM checkpoints WHERE thread_id = ? ORDER BY checkpoint_id",
        (THREAD_ID,)
    ).fetchall()
    conn.close()
    print(f"\n  Checkpoints in DB ({len(rows)} rows):")
    import json
    for r in rows:
        meta = json.loads(r[1])
        print(f"    step={meta['step']:2d}  source={meta['source']:<6}  id={r[0]}")


if __name__ == "__main__":
    from db.schema import init_db, AthleteRow, engine
    from sqlalchemy.orm import Session
    from datetime import timedelta

    init_db()

    # Ensure test athlete exists
    with Session(engine) as session:
        if not session.get(AthleteRow, ATHLETE_ID):
            session.add(AthleteRow(
                id=ATHLETE_ID, name="Test Athlete", fitness_level="intermediate",
                weekly_mileage_target_km=45.0, resting_heart_rate=52, max_heart_rate=185,
                injury_flags=[], strava_athlete_id="strava_test_123",
                goal={
                    "race_name": "Mumbai Marathon 2026", "race_type": "marathon",
                    "race_date": (_today + timedelta(weeks=16)).isoformat(),
                    "race_location": {"city": "Mumbai", "country": "India",
                                      "latitude": 19.076, "longitude": 72.8777, "altitude_m": 10.0},
                    "training_location": {"city": "Bengaluru", "country": "India",
                                          "latitude": 12.9716, "longitude": 77.5946, "altitude_m": 920.0},
                    "target_finish_seconds": 4 * 3600, "course_elevation_gain_m": 120.0,
                },
            ))
            session.commit()

    # 1. Clean slate
    wipe_checkpoint()

    # 2. Run 1 — crashes inside analyze
    _run_graph(crash_in_analyze=True,  label="RUN 1: crashes inside analyze", resume=False)
    show_checkpoints()

    # 3. Run 2 — resume=True → passes None to invoke, LangGraph resumes from analyze
    _run_graph(crash_in_analyze=False, label="RUN 2: resumes — assess + fetch_data should NOT re-run", resume=True)
    show_checkpoints()
