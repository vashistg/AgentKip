#!/usr/bin/env python3
"""
Live run — fetches real data and generates a weekly plan for any athlete in the DB.

Usage:
    source .venv/bin/activate
    python scripts/run_real.py                        # defaults to gaurav_real_001
    python scripts/run_real.py --athlete shagun
"""
import os
import sys
import time as _time
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_time.sleep = lambda s: None

from datetime import datetime

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


def run(athlete_id: str) -> None:
    from db.schema import init_db, load_athlete
    from agent.state import AgentState, RunPhase
    from langgraph.graph import END
    import agent.graph as graph_module
    from agent.nodes.wait import _reset

    init_db()

    athlete = load_athlete(athlete_id)
    if athlete is None:
        print(f"✗ No athlete found with id={athlete_id!r}")
        sys.exit(1)
    print(f"✓ Loaded athlete: {athlete.name} (id={athlete_id})")

    # Patch wait node to exit after one cycle instead of polling forever
    def _one_shot_wait(state: AgentState) -> dict:
        print("\n✓ Reached wait node — live cycle complete")
        return _reset(state, run_phase=RunPhase.post_run)

    def _one_shot_route(_: AgentState) -> str:
        return END

    graph_module.wait = _one_shot_wait
    graph_module._route_after_wait = _one_shot_route

    print("\n=== Running LIVE agent cycle ===")
    print("(assess → fetch_data → analyze → adapt_plan → wait → END)\n")

    from unittest.mock import patch
    with patch("agent.nodes.wait.poll_new_activity",   return_value=None), \
         patch("agent.nodes.wait.poll_garmin_activity", return_value=None):

        graph = graph_module.build_graph()
        final_state = graph.invoke(
            AgentState(
                athlete_id=athlete_id,
                run_phase=RunPhase.weekly_replan,
                cycle_started_at=datetime.now(),
            ),
            config={"configurable": {"thread_id": athlete_id}},
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

    print("\n\n=== Recent workouts ===\n")
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
    parser = argparse.ArgumentParser()
    parser.add_argument("--athlete", default="gaurav_real_001", help="Athlete ID from the DB")
    args = parser.parse_args()
    run(args.athlete)
