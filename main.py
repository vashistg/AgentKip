import argparse
import sys
import time
from datetime import datetime

import structlog
from dotenv import load_dotenv

from agent.graph import build_graph
from agent.state import AgentState, RunPhase
from db.schema import init_db
from tools.plan import load_current_plan, load_last_plan

load_dotenv()

structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ]
)

logger = structlog.get_logger()

SUNDAY = 6
WEEKLY_REPLAN_HOUR = 20
RESTART_DELAY_SECONDS = 60


def main(athlete_id: str) -> None:
    logger.info("coach_starting", athlete_id=athlete_id)

    init_db()

    from db.schema import load_athlete
    if load_athlete(athlete_id) is None:
        print(f"\nNo profile found for '{athlete_id}'.")
        print("Run onboarding first:\n")
        print("    python scripts/onboard.py\n")
        sys.exit(1)

    current_plan = load_current_plan(athlete_id)
    last_plan = load_last_plan(athlete_id)

    # Force a weekly replan on first run — no plan exists yet
    if current_plan is None:
        run_phase = RunPhase.weekly_replan
        logger.info("no_existing_plan", action="forcing_weekly_replan")
    else:
        run_phase = _initial_run_phase()

    initial_state = AgentState(
        athlete_id=athlete_id,
        run_phase=run_phase,
        cycle_started_at=datetime.now(),
        current_plan=current_plan,
        last_plan=last_plan,
    )

    logger.info("starting_graph", run_phase=run_phase, has_plan=current_plan is not None)

    graph = build_graph()

    # Run until the race goal is complete (graph exits at END).
    # Restart automatically if the graph crashes — transient API failures
    # shouldn't stop a long-running coaching session.
    while True:
        try:
            graph.invoke(initial_state)
            logger.info("coach_finished", athlete_id=athlete_id, reason="race_goal_complete")
            break
        except Exception as e:
            logger.error("graph_crashed", error=str(e), restarting_in=RESTART_DELAY_SECONDS)
            time.sleep(RESTART_DELAY_SECONDS)
            # Reload state from DB so we pick up where we left off
            initial_state = AgentState(
                athlete_id=athlete_id,
                run_phase=_initial_run_phase(),
                cycle_started_at=datetime.now(),
                current_plan=load_current_plan(athlete_id),
                last_plan=load_last_plan(athlete_id),
            )


def _initial_run_phase() -> RunPhase:
    now = datetime.now()
    if now.weekday() == SUNDAY and now.hour >= WEEKLY_REPLAN_HOUR:
        return RunPhase.weekly_replan
    return RunPhase.post_run


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AI Running Coach agent")
    parser.add_argument("athlete_id", help="ID of the athlete to coach")
    args = parser.parse_args()
    main(args.athlete_id)
