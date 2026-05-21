import os
from datetime import timedelta

import structlog

from agent.state import AgentState, RunPhase
from models.wellness import DailyWellness
from tools.garmin import fetch_garmin_activities, fetch_garmin_activity, fetch_garmin_wellness
from tools.strava import fetch_strava_activities, fetch_strava_activity

logger = structlog.get_logger()

LOOKBACK_DAYS = 14


def _has_garmin_creds(athlete_id: str) -> bool:
    from db.schema import load_credentials
    db = load_credentials(athlete_id)
    if db.get("garmin_email") and db.get("garmin_password"):
        return True
    return bool(os.environ.get("GARMIN_EMAIL") and os.environ.get("GARMIN_PASSWORD"))


def fetch_data(state: AgentState) -> dict:
    athlete = state.athlete
    log = logger.bind(node="fetch_data", athlete_id=athlete.id, run_phase=state.run_phase)
    log.info("node_started")

    errors = list(state.errors)

    # --- Workouts: Garmin preferred, Strava as fallback ---
    use_garmin = _has_garmin_creds(athlete.id)
    use_strava = not use_garmin and bool(athlete.strava_athlete_id)
    source = "garmin" if use_garmin else "strava"

    try:
        if state.run_phase == RunPhase.post_run:
            if use_garmin:
                workouts = [fetch_garmin_activity(athlete.id, state.trigger_workout_id)]
            else:
                workouts = [fetch_strava_activity(athlete.strava_athlete_id, state.trigger_workout_id)]
            log.info("fetched_trigger_workout", source=source, workout_id=state.trigger_workout_id)
        else:
            since = state.cycle_started_at.date() - timedelta(days=LOOKBACK_DAYS)
            if use_garmin:
                workouts = fetch_garmin_activities(athlete.id, since=since)
            else:
                workouts = fetch_strava_activities(athlete.strava_athlete_id, since=since)
            log.info("fetched_recent_workouts", source=source, count=len(workouts), since=since)

    except Exception as e:
        errors.append(f"fetch_data: failed to fetch workouts from {source}: {e}")
        log.error("fetch_failed", source=source, error=str(e))
        return {"errors": errors}

    # --- Wellness: always from Garmin (non-fatal if unavailable) ---
    wellness: list[DailyWellness] = []
    if state.run_phase == RunPhase.weekly_replan and _has_garmin_creds(athlete.id):
        try:
            since = state.cycle_started_at.date() - timedelta(days=LOOKBACK_DAYS)
            wellness = fetch_garmin_wellness(athlete.id, since=since)
            log.info("fetched_wellness", days=len(wellness), since=since)
        except Exception as e:
            log.warning("wellness_fetch_failed", error=str(e))

    updated_athlete = athlete.model_copy(update={"recent_workouts": workouts})

    log.info("node_completed", workouts_loaded=len(workouts), wellness_days=len(wellness))
    return {"athlete": updated_athlete, "wellness": wellness, "errors": errors, "workout_source": source}
