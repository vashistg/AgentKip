"""Tests for agent nodes: analyze, assess, fetch_data."""
import pytest
from datetime import date, datetime, timedelta
from unittest.mock import MagicMock, patch

from conftest import make_athlete, make_workout, seed_athlete
from agent.state import AgentState, AnalysisResult, RunPhase
from agent.nodes.analyze import (
    _check_hr_anomaly,
    _check_weekly_hr_patterns,
    _goal_progress,
    _training_load_trend,
    analyze,
)
from models.plan import GoalProgress, TrainingLoadTrend
from models.workout import ActivityType


# ---------------------------------------------------------------------------
# _training_load_trend
# ---------------------------------------------------------------------------

class TestTrainingLoadTrend:
    def test_overtraining(self):
        notes = []
        result = _training_load_trend(actual=100.0, target=90.0, notes=notes)
        assert result == TrainingLoadTrend.overtraining
        assert any("overtraining" in n for n in notes)

    def test_undertraining(self):
        notes = []
        result = _training_load_trend(actual=30.0, target=90.0, notes=notes)
        assert result == TrainingLoadTrend.undertraining
        assert notes  # a note should be added

    def test_on_track(self):
        notes = []
        result = _training_load_trend(actual=90.0, target=90.0, notes=notes)
        assert result == TrainingLoadTrend.on_track
        assert notes == []

    def test_zero_target_returns_on_track(self):
        # Guard against divide-by-zero when no target set
        result = _training_load_trend(actual=50.0, target=0.0, notes=[])
        assert result == TrainingLoadTrend.on_track

    def test_boundary_exactly_10_percent_over(self):
        # 10% over is overtraining (ratio > 1.1 is the threshold)
        notes = []
        result = _training_load_trend(actual=99.1, target=90.0, notes=notes)
        assert result == TrainingLoadTrend.overtraining

    def test_boundary_exactly_10_percent_under(self):
        # 10% under is undertraining (ratio < 0.9 is the threshold)
        notes = []
        result = _training_load_trend(actual=80.9, target=90.0, notes=notes)
        assert result == TrainingLoadTrend.undertraining


# ---------------------------------------------------------------------------
# _check_weekly_hr_patterns
# ---------------------------------------------------------------------------

class TestWeeklyHrPatterns:
    def test_no_flag_when_hr_normal(self):
        workouts = [
            make_workout(wid="w1", activity_type=ActivityType.easy,
                         avg_heart_rate=140, days_ago=2),
            make_workout(wid="w2", activity_type=ActivityType.easy,
                         avg_heart_rate=145, days_ago=5),
        ]
        notes = []
        _check_weekly_hr_patterns(workouts, max_hr=185, notes=notes)
        assert notes == []

    def test_flag_when_two_easy_runs_elevated(self):
        # 165 / 185 = 89% > 78% ceiling — both anomalous
        workouts = [
            make_workout(wid="w1", activity_type=ActivityType.easy,
                         avg_heart_rate=165, days_ago=2),
            make_workout(wid="w2", activity_type=ActivityType.easy,
                         avg_heart_rate=168, days_ago=5),
        ]
        notes = []
        _check_weekly_hr_patterns(workouts, max_hr=185, notes=notes)
        assert len(notes) == 1
        assert "easy/recovery runs" in notes[0]

    def test_no_flag_for_single_elevated_easy_run(self):
        # Only 1 anomalous run — threshold is ≥2
        workouts = [
            make_workout(wid="w1", activity_type=ActivityType.easy,
                         avg_heart_rate=165, days_ago=2),
            make_workout(wid="w2", activity_type=ActivityType.easy,
                         avg_heart_rate=140, days_ago=5),
        ]
        notes = []
        _check_weekly_hr_patterns(workouts, max_hr=185, notes=notes)
        assert notes == []

    def test_tempo_runs_not_checked(self):
        # High HR on tempo is expected — should not trigger the flag
        workouts = [
            make_workout(wid="w1", activity_type=ActivityType.tempo,
                         avg_heart_rate=175, days_ago=2),
            make_workout(wid="w2", activity_type=ActivityType.tempo,
                         avg_heart_rate=178, days_ago=5),
        ]
        notes = []
        _check_weekly_hr_patterns(workouts, max_hr=185, notes=notes)
        assert notes == []

    def test_skips_workouts_without_hr(self):
        workouts = [
            make_workout(wid="w1", activity_type=ActivityType.easy,
                         avg_heart_rate=None, days_ago=2),
            make_workout(wid="w2", activity_type=ActivityType.easy,
                         avg_heart_rate=None, days_ago=5),
        ]
        notes = []
        _check_weekly_hr_patterns(workouts, max_hr=185, notes=notes)
        assert notes == []

    def test_no_crash_when_max_hr_none(self):
        workouts = [make_workout(wid="w1", avg_heart_rate=175, days_ago=1)]
        notes = []
        _check_weekly_hr_patterns(workouts, max_hr=None, notes=notes)
        assert notes == []


