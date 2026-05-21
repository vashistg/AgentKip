from datetime import date

import structlog

from agent.state import AgentState, AnalysisResult, RunPhase
from memory.episodic import EpisodeType, log_episode
from models.plan import GoalProgress, TrainingLoadTrend
from models.workout import ActivityType, Workout

logger = structlog.get_logger()

# Thresholds for training load assessment
OVERTRAINING_RATIO = 1.1   # >10% above target = overtraining
UNDERTRAINING_RATIO = 0.9  # <10% below target = undertraining

# HR anomaly detection for post-run injury check
# Easy/recovery runs should stay below this fraction of max HR
EASY_RUN_HR_CEILING = 0.78

EASY_ACTIVITY_TYPES = {ActivityType.easy, ActivityType.recovery}


def analyze(state: AgentState) -> dict:
    athlete = state.athlete
    log = logger.bind(node="analyze", athlete_id=athlete.id, run_phase=state.run_phase)
    log.info("node_started")

    if state.run_phase == RunPhase.post_run:
        return _analyze_post_run(state, log)
    return _analyze_weekly(state, log)


# --- weekly replan -----------------------------------------------------------

def _analyze_weekly(state: AgentState, log) -> dict:
    athlete = state.athlete
    workouts = athlete.recent_workouts
    errors = list(state.errors)
    notes = []

    actual_volume = sum(w.distance_km for w in workouts if w.distance_km)
    target_volume = athlete.weekly_mileage_target_km * 2  # 2-week window

    trend = _training_load_trend(actual_volume, target_volume, notes)
    goal_progress = _goal_progress(athlete, actual_volume, notes)
    _check_weekly_hr_patterns(workouts, athlete.max_heart_rate, notes)
    _check_wellness(state.wellness, athlete.resting_heart_rate, notes)

    if len(workouts) == 0:
        notes.append("No workouts found in the last 14 days")

    analysis = AnalysisResult(
        training_load_trend=trend,
        goal_progress=goal_progress,
        two_week_volume_km=round(actual_volume, 2),
        target_two_week_volume_km=round(target_volume, 2),
        notes=notes,
    )

    log.info("node_completed", trend=trend, goal_progress=goal_progress,
             actual_km=actual_volume, target_km=target_volume)
    return {"analysis": analysis, "errors": errors}


def _training_load_trend(actual: float, target: float, notes: list[str]) -> TrainingLoadTrend:
    if target == 0:
        return TrainingLoadTrend.on_track
    ratio = actual / target
    if ratio > OVERTRAINING_RATIO:
        notes.append(f"Volume {actual:.1f} km exceeds 2-week target of {target:.1f} km — risk of overtraining")
        return TrainingLoadTrend.overtraining
    if ratio < UNDERTRAINING_RATIO:
        notes.append(f"Volume {actual:.1f} km is below 2-week target of {target:.1f} km")
        return TrainingLoadTrend.undertraining
    return TrainingLoadTrend.on_track


def _goal_progress(athlete, actual_volume: float, notes: list[str]) -> GoalProgress:
    goal = athlete.goal
    days_to_race = (goal.race_date - date.today()).days

    if days_to_race <= 0:
        return GoalProgress.on_track  # race complete — graph will exit at wait

    weeks_to_race = days_to_race / 7
    target_weekly = athlete.weekly_mileage_target_km
    actual_weekly_avg = actual_volume / 2  # over 2 weeks

    # In the last 3 weeks expect a taper — lower volume is correct
    if weeks_to_race <= 3:
        notes.append(f"Taper phase — {days_to_race} days to race")
        return GoalProgress.on_track

    # Check if pace-based goal is set and athlete has recent running data
    if goal.target_finish_seconds and goal.race_distance_km:
        target_pace = goal.target_finish_seconds / 60 / goal.race_distance_km  # min/km
        recent_runs = [w for w in athlete.recent_workouts
                       if w.activity_type in {ActivityType.tempo, ActivityType.long_run}
                       and w.pace_min_per_km is not None]
        if recent_runs:
            avg_pace = sum(w.pace_min_per_km for w in recent_runs) / len(recent_runs)
            if avg_pace > target_pace * 1.05:
                notes.append(f"Current pace {avg_pace:.1f} min/km is slower than race target {target_pace:.1f} min/km")
                return GoalProgress.behind
            if avg_pace <= target_pace:
                notes.append(f"Pace tracking ahead of race target {target_pace:.1f} min/km")
                return GoalProgress.ahead

    # Fall back to volume-based assessment
    if actual_weekly_avg >= target_weekly * 1.05:
        return GoalProgress.ahead
    if actual_weekly_avg < target_weekly * UNDERTRAINING_RATIO:
        notes.append(f"Weekly average {actual_weekly_avg:.1f} km below target {target_weekly:.1f} km "
                     f"with {weeks_to_race:.0f} weeks to race")
        return GoalProgress.behind
    return GoalProgress.on_track


def _check_weekly_hr_patterns(
    workouts: list[Workout], max_hr: int | None, notes: list[str]
) -> None:
    """Flag if multiple easy/recovery runs had elevated HR — sign of accumulated fatigue."""
    if max_hr is None:
        return
    anomalous = [
        w for w in workouts
        if w.activity_type in EASY_ACTIVITY_TYPES
        and w.avg_heart_rate is not None
        and w.avg_heart_rate / max_hr > EASY_RUN_HR_CEILING
    ]
    if len(anomalous) >= 2:
        avg_fraction = sum(w.avg_heart_rate / max_hr for w in anomalous) / len(anomalous)
        notes.append(
            f"Elevated HR on {len(anomalous)} easy/recovery runs "
            f"(avg {avg_fraction:.0%} of max HR) — possible fatigue, illness, or overreach"
        )


