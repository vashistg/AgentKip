import os
from datetime import date, datetime, timedelta
from typing import Optional

import structlog
from garminconnect import Garmin

from models.wellness import DailyWellness
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


def _client(athlete_id: str) -> Garmin:
    from db.schema import load_credentials
    db_creds = load_credentials(athlete_id)
    email    = db_creds.get("garmin_email")    or os.environ.get("GARMIN_EMAIL")
    password = db_creds.get("garmin_password") or os.environ.get("GARMIN_PASSWORD")
    if not email or not password:
        raise RuntimeError(f"No Garmin credentials found for athlete {athlete_id}")
    g = Garmin(email, password)
    g.login()
    return g


def fetch_garmin_wellness(user_id: str, since: date) -> list[DailyWellness]:
    """
    Fetch daily wellness metrics from Garmin for each day since `since`.
    Returns one DailyWellness per day, skipping days with no data.
    """
    log = logger.bind(tool="garmin.fetch_wellness", user_id=user_id, since=since)
    g = _client(user_id)

    # Cadence: one batch call for all running activities in range
    cadence_by_date: dict[date, float] = {}
    try:
        activities = g.get_activities_by_date(since.isoformat(), date.today().isoformat(), "running")
        for a in activities:
            cadence = a.get("averageRunningCadenceInStepsPerMinute")
            if cadence:
                act_date = datetime.fromisoformat(a["startTimeLocal"]).date()
                cadence_by_date[act_date] = float(cadence)
    except Exception as e:
        log.warning("cadence_fetch_failed", error=str(e))

    results: list[DailyWellness] = []
    current = since
    today = date.today()

    while current <= today:
        day_str = current.isoformat()
        rhr = stress = sleep_s = deep_s = rem_s = None

        # RHR + stress — both in get_stats
        try:
            stats = g.get_stats(day_str)
            rhr    = stats.get("restingHeartRate")
            stress = stats.get("averageStressLevel")
            if stress is not None and stress < 0:
                stress = None  # Garmin returns -1 when no data
        except Exception:
            pass

        # Sleep
        try:
            sleep = g.get_sleep_data(day_str)
            dto = sleep.get("dailySleepDTO", {})
            sleep_s = dto.get("sleepTimeSeconds")
            deep_s  = dto.get("deepSleepSeconds")
            rem_s   = dto.get("remSleepSeconds")
        except Exception:
            pass

        # Only include days where we got at least one metric
        if any(v is not None for v in [rhr, stress, sleep_s, cadence_by_date.get(current)]):
            results.append(DailyWellness(
                date=current,
                resting_heart_rate=int(rhr) if rhr else None,
                avg_stress=int(stress) if stress is not None else None,
                sleep_seconds=int(sleep_s) if sleep_s else None,
                deep_sleep_seconds=int(deep_s) if deep_s else None,
                rem_sleep_seconds=int(rem_s) if rem_s else None,
                avg_cadence_spm=cadence_by_date.get(current),
            ))

        current += timedelta(days=1)

    log.info("fetched", days=len(results), since=since)
    return results


def fetch_garmin_activities(user_id: str, since: date) -> list[Workout]:
    """Fetch all activities logged on or after `since`."""
    log = logger.bind(tool="garmin.fetch_activities", user_id=user_id, since=since)
    g = _client(user_id)
    raw = g.get_activities_by_date(since.isoformat(), date.today().isoformat())
    log.info("fetched", count=len(raw))
    return [w for a in raw if (w := _map_activity(a)) is not None]


def fetch_garmin_activity(user_id: str, activity_id: str) -> Workout:
    """Fetch a single activity by ID."""
    log = logger.bind(tool="garmin.fetch_activity", activity_id=activity_id)
    g = _client(user_id)
    raw = g.get_activity(activity_id)
    workout = _map_activity(raw)
    if workout is None:
        raise ValueError(f"Could not map Garmin activity {activity_id} to Workout")
    log.info("fetched", activity_type=workout.activity_type)
    return workout


def poll_new_activity(user_id: str, since: datetime) -> Optional[str]:
    """Return the ID of the most recent activity uploaded after `since`, or None."""
    g = _client(user_id)
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
