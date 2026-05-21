import os
import re
import time
from datetime import date, datetime
from typing import Optional

import structlog
from stravalib import Client
from stravalib.model import DetailedActivity, SummaryActivity

from models.workout import ActivityType, DataSource, Workout

logger = structlog.get_logger()

# Strava workout_type field values
_WORKOUT_TYPE_MAP = {
    1: ActivityType.race,
    2: ActivityType.long_run,
    3: ActivityType.interval,
}

# Strava sport_type field values for non-running activities
_SPORT_TYPE_MAP = {
    "WeightTraining": ActivityType.strength,
    "Crossfit":       ActivityType.strength,
    "Swim":           ActivityType.cross_training,
    "Ride":           ActivityType.cross_training,
    "VirtualRide":    ActivityType.cross_training,
    "Walk":           ActivityType.recovery,
    "Hike":           ActivityType.recovery,
}

# Refresh the token this many seconds before it actually expires
_REFRESH_BUFFER_SECONDS = 5 * 60  # 5 minutes


# ---------------------------------------------------------------------------
# Token management
# ---------------------------------------------------------------------------

def _client(athlete_id: str) -> Client:
    c = Client()
    c.access_token = _valid_access_token(athlete_id)
    return c


def _load_strava_creds(athlete_id: str) -> dict:
    """Load Strava credentials from DB; fall back to env vars (single-user mode)."""
    from db.schema import load_credentials
    db_creds = load_credentials(athlete_id)
    if db_creds.get("strava_access_token"):
        return db_creds
    return {
        "strava_access_token":     os.environ.get("STRAVA_ACCESS_TOKEN"),
        "strava_refresh_token":    os.environ.get("STRAVA_REFRESH_TOKEN"),
        "strava_token_expires_at": int(os.environ.get("STRAVA_TOKEN_EXPIRES_AT", "0")),
        "_source": "env",
    }


def _valid_access_token(athlete_id: str) -> str:
    """Return a non-expired access token, refreshing if needed."""
    creds = _load_strava_creds(athlete_id)
    expires_at = creds.get("strava_token_expires_at") or 0
    if time.time() < expires_at - _REFRESH_BUFFER_SECONDS:
        return creds["strava_access_token"]

    logger.info("strava_token_refreshing", reason="expired_or_missing_expiry", athlete_id=athlete_id)
    return _refresh_and_persist(athlete_id, creds)


def _refresh_and_persist(athlete_id: str, creds: dict) -> str:
    """Exchange the refresh token for new credentials and persist them."""
    c = Client()
    resp = c.refresh_access_token(
        client_id=os.environ["STRAVA_CLIENT_ID"],
        client_secret=os.environ["STRAVA_CLIENT_SECRET"],
        refresh_token=creds["strava_refresh_token"],
    )

    access_token  = resp["access_token"]
    refresh_token = resp["refresh_token"]
    expires_at    = resp["expires_at"]

    # Always update in-process env so subsequent calls in the same cycle use the new token
    os.environ["STRAVA_ACCESS_TOKEN"]     = access_token
    os.environ["STRAVA_REFRESH_TOKEN"]    = refresh_token
    os.environ["STRAVA_TOKEN_EXPIRES_AT"] = str(expires_at)

    if creds.get("_source") == "env":
        # Single-user mode: persist to .env file
        _patch_env_file({
            "STRAVA_ACCESS_TOKEN":     access_token,
            "STRAVA_REFRESH_TOKEN":    refresh_token,
            "STRAVA_TOKEN_EXPIRES_AT": str(expires_at),
        })
    else:
        # Multi-user mode: persist refreshed tokens to DB
        from db.schema import save_credentials
        save_credentials(
            athlete_id=athlete_id,
            strava_access_token=access_token,
            strava_refresh_token=refresh_token,
            strava_token_expires_at=int(expires_at),
        )

    logger.info("strava_token_refreshed", expires_at=expires_at, athlete_id=athlete_id)
    return access_token


