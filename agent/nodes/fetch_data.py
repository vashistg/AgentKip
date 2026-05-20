from datetime import timedelta

import structlog

from agent.state import AgentState, RunPhase
from tools.garmin import fetch_garmin_activities, fetch_garmin_activity
from tools.strava import fetch_strava_activities, fetch_strava_activity

logger = structlog.get_logger()

LOOKBACK_DAYS = 14


def fetch_data(state: AgentState) -> dict:
    athlete = state.athlete
    log = logger.bind(node="fetch_data", athlete_id=athlete.id, run_phase=state.run_phase)
    log.info("node_started")

    errors = list(state.errors)

    # Pick one source — Strava preferred, Garmin as fallback
    use_strava = bool(athlete.strava_athlete_id)
    source = "strava" if use_strava else "garmin"
    log = log.bind(source=source)

    try:
        if state.run_phase == RunPhase.post_run:
            # Fetch only the single workout that triggered this cycle
            if use_strava:
                workouts = [fetch_strava_activity(athlete.strava_athlete_id, state.trigger_workout_id)]
            else:
                workouts = [fetch_garmin_activity(athlete.garmin_user_id, state.trigger_workout_id)]
            log.info("fetched_trigger_workout", workout_id=state.trigger_workout_id)

        else:
            # Weekly replan — fetch last 14 days
            since = state.cycle_started_at.date() - timedelta(days=LOOKBACK_DAYS)
            if use_strava:
                workouts = fetch_strava_activities(athlete.strava_athlete_id, since=since)
            else:
                workouts = fetch_garmin_activities(athlete.garmin_user_id, since=since)
            log.info("fetched_recent_workouts", count=len(workouts), since=since)

    except Exception as e:
        errors.append(f"fetch_data: failed to fetch from {source}: {e}")
        log.error("fetch_failed", error=str(e))
        return {"errors": errors}

    # Athlete is a Pydantic model — use model_copy to get an updated instance
    updated_athlete = athlete.model_copy(update={"recent_workouts": workouts})

    log.info("node_completed", workouts_loaded=len(workouts))
    return {"athlete": updated_athlete, "errors": errors}
