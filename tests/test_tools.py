"""Tests for tools layer: plan save/load and weather models."""
import pytest
from datetime import date, datetime, timedelta

import pydantic
import pytest

from conftest import make_athlete, make_workout, seed_athlete
from models.plan import (
    GoalProgress,
    Plan,
    PlannedActivityType,
    PlannedWorkout,
    PlanReasoning,
    TrainingLoadTrend,
)
from models.weather import WeatherCondition, WeatherSeverity
from tools.weather import DailyForecast, WeeklyForecast


# ---------------------------------------------------------------------------
# WeatherCondition.severity
# ---------------------------------------------------------------------------

class TestWeatherSeverity:
    def _condition(self, feels_like_c: float) -> WeatherCondition:
        return WeatherCondition(
            temperature_c=feels_like_c - 2,
            feels_like_c=feels_like_c,
            humidity_pct=60,
            wind_speed_kmh=10,
            description="test",
        )

    def test_normal_below_28(self):
        assert self._condition(25.0).severity == WeatherSeverity.normal

    def test_warm_28_to_32(self):
        assert self._condition(30.0).severity == WeatherSeverity.warm

    def test_hot_at_32(self):
        assert self._condition(32.0).severity == WeatherSeverity.hot

    def test_hot_at_34(self):
        assert self._condition(34.0).severity == WeatherSeverity.hot

    def test_very_hot_at_38(self):
        assert self._condition(38.0).severity == WeatherSeverity.very_hot

    def test_extreme_at_42(self):
        assert self._condition(42.0).severity == WeatherSeverity.extreme

    def test_extreme_above_42(self):
        assert self._condition(45.0).severity == WeatherSeverity.extreme

    def test_uses_feels_like_not_temperature(self):
        # temp=25 (would be normal) but feels_like=35 (should be hot)
        cond = WeatherCondition(
            temperature_c=25.0, feels_like_c=35.0,
            humidity_pct=90, wind_speed_kmh=5, description="humid",
        )
        assert cond.severity == WeatherSeverity.hot


# ---------------------------------------------------------------------------
# WeatherCondition validation bounds
# ---------------------------------------------------------------------------

class TestWeatherConditionValidation:
    def test_rejects_temperature_above_60(self):
        with pytest.raises(pydantic.ValidationError):
            WeatherCondition(temperature_c=65, feels_like_c=65,
                             humidity_pct=50, wind_speed_kmh=10, description="x")

    def test_rejects_humidity_above_100(self):
        with pytest.raises(pydantic.ValidationError):
            WeatherCondition(temperature_c=25, feels_like_c=25,
                             humidity_pct=101, wind_speed_kmh=10, description="x")


# ---------------------------------------------------------------------------
# WeeklyForecast.for_date
# ---------------------------------------------------------------------------

class TestWeeklyForecastForDate:
    def _forecast(self, days: int = 3) -> WeeklyForecast:
        today = date.today()
        return WeeklyForecast(
            city_lat=12.97, city_lng=77.59,
            forecasts=[
                DailyForecast(
                    date=today + timedelta(days=i),
                    condition=WeatherCondition(
                        temperature_c=28 + i, feels_like_c=30 + i,
                        humidity_pct=65, wind_speed_kmh=10,
                        description=f"day {i}",
                    ),
                )
                for i in range(days)
            ],
        )

    def test_returns_condition_for_matching_date(self):
        forecast = self._forecast()
        result = forecast.for_date(date.today())
        assert result is not None
        assert result.temperature_c == 28.0

    def test_returns_none_for_missing_date(self):
        forecast = self._forecast()
        result = forecast.for_date(date.today() + timedelta(days=99))
        assert result is None

    def test_returns_correct_day(self):
        forecast = self._forecast(days=5)
        day3 = forecast.for_date(date.today() + timedelta(days=3))
        assert day3 is not None
        assert day3.temperature_c == 31.0  # 28 + 3


# ---------------------------------------------------------------------------
# Plan save / load roundtrip
# ---------------------------------------------------------------------------

def _make_plan(week_start: date | None = None) -> Plan:
    start = week_start or (date.today() + timedelta(days=7))
    workouts = [
        PlannedWorkout(
            date=start + timedelta(days=i),
            activity_type=PlannedActivityType.easy,
            target_distance_km=10.0,
            target_hr_zone=2,
            notes=f"Day {i}",
        )
        for i in range(7)
    ]
    return Plan(
        id="test_plan_001",
        created_at=datetime.now(),
        week_start_date=start,
        workouts=workouts,
        reasoning=PlanReasoning(
            training_load_trend=TrainingLoadTrend.on_track,
            goal_progress=GoalProgress.on_track,
            two_week_volume_km=80.0,
            target_two_week_volume_km=90.0,
            changes_from_last_week=[],
            summary="Test plan",
        ),
    )


class TestPlanSaveLoad:
    def test_save_and_load_current_plan(self, engine):
        from tools.plan import load_current_plan, save_plan
        plan = _make_plan()
        save_plan(plan, athlete_id="test_athlete")
        loaded = load_current_plan("test_athlete")
        assert loaded is not None
        assert loaded.id == plan.id
        assert loaded.total_volume_km == pytest.approx(70.0)  # 7 × 10 km

    def test_loaded_plan_reasoning_survives_roundtrip(self, engine):
        from tools.plan import load_current_plan, save_plan
        plan = _make_plan()
        save_plan(plan, athlete_id="test_athlete")
        loaded = load_current_plan("test_athlete")
        assert loaded.reasoning.training_load_trend == TrainingLoadTrend.on_track
        assert loaded.reasoning.summary == "Test plan"

    def test_save_demotes_previous_current_plan(self, engine):
        from tools.plan import load_current_plan, load_last_plan, save_plan
        plan_a = _make_plan(week_start=date.today() + timedelta(days=7))
        plan_a = plan_a.model_copy(update={"id": "plan_a"})
        save_plan(plan_a, athlete_id="test_athlete")

        plan_b = _make_plan(week_start=date.today() + timedelta(days=14))
        plan_b = plan_b.model_copy(update={"id": "plan_b"})
        save_plan(plan_b, athlete_id="test_athlete")

        current = load_current_plan("test_athlete")
        last    = load_last_plan("test_athlete")

        assert current.id == "plan_b"
        assert last.id == "plan_a"

    def test_load_current_returns_none_when_no_plan(self, engine):
        from tools.plan import load_current_plan
        assert load_current_plan("unknown_athlete") is None

    def test_load_last_returns_none_when_only_one_plan(self, engine):
        from tools.plan import load_current_plan, load_last_plan, save_plan
        save_plan(_make_plan(), athlete_id="test_athlete")
        assert load_last_plan("test_athlete") is None

    def test_plan_workouts_survive_roundtrip(self, engine):
        from tools.plan import load_current_plan, save_plan
        plan = _make_plan()
        save_plan(plan, athlete_id="test_athlete")
        loaded = load_current_plan("test_athlete")
        assert len(loaded.workouts) == 7
        assert loaded.workouts[0].activity_type == PlannedActivityType.easy
        assert loaded.workouts[0].target_hr_zone == 2