def _patch_env_file(updates: dict[str, str]) -> None:
    """Update specific keys in the .env file without touching anything else."""
    env_path = _find_env_file()
    if not env_path:
        return

    with open(env_path) as f:
        content = f.read()

    for key, value in updates.items():
        if re.search(rf"^{key}=", content, re.MULTILINE):
            content = re.sub(rf"^{key}=.*$", f"{key}={value}", content, flags=re.MULTILINE)
        else:
            content = content.rstrip("\n") + f"\n{key}={value}\n"

    with open(env_path, "w") as f:
        f.write(content)


def _find_env_file() -> Optional[str]:
    """Walk up from this file to find the project-root .env."""
    directory = os.path.dirname(os.path.abspath(__file__))
    for _ in range(4):
        candidate = os.path.join(directory, ".env")
        if os.path.exists(candidate):
            return candidate
        directory = os.path.dirname(directory)
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_strava_activities(athlete_id: str, since: date) -> list[Workout]:
    """Fetch all activities for athlete_id logged on or after `since`."""
    log = logger.bind(tool="strava.fetch_activities", athlete_id=athlete_id, since=since)
    c = _client(athlete_id)
    since_dt = datetime(since.year, since.month, since.day)
    activities = list(c.get_activities(after=since_dt))
    log.info("fetched", count=len(activities))
    return [w for a in activities if (w := _map_activity(a)) is not None]


def fetch_strava_activity(athlete_id: str, activity_id: str) -> Workout:
    """Fetch a single activity by ID."""
    log = logger.bind(tool="strava.fetch_activity", activity_id=activity_id)
    c = _client(athlete_id)
    activity = c.get_activity(int(activity_id))
    workout = _map_activity(activity)
    if workout is None:
        raise ValueError(f"Could not map Strava activity {activity_id} to Workout")
    log.info("fetched", activity_type=workout.activity_type)
    return workout


def poll_new_activity(athlete_id: str, since: datetime) -> Optional[str]:
    """Return the ID of the most recent activity uploaded after `since`, or None."""
    c = _client(athlete_id)
    activities = list(c.get_activities(after=since, limit=1))
    if activities:
        return str(activities[0].id)
    return None


# ---------------------------------------------------------------------------
# Internal mapper
# ---------------------------------------------------------------------------

def _map_activity(activity: SummaryActivity | DetailedActivity) -> Optional[Workout]:
    """Convert a Strava Activity to our Workout model. Returns None for unsupported types."""
    try:
        activity_type = _resolve_activity_type(activity)
        distance_km = float(activity.distance) / 1000 if activity.distance else None
        avg_hr = int(activity.average_heartrate) if activity.average_heartrate else None
        max_hr = int(activity.max_heartrate) if activity.max_heartrate else None
        elevation = float(activity.total_elevation_gain) if activity.total_elevation_gain else None

        # stravalib v2.4: elapsed_time is Duration (not timedelta) — cast directly to int
        duration_s = int(activity.elapsed_time)

        return Workout(
            id=str(activity.id),
            date=activity.start_date.date(),
            activity_type=activity_type,
            source=DataSource.strava,
            distance_km=distance_km,
            duration_seconds=duration_s,
            avg_heart_rate=avg_hr,
            max_heart_rate=max_hr,
            elevation_gain_m=elevation,
        )
    except Exception:
        logger.warning("skipped_activity", activity_id=getattr(activity, "id", "unknown"))
        return None


def _resolve_activity_type(activity: SummaryActivity | DetailedActivity) -> ActivityType:
    sport_obj = getattr(activity, "sport_type", None) or getattr(activity, "type", "")
    # stravalib v2.4 wraps sport_type in RelaxedSportType (a Pydantic RootModel)
    sport = getattr(sport_obj, "root", str(sport_obj))

    # Non-running sport types take priority
    if sport in _SPORT_TYPE_MAP:
        return _SPORT_TYPE_MAP[sport]

    if "Run" in sport:
        # Use Strava's workout_type hint if available
        workout_type = getattr(activity, "workout_type", 0) or 0
        return _WORKOUT_TYPE_MAP.get(workout_type, ActivityType.easy)

    return ActivityType.cross_training