# ---------------------------------------------------------------------------
# _goal_progress
# ---------------------------------------------------------------------------

class TestGoalProgress:
    def test_taper_within_3_weeks(self):
        athlete = make_athlete(race_date=date.today() + timedelta(days=14))
        notes = []
        result = _goal_progress(athlete, actual_volume=20.0, notes=notes)
        assert result == GoalProgress.on_track
        assert any("Taper" in n for n in notes)

    def test_behind_on_volume(self):
        # actual_weekly_avg = 15 km, target = 45 km → well below 90% threshold
        athlete = make_athlete(weekly_target_km=45.0,
                               race_date=date.today() + timedelta(weeks=20))
        notes = []
        result = _goal_progress(athlete, actual_volume=30.0, notes=notes)  # 15 km/week avg
        assert result == GoalProgress.behind
        assert notes

    def test_ahead_on_volume(self):
        # actual_weekly_avg = 50 km, target = 45 km → 5% above target
        athlete = make_athlete(weekly_target_km=45.0,
                               race_date=date.today() + timedelta(weeks=20))
        notes = []
        result = _goal_progress(athlete, actual_volume=100.0, notes=notes)  # 50 km/week avg
        assert result == GoalProgress.ahead

    def test_on_track_when_race_already_passed(self):
        athlete = make_athlete(race_date=date.today() - timedelta(days=1))
        result = _goal_progress(athlete, actual_volume=0.0, notes=[])
        assert result == GoalProgress.on_track


# ---------------------------------------------------------------------------
# _check_hr_anomaly (post-run)
# ---------------------------------------------------------------------------

class TestHrAnomalyPostRun:
    def _mock_log(self):
        return MagicMock()

    def test_easy_run_elevated_hr_flagged(self):
        # 165 / 185 = 89% > 78% ceiling
        workout = make_workout(activity_type=ActivityType.easy,
                               avg_heart_rate=165, max_heart_rate=181)
        notes = []
        _check_hr_anomaly(workout, max_hr=185, notes=notes, log=self._mock_log())
        assert len(notes) == 1
        assert "HR anomaly" in notes[0]

    def test_easy_run_normal_hr_not_flagged(self):
        # 140 / 185 = 76% < 78% ceiling
        workout = make_workout(activity_type=ActivityType.easy,
                               avg_heart_rate=140, max_heart_rate=165)
        notes = []
        _check_hr_anomaly(workout, max_hr=185, notes=notes, log=self._mock_log())
        assert notes == []

    def test_tempo_run_never_flagged(self):
        # Tempo with high HR is expected — function skips non-easy types
        workout = make_workout(activity_type=ActivityType.tempo,
                               avg_heart_rate=178, max_heart_rate=185)
        notes = []
        _check_hr_anomaly(workout, max_hr=185, notes=notes, log=self._mock_log())
        assert notes == []

    def test_no_crash_when_avg_hr_none(self):
        workout = make_workout(activity_type=ActivityType.easy, avg_heart_rate=None)
        notes = []
        _check_hr_anomaly(workout, max_hr=185, notes=notes, log=self._mock_log())
        assert notes == []

    def test_no_crash_when_max_hr_none(self):
        workout = make_workout(activity_type=ActivityType.easy, avg_heart_rate=165)
        notes = []
        _check_hr_anomaly(workout, max_hr=None, notes=notes, log=self._mock_log())
        assert notes == []


# ---------------------------------------------------------------------------
# analyze node — weekly (full state integration, no LLM/DB needed)
# ---------------------------------------------------------------------------