def _check_wellness(wellness, baseline_rhr: int | None, notes: list[str]) -> None:
    """Add notes from Garmin wellness data: RHR elevation, stress, sleep debt, cadence."""
    if not wellness:
        return

    # RHR: flag if recent average is more than 5 bpm above athlete baseline
    rhr_values = [w.resting_heart_rate for w in wellness if w.resting_heart_rate]
    if rhr_values and baseline_rhr:
        avg_rhr = sum(rhr_values) / len(rhr_values)
        if avg_rhr > baseline_rhr + 5:
            notes.append(
                f"Garmin RHR elevated: avg {avg_rhr:.0f} bpm over {len(rhr_values)} days "
                f"(baseline {baseline_rhr} bpm) — may indicate fatigue or illness"
            )
        else:
            notes.append(
                f"Garmin RHR stable: avg {avg_rhr:.0f} bpm over {len(rhr_values)} days "
                f"(baseline {baseline_rhr} bpm)"
            )

    # Stress: flag if 2+ days were high (≥51), or any single day very-high (≥76)
    stress_values = [w.avg_stress for w in wellness if w.avg_stress is not None]
    if stress_values:
        high_stress_days = sum(1 for s in stress_values if s >= 51)
        peak_stress = max(stress_values)
        avg_stress = sum(stress_values) / len(stress_values)
        if peak_stress >= 76 or high_stress_days >= 2:
            notes.append(
                f"Garmin stress elevated: avg {avg_stress:.0f}/100, peak {peak_stress}/100 "
                f"({high_stress_days} high-stress day(s) of {len(stress_values)}) "
                f"— may suppress recovery and elevate running HR"
            )
        else:
            notes.append(
                f"Garmin stress normal: avg {avg_stress:.0f}/100 across {len(stress_values)} days"
            )

    # Sleep: flag if consistently short
    sleep_values = [w.sleep_hours for w in wellness if w.sleep_hours]
    if sleep_values:
        avg_sleep = sum(sleep_values) / len(sleep_values)
        if avg_sleep < 7.0:
            notes.append(
                f"Garmin sleep deficit: avg {avg_sleep:.1f}h/night over {len(sleep_values)} nights "
                f"— recovery and adaptation will be impaired"
            )
        else:
            notes.append(
                f"Garmin sleep adequate: avg {avg_sleep:.1f}h/night over {len(sleep_values)} nights"
            )

    # Cadence: flag if below optimal range
    cadence_values = [w.avg_cadence_spm for w in wellness if w.avg_cadence_spm]
    if cadence_values:
        avg_cadence = sum(cadence_values) / len(cadence_values)
        if avg_cadence < 170:
            notes.append(
                f"Garmin cadence {avg_cadence:.0f} spm — below optimal 170–180 spm range; "
                f"consider shorter stride drills"
            )
        else:
            notes.append(f"Garmin cadence {avg_cadence:.0f} spm — within optimal range")


# --- post-run check ----------------------------------------------------------

def _analyze_post_run(state: AgentState, log) -> dict:
    athlete = state.athlete
    errors = list(state.errors)
    notes = []

    trigger_workout = next(
        (w for w in athlete.recent_workouts if w.id == state.trigger_workout_id),
        None,
    )

    if trigger_workout is None:
        errors.append(f"analyze: trigger workout {state.trigger_workout_id!r} not found in recent workouts")
        log.error("trigger_workout_missing")
        return {"errors": errors}

    _check_hr_anomaly(trigger_workout, athlete.max_heart_rate, notes, log)

    if notes:
        log_episode(
            athlete_id=athlete.id,
            episode_type=EpisodeType.injury_flagged,
            data={
                "workout_id": trigger_workout.id,
                "workout_date": trigger_workout.date.isoformat(),
                "activity_type": trigger_workout.activity_type.value,
                "avg_hr": trigger_workout.avg_heart_rate,
                "notes": notes,
            },
        )

    # Carry forward last analysis values — post-run doesn't recompute full volume
    prior = state.analysis
    analysis = AnalysisResult(
        training_load_trend=prior.training_load_trend if prior else TrainingLoadTrend.on_track,
        goal_progress=prior.goal_progress if prior else GoalProgress.on_track,
        two_week_volume_km=prior.two_week_volume_km if prior else 0,
        target_two_week_volume_km=prior.target_two_week_volume_km if prior else 0,
        notes=notes,
    )

    log.info("node_completed", notes=notes)
    return {"analysis": analysis, "errors": errors}


def _check_hr_anomaly(workout: Workout, max_hr: int | None, notes: list[str], log) -> None:
    if workout.activity_type not in EASY_ACTIVITY_TYPES:
        return
    if workout.avg_heart_rate is None or max_hr is None:
        return

    hr_fraction = workout.avg_heart_rate / max_hr
    if hr_fraction > EASY_RUN_HR_CEILING:
        notes.append(
            f"HR anomaly on {workout.activity_type} run: "
            f"{workout.avg_heart_rate} bpm ({hr_fraction:.0%} of max) — possible injury or illness"
        )
        log.warning("hr_anomaly_detected", avg_hr=workout.avg_heart_rate,
                    max_hr=max_hr, fraction=round(hr_fraction, 2))
