import time
from datetime import datetime, timedelta

import structlog

from agent.state import AgentState, RunPhase
from tools.strava import poll_new_activity
from tools.garmin import poll_new_activity as poll_garmin_activity

logger = structlog.get_logger()

POLL_INTERVAL_SECONDS = 30 * 60   # check for new workouts every 30 minutes
WEEKLY_REPLAN_HOUR = 20            # trigger weekly replan at 8pm every Sunday
SUNDAY = 6


def wait(state: AgentState) -> dict:
    athlete = state.athlete
    log = logger.bind(node="wait", athlete_id=athlete.id if athlete else "unknown",
                      completed_phase=state.run_phase)
    log.info("node_started")

    # If we just finished a weekly replan, sleep through the rest of Sunday
    # so we don't immediately re-trigger another replan
    if state.run_phase == RunPhase.weekly_replan:
        monday_6am = (datetime.now() + timedelta(days=1)).replace(hour=6, minute=0, second=0, microsecond=0)
        sleep_seconds = max(0, (monday_6am - datetime.now()).total_seconds())
        log.info("sleeping_until_monday", until=monday_6am.isoformat(), seconds=sleep_seconds)
        time.sleep(sleep_seconds)

    # Poll loop — runs until a new workout is detected or Sunday replan window arrives
    while True:
        now = datetime.now()

        # Sunday evening → trigger weekly replan for the coming week
        if now.weekday() == SUNDAY and now.hour >= WEEKLY_REPLAN_HOUR:
            log.info("weekly_replan_triggered")
            return _reset(state, run_phase=RunPhase.weekly_replan, promote_plan=True)

        # Check for a new workout from the athlete's connected account
        new_workout_id = _poll_for_new_activity(state)
        if new_workout_id:
            log.info("post_run_triggered", workout_id=new_workout_id)
            return _reset(state, run_phase=RunPhase.post_run, trigger_workout_id=new_workout_id)

        log.debug("no_new_activity", next_check_in_seconds=POLL_INTERVAL_SECONDS)
        time.sleep(POLL_INTERVAL_SECONDS)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _poll_for_new_activity(state: AgentState) -> str | None:
    """Returns a workout ID if a new activity has been uploaded since this cycle started."""
    athlete = state.athlete
    if not athlete:
        return None

    since = state.cycle_started_at

    # Strava preferred, Garmin as fallback — same rule as fetch_data
    if athlete.strava_athlete_id:
        return poll_new_activity(athlete.strava_athlete_id, since=since)
    if athlete.garmin_user_id:
        return poll_garmin_activity(athlete.garmin_user_id, since=since)
    return None


def _reset(
    state: AgentState,
    run_phase: RunPhase,
    trigger_workout_id: str | None = None,
    promote_plan: bool = False,
) -> dict:
    """Build the state update for the start of the next cycle."""
    return {
        "run_phase": run_phase,
        "cycle_started_at": datetime.now(),
        "trigger_workout_id": trigger_workout_id,
        # Promote current → last only before a weekly replan so adapt_plan can diff
        "last_plan": state.current_plan if promote_plan else state.last_plan,
        # Clear per-cycle fields — assess/fetch_data/analyze will repopulate them
        "athlete": None,
        "cleared_to_train": True,
        "assessment_notes": [],
        "analysis": None,
        "errors": [],
    }
