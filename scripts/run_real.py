#!/usr/bin/env python3
"""
Live run — connects to real Strava, generates a weekly plan for Gaurav.
Only the wait node is patched (so it doesn't block polling forever).

Usage:
    source .venv/bin/activate
    python scripts/run_real.py
"""
import os
import sys
import time as _time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_time.sleep = lambda s: None

from datetime import date, datetime, timedelta

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

ATHLETE_ID = "gaurav_real_001"

# ---------------------------------------------------------------------------
# Athlete profile — edit these to match your actual details
# ---------------------------------------------------------------------------
ATHLETE_PROFILE = {
    "id":                        ATHLETE_ID,
    "name":                      "Gaurav Vashist",
    "fitness_level":             "intermediate",
    "weekly_mileage_target_km":  40.0,
    "resting_heart_rate":        52,
    "max_heart_rate":            190,
    "injury_flags":              [],
    "strava_athlete_id":         "18391711",
    "goal": {
        "race_name":             "Mumbai Marathon 2027",
        "race_type":             "marathon",
        "race_date":             (date.today() + timedelta(weeks=32)).isoformat(),
        "race_location": {
            "city": "Mumbai", "country": "India",
            "latitude": 19.0760, "longitude": 72.8777, "altitude_m": 10.0,
        },
        "training_location": {
            "city": "Bengaluru", "country": "India",
            "latitude": 12.9716, "longitude": 77.5946, "altitude_m": 920.0,
        },
        "target_finish_seconds": 4 * 3600,   # 4-hour goal
        "course_elevation_gain_m": 120.0,
    },
}


def seed_athlete() -> None:
    from db.schema import AthleteRow, engine
    from sqlalchemy.orm import Session

    with Session(engine) as session:
        existing = session.get(AthleteRow, ATHLETE_ID)
        if existing:
            session.delete(existing)
            session.commit()

        p = ATHLETE_PROFILE
        session.add(AthleteRow(
            id=p["id"],
            name=p["name"],
            fitness_level=p["fitness_level"],
            weekly_mileage_target_km=p["weekly_mileage_target_km"],
            resting_heart_rate=p["resting_heart_rate"],
            max_heart_rate=p["max_heart_rate"],
            injury_flags=p["injury_flags"],
            strava_athlete_id=p["strava_athlete_id"],
            goal=p["goal"],
        ))
        session.commit()
    print(f"✓ Athlete seeded: {p['name']} (Strava ID {p['strava_athlete_id']})")


def run() -> None:
    from db.schema import init_db
    from agent.state import AgentState, RunPhase
    from langgraph.graph import END
    import agent.graph as graph_module
    from agent.nodes.wait import _reset

    init_db()
    seed_athlete()

    # Patch wait node to exit after one cycle instead of polling forever
    def _real_wait(state: AgentState) -> dict:
        print("\n✓ Reached wait node — live cycle complete")
        return _reset(state, run_phase=RunPhase.post_run)

    def _real_route_after_wait(_: AgentState) -> str:
        return END

    graph_module.wait = _real_wait
    graph_module._route_after_wait = _real_route_after_wait

    print("\n=== Running LIVE agent cycle (real Strava data) ===")
    print("(assess → fetch_data → analyze → adapt_plan → wait → END)\n")

    # No patches on fetch_data — real Strava calls go through
    from unittest.mock import patch
    with patch("agent.nodes.wait.poll_new_activity",  return_value=None), \
         patch("agent.nodes.wait.poll_garmin_activity", return_value=None):

        graph = graph_module.build_graph()
        final_state = graph.invoke(
            AgentState(
                athlete_id=ATHLETE_ID,
                run_phase=RunPhase.weekly_replan,
                cycle_started_at=datetime.now(),
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

    print("\n=== Live Plan ===")
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
    print(f"    Summary:\n")
    # Word-wrap the summary at 80 chars
    import textwrap
    for line in textwrap.wrap(plan.reasoning.summary, width=90):
        print(f"      {line}")

    if plan.reasoning.changes_from_last_week:
        print(f"\n    Changes from last week:")
        for c in plan.reasoning.changes_from_last_week:
            print(f"      {c.date}  {c.from_activity.value} → {c.to_activity.value}: {c.reason}")

    print(f"\n  Schedule:")
    for w in plan.workouts:
        dist  = f"{w.target_distance_km:.1f} km" if w.target_distance_km else "—"
        zone  = f"zone {w.target_hr_zone}" if w.target_hr_zone else ""
        notes = f"\n           {w.notes[:100]}…" if w.notes and len(w.notes) > 100 else (f"\n           {w.notes}" if w.notes else "")
        print(f"    {w.date}  {w.date.strftime('%A'):<9}  {w.activity_type.value:<16}  {dist:<9}  {zone}{notes}")

    print("\n\n=== Workouts fetched from Strava ===\n")
    fetched_athlete = (
        final_state.get("athlete")
        if isinstance(final_state, dict)
        else getattr(final_state, "athlete", None)
    )
    if fetched_athlete:
        for w in fetched_athlete.recent_workouts:
            dist = f"{w.distance_km:.1f} km" if w.distance_km else "—"
            hr   = f"avg HR {w.avg_heart_rate}" if w.avg_heart_rate else ""
            print(f"  {w.date}  {w.activity_type.value:<16}  {dist:<10}  {hr}")


if __name__ == "__main__":
    run()
