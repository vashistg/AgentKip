import os
from datetime import date, datetime
from typing import Optional

import structlog
from garminconnect import Garmin

from models.workout import ActivityType, DataSource, Workout

logger = structlog.get_logger()

# Garmin activityType.typeKey values
_ACTIVITY_TYPE_MAP = {
    "running":          ActivityType.easy,
    "trail_running":    ActivityType.easy,
    "track_running":    ActivityType.interval,
    "strength_training": ActivityType.strength,
    "fitness_equipment": ActivityType.strength,
    "swimming":         ActivityType.cross_training,
    "cycling":          ActivityType.cross_training,
    "walking":          ActivityType.recovery,
    "hiking":           ActivityType.recovery,
}


def _client() -> Garmin:
    g = Garmin(os.environ["GARMIN_EMAIL"], os.environ["GARMIN_PASSWORD"])
    g.login()
    return g


def fetch_garmin_activities(user_id: str, since: date) -> list[Workout]:
    """Fetch all activities logged on or after `since`."""
    log = logger.bind(tool="garmin.fetch_activities", user_id=user_id, since=since)
    g = _client()
    raw = g.get_activities_by_date(since.isoformat(), date.today().isoformat())
    log.info("fetched", count=len(raw))
    return [w for a in raw if (w := _map_activity(a)) is not None]


def fetch_garmin_activity(user_id: str, activity_id: str) -> Workout:
    """Fetch a single activity by ID."""
    log = logger.bind(tool="garmin.fetch_activity", activity_id=activity_id)
    g = _client()
    raw = g.get_activity(activity_id)
    workout = _map_activity(raw)
    if workout is None:
        raise ValueError(f"Could not map Garmin activity {activity_id} to Workout")
    log.info("fetched", activity_type=workout.activity_type)
    return workout


def poll_new_activity(user_id: str, since: datetime) -> Optional[str]:
    """Return the ID of the most recent activity uploaded after `since`, or None."""
    g = _client()
    raw = g.get_activities_by_date(since.date().isoformat(), date.today().isoformat())
    recent = [a for a in raw if _parse_start_time(a) > since]
    if recent:
        # Most recent first
        recent.sort(key=lambda a: _parse_start_time(a), reverse=True)
        return str(recent[0].get("activityId"))
    return None


# ---------------------------------------------------------------------------
# Internal mapper
# ---------------------------------------------------------------------------

def _map_activity(raw: dict) -> Optional[Workout]:
    """Convert a raw Garmin activity dict to our Workout model."""
    try:
        type_key = raw.get("activityType", {}).get("typeKey", "running")
        activity_type = _ACTIVITY_TYPE_MAP.get(type_key, ActivityType.cross_training)

        distance_m = raw.get("distance")
        distance_km = distance_m / 1000 if distance_m else None

        avg_hr = raw.get("averageHR")
        max_hr = raw.get("maxHR")
        elevation = raw.get("elevationGain")

        start_time = _parse_start_time(raw)

        return Workout(
            id=str(raw["activityId"]),
            date=start_time.date(),
            activity_type=activity_type,
            source=DataSource.garmin,
            distance_km=float(distance_km) if distance_km else None,
            duration_seconds=int(raw["duration"]),
            avg_heart_rate=int(avg_hr) if avg_hr else None,
            max_heart_rate=int(max_hr) if max_hr else None,
            elevation_gain_m=float(elevation) if elevation else None,
        )
    except Exception:
        logger.warning("skipped_activity", activity_id=raw.get("activityId", "unknown"))
        return None


def _parse_start_time(raw: dict) -> datetime:
    return datetime.fromisoformat(raw["startTimeLocal"].replace("Z", "+00:00"))
