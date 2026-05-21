#!/usr/bin/env python3
"""
Week 2 simulation — Shagun undertrained badly with elevated HR throughout.
Runs on top of the existing DB from seed_and_run.py (week 1 plan must exist).

Usage:
    source .venv/bin/activate
    python scripts/simulate_week2.py
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

# ---------------------------------------------------------------------------
# Week 2 actuals — undertrained + high HR throughout
#
# Planned week was: Easy 10km / Strength / Tempo 11km / Strength / Long 22km / Strength / Rest
# What actually happened: shorter runs, high HR, multiple stops, missed sessions
# ---------------------------------------------------------------------------

WEEK2_WORKOUTS = [
    # Monday: Easy run — only 6 km (planned 10), avg HR 165 = 89% of max 185 → anomaly
    Workout(
        id="w2_1", date=_today - timedelta(days=6),
        activity_type=ActivityType.easy, source=DataSource.strava,
        distance_km=6.0, duration_seconds=2700,
        avg_heart_rate=165, max_heart_rate=181, elevation_gain_m=60.0,
    ),
    # Tuesday: Strength — completed but felt heavy
    Workout(
        id="w2_2", date=_today - timedelta(days=5),
        activity_type=ActivityType.strength, source=DataSource.strava,
        distance_km=None, duration_seconds=2700,
        avg_heart_rate=128, max_heart_rate=148, elevation_gain_m=None,
    ),
    # Wednesday: Tempo — stopped at 5 km (planned 11), avg HR 178 = 96% → near max
    Workout(
        id="w2_3", date=_today - timedelta(days=4),
        activity_type=ActivityType.tempo, source=DataSource.strava,
        distance_km=5.0, duration_seconds=1800,
        avg_heart_rate=178, max_heart_rate=185, elevation_gain_m=40.0,
    ),
    # Thursday: Skipped strength — rest
    # Friday: Tried long run, HR too high, converted to easy, stopped at 8 km (planned 22)
    Workout(
        id="w2_4", date=_today - timedelta(days=2),
        activity_type=ActivityType.easy, source=DataSource.strava,
        distance_km=8.0, duration_seconds=3600,
        avg_heart_rate=172, max_heart_rate=183, elevation_gain_m=70.0,
    ),
    # Saturday + Sunday: Full rest — no entries on Strava
]

# Warmer forecast for week 2 — temperature crept up
WEEK2_WEATHER = WeeklyForecast(
    city_lat=12.9716, city_lng=77.5946,
    forecasts=[
        DailyForecast(
            date=_today + timedelta(days=i),
            condition=WeatherCondition(
                temperature_c=31.0, feels_like_c=34.0,
                humidity_pct=72, wind_speed_kmh=8.0,
                description="Hot and humid",
            ),
        )
        for i in range(7)
    ],
)

ATHLETE_ID = "seed_athlete_001"


def run() -> None:
    from db.schema import init_db
    from agent.state import AgentState, RunPhase
    from langgraph.graph import END
    import agent.graph as graph_module
    from agent.nodes.wait import _reset
    from tools.plan import load_current_plan, load_last_plan

    init_db()

    # Week 1 plan becomes last_plan so the LLM can diff against it
    week1_plan = load_current_plan(ATHLETE_ID)
    if week1_plan is None:
        print("✗ No week 1 plan found. Run seed_and_run.py first.")
        return

    print(f"✓ Loaded week 1 plan ({week1_plan.week_start_date}) as last_plan")
    print(f"  Volume: {week1_plan.total_volume_km:.1f} km | Summary: {week1_plan.reasoning.summary[:80]}…\n")

    # Patch wait + router to exit after one cycle
    def _seed_wait(state: AgentState) -> dict:
        print("\n✓ Reached wait node — week 2 cycle complete")
        return _reset(state, run_phase=RunPhase.post_run)

    def _seed_route_after_wait(_: AgentState) -> str:
        return END

    graph_module.wait = _seed_wait
    graph_module._route_after_wait = _seed_route_after_wait

    print("=== Running week 2 agent cycle ===")
    print("(assess → fetch_data → analyze → adapt_plan → wait → END)\n")

    with patch("agent.nodes.fetch_data.fetch_strava_activities", return_value=WEEK2_WORKOUTS), \
         patch("agent.nodes.fetch_data.fetch_strava_activity",   return_value=WEEK2_WORKOUTS[0]), \
         patch("agent.nodes.wait.poll_new_activity",             return_value=None), \
         patch("agent.nodes.wait.poll_garmin_activity",          return_value=None), \
         patch("agent.nodes.adapt_plan.get_forecast",            return_value=WEEK2_WEATHER):

        graph = graph_module.build_graph()
        final_state = graph.invoke(
            AgentState(
                athlete_id=ATHLETE_ID,
                run_phase=RunPhase.weekly_replan,
                cycle_started_at=datetime.now(),
                current_plan=None,
                last_plan=week1_plan,
            ),
            config={"configurable": {"thread_id": ATHLETE_ID}},
        )

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

    print("\n=== Week 2 Plan ===")
    if errors:
        print(f"Errors: {errors}")
    if plan is None:
        print("✗ No plan generated")
        return

    print(f"\n✓ Plan generated: {plan.id}")
    print(f"  Week start:    {plan.week_start_date}")
    print(f"  Total volume:  {plan.total_volume_km:.1f} km")
    print(f"\n  Reasoning:")
    print(f"    Load trend:    {plan.reasoning.training_load_trend}")
    print(f"    Goal progress: {plan.reasoning.goal_progress}")
    print(f"    Summary: {plan.reasoning.summary}")
    if plan.reasoning.changes_from_last_week:
        print(f"\n    Changes from week 1:")
        for c in plan.reasoning.changes_from_last_week:
            print(f"      {c.date}  {c.from_activity.value} → {c.to_activity.value}: {c.reason}")

    print(f"\n  Schedule:")
    for w in plan.workouts:
        dist  = f"{w.target_distance_km:.1f} km" if w.target_distance_km else "—"
        zone  = f"zone {w.target_hr_zone}" if w.target_hr_zone else ""
        notes = f"  ← {w.notes[:80]}…" if w.notes and len(w.notes) > 80 else (f"  ← {w.notes}" if w.notes else "")
        print(f"    {w.date}  {w.date.strftime('%A'):<9}  {w.activity_type.value:<16}  {dist:<9}  {zone}{notes}")

    # ---------------------------------------------------------------------------
    print("\n\n=== Semantic Memory (all entries, oldest first) ===\n")
    from memory.semantic import retrieve_relevant
    entries = retrieve_relevant(ATHLETE_ID, query="training plan weekly summary", n_results=10)
    # Sort by the embedded date "Week of YYYY-MM-DD" so output is chronological
    entries.sort(key=lambda d: d[8:18] if d.startswith("Week of ") else d)
    for i, doc in enumerate(entries, 1):
        print(f"[Week {i}]\n{doc}\n")

    print("\n=== Episodic Memory (all entries) ===\n")
    import json
    from memory.episodic import get_recent_episodes
    episodes = get_recent_episodes(ATHLETE_ID, limit=20)
    for e in episodes:
        print(f"Type:  {e['type']}")
        print(f"Date:  {e['event_date']}")
        print(f"Data:  {json.dumps(e['data'], indent=2)}")
        print()


if __name__ == "__main__":
    run()