class TestAnalyzeWeekly:
    def _state(self, workouts, weekly_target=45.0, race_date=None):
        athlete = make_athlete(
            max_heart_rate=185,
            weekly_target_km=weekly_target,
            race_date=race_date or (date.today() + timedelta(weeks=20)),
            workouts=workouts,
        )
        return AgentState(
            run_phase=RunPhase.weekly_replan,
            cycle_started_at=datetime.now(),
            athlete_id=athlete.id,
            athlete=athlete,
        )

    def test_undertraining_detected(self):
        # 2-week window: only 20 km actual vs 90 km target
        workouts = [make_workout(wid=f"w{i}", distance_km=5.0, days_ago=i)
                    for i in range(1, 5)]
        result = analyze(self._state(workouts))
        assert result["analysis"].training_load_trend == TrainingLoadTrend.undertraining

    def test_no_workouts_adds_note(self):
        result = analyze(self._state([]))
        notes = result["analysis"].notes
        assert any("No workouts" in n for n in notes)

    def test_errors_carried_forward(self):
        state = self._state([])
        state.errors = ["prior error"]
        result = analyze(state)
        assert "prior error" in result["errors"]


# ---------------------------------------------------------------------------
# assess node — integration with DB
# ---------------------------------------------------------------------------

class TestAssessNode:
    def test_athlete_not_found(self, engine):
        from agent.nodes.assess import assess
        state = AgentState(
            run_phase=RunPhase.weekly_replan,
            cycle_started_at=datetime.now(),
            athlete_id="nonexistent",
        )
        result = assess(state)
        assert result["cleared_to_train"] is False
        assert any("not found" in e for e in result["errors"])

    def test_injury_flags_block_training(self, engine):
        from agent.nodes.assess import assess
        athlete = make_athlete(injury_flags=["left knee pain"])
        seed_athlete(engine, athlete)
        state = AgentState(
            run_phase=RunPhase.weekly_replan,
            cycle_started_at=datetime.now(),
            athlete_id=athlete.id,
        )
        result = assess(state)
        assert result["cleared_to_train"] is False
        assert result["athlete"] is not None
        assert any("injury" in n.lower() for n in result["assessment_notes"])

    def test_no_injury_clears_to_train(self, engine):
        from agent.nodes.assess import assess
        athlete = make_athlete(injury_flags=[])
        seed_athlete(engine, athlete)
        state = AgentState(
            run_phase=RunPhase.weekly_replan,
            cycle_started_at=datetime.now(),
            athlete_id=athlete.id,
        )
        result = assess(state)
        assert result["cleared_to_train"] is True
        assert result["athlete"].id == athlete.id
        assert result["errors"] == []


# ---------------------------------------------------------------------------
# fetch_data node — mocked Strava
# ---------------------------------------------------------------------------

class TestFetchDataNode:
    def _weekly_state(self, athlete):
        return AgentState(
            run_phase=RunPhase.weekly_replan,
            cycle_started_at=datetime.now(),
            athlete_id=athlete.id,
            athlete=athlete,
        )

    def _post_run_state(self, athlete, trigger_id):
        return AgentState(
            run_phase=RunPhase.post_run,
            cycle_started_at=datetime.now(),
            athlete_id=athlete.id,
            athlete=athlete,
            trigger_workout_id=trigger_id,
        )

    def test_weekly_replan_loads_recent_workouts(self):
        from agent.nodes.fetch_data import fetch_data
        athlete = make_athlete()
        workouts = [make_workout(wid=f"w{i}", days_ago=i) for i in range(1, 4)]
        state = self._weekly_state(athlete)
        with patch("agent.nodes.fetch_data.fetch_strava_activities", return_value=workouts):
            result = fetch_data(state)
        assert len(result["athlete"].recent_workouts) == 3
        assert result["errors"] == []

    def test_post_run_loads_single_workout(self):
        from agent.nodes.fetch_data import fetch_data
        athlete = make_athlete()
        trigger = make_workout(wid="trigger_w")
        state = self._post_run_state(athlete, trigger_id="trigger_w")
        with patch("agent.nodes.fetch_data.fetch_strava_activity", return_value=trigger):
            result = fetch_data(state)
        assert len(result["athlete"].recent_workouts) == 1
        assert result["athlete"].recent_workouts[0].id == "trigger_w"

    def test_fetch_error_appended_to_errors(self):
        from agent.nodes.fetch_data import fetch_data
        athlete = make_athlete()
        state = self._weekly_state(athlete)
        with patch("agent.nodes.fetch_data.fetch_strava_activities",
                   side_effect=Exception("connection refused")):
            result = fetch_data(state)
        assert any("fetch_data" in e for e in result["errors"])
