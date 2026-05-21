import json
import uuid
from datetime import date, datetime, timedelta
from typing import Any

import anthropic
import structlog

from agent.state import AgentState, RunPhase
from models.plan import (
    AdaptationStatus,
    GoalProgress,
    IntraWeekAdaptation,
    Plan,
    PlannedActivityType,
    PlannedWorkout,
    PlanReasoning,
    TrainingLoadTrend,
    WorkoutChange,
)
from memory.episodic import EpisodeType, log_episode
from memory.semantic import retrieve_relevant, store_observation
from tools.plan import save_plan
from tools.weather import get_forecast

logger = structlog.get_logger()

_client = anthropic.Anthropic()
MODEL = "claude-sonnet-4-6"
MAX_TOOL_ITERATIONS = 5

# ---------------------------------------------------------------------------
# Tool schemas exposed to the LLM
# ---------------------------------------------------------------------------

_GET_WEATHER_TOOL = {
    "name": "get_weather_forecast",
    "description": (
        "Get the 7-day weather forecast for a location. "
        "Call this to check if upcoming workouts need pace or intensity adjustments."
    ),
    "input_schema": {
        "type": "object",
        "required": ["latitude", "longitude", "city"],
        "properties": {
            "latitude": {"type": "number"},
            "longitude": {"type": "number"},
            "city": {"type": "string"},
        },
    },
}

_CREATE_PLAN_TOOL = {
    "name": "create_weekly_plan",
    "description": "Output the final weekly training plan once you have all information needed.",
    "input_schema": {
        "type": "object",
        "required": ["workouts", "reasoning"],
        "properties": {
            "workouts": {
                "type": "array",
                "description": "7 workouts, one per day Mon–Sun: 3 runs, 3 strength, 1 rest",
                "items": {
                    "type": "object",
                    "required": ["date", "activity_type"],
                    "properties": {
                        "date": {"type": "string", "description": "YYYY-MM-DD"},
                        "activity_type": {"type": "string", "enum": [t.value for t in PlannedActivityType]},
                        "target_distance_km": {"type": "number"},
                        "target_duration_seconds": {"type": "integer"},
                        "target_hr_zone": {"type": "integer", "minimum": 1, "maximum": 5},
                        "notes": {"type": "string"},
                    },
                },
            },
            "reasoning": {
                "type": "object",
                "required": [
                    "training_load_trend", "goal_progress",
                    "two_week_volume_km", "target_two_week_volume_km",
                    "changes_from_last_week", "summary",
                ],
                "properties": {
                    "training_load_trend": {"type": "string", "enum": ["overtraining", "on_track", "undertraining"]},
                    "goal_progress": {"type": "string", "enum": ["ahead", "on_track", "behind"]},
                    "two_week_volume_km": {"type": "number"},
                    "target_two_week_volume_km": {"type": "number"},
                    "changes_from_last_week": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["date", "from_activity", "to_activity", "reason"],
                            "properties": {
                                "date": {"type": "string"},
                                "from_activity": {"type": "string"},
                                "to_activity": {"type": "string"},
                                "reason": {"type": "string"},
                            },
                        },
                    },
                    "summary": {
                        "type": "string",
                        "description": (
                            "3–4 sentences MAX. Format: (1) key signal this week "
                            "(volume/HR/wellness in one sentence). "
                            "(2) coaching decision and why. "
                            "(3) what to watch or the decision gate for next week. "
                            "No headers, no bullet points, no repetition of athlete name."
                        ),
                    },
                },
            },
        },
    },
}

_PROPOSE_ADAPTATION_TOOL = {
    "name": "propose_intra_week_adaptation",
    "description": (
        "Propose changes to remaining workouts this week due to injury risk. "
        "These will be sent to the athlete for approval before taking effect. "
        "Only call this if there is a genuine concern."
    ),
    "input_schema": {
        "type": "object",
        "required": ["trigger", "affected_dates", "proposed_workouts"],
        "properties": {
            "trigger": {"type": "string", "description": "Plain-English reason shown to the athlete"},
            "affected_dates": {"type": "array", "items": {"type": "string", "description": "YYYY-MM-DD"}},
            "proposed_workouts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["date", "activity_type"],
                    "properties": {
                        "date": {"type": "string"},
                        "activity_type": {"type": "string", "enum": [t.value for t in PlannedActivityType]},
                        "target_distance_km": {"type": "number"},
                        "target_duration_seconds": {"type": "integer"},
                        "target_hr_zone": {"type": "integer"},
                        "notes": {"type": "string"},
                    },
                },
            },
        },
    },
}

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def adapt_plan(state: AgentState) -> dict:
    athlete = state.athlete
    log = logger.bind(node="adapt_plan", athlete_id=athlete.id, run_phase=state.run_phase)
    log.info("node_started")

    errors = list(state.errors)
    try:
        if state.run_phase == RunPhase.post_run:
            return _adapt_post_run(state, log, errors)
        return _adapt_weekly(state, log, errors)
    except Exception as e:
        errors.append(f"adapt_plan: {e}")
        log.error("node_failed", error=str(e))
        return {"errors": errors}


# ---------------------------------------------------------------------------
# Weekly replan
# ---------------------------------------------------------------------------

def _adapt_weekly(state: AgentState, log, errors: list[str]) -> dict:
    athlete_id = state.athlete.id
    a = state.analysis

    # Surface past patterns before prompting the LLM — helps personalise the plan
    past_observations = retrieve_relevant(
        athlete_id=athlete_id,
        query=(
            f"training load {a.training_load_trend.value}, goal {a.goal_progress.value}, "
            + " ".join(a.notes[:2])
        ),
        n_results=4,
    )

    result = _run_llm_with_tools(
        messages=[{"role": "user", "content": _weekly_user_message(state)}],
        tools=[_GET_WEATHER_TOOL, _CREATE_PLAN_TOOL],
        system=_weekly_system_prompt(state, past_observations),
        output_tool="create_weekly_plan",
        log=log,
    )

    if result is None:
        errors.append("adapt_plan: LLM did not produce a weekly plan")
        log.error("no_plan_produced")
        return {"errors": errors}

    plan = _parse_weekly_plan(result)
    save_plan(plan, athlete_id=athlete_id)

    # Log what happened so future cycles can learn from it
    log_episode(
        athlete_id=athlete_id,
        episode_type=EpisodeType.plan_generated,
        data={
            "plan_id": plan.id,
            "week_start": plan.week_start_date.isoformat(),
            "total_volume_km": plan.total_volume_km,
            "training_load_trend": a.training_load_trend.value,
            "goal_progress": a.goal_progress.value,
            "summary": plan.reasoning.summary,
        },
    )
    store_observation(
        athlete_id=athlete_id,
        text=(
            f"Week of {plan.week_start_date}: {plan.reasoning.summary} "
            f"Load: {a.training_load_trend.value}, goal: {a.goal_progress.value}. "
            f"Volume: {plan.total_volume_km:.1f} km."
        ),
        category="plan_outcome",
        # Deterministic ID: same week always upserts the existing entry instead of
        # appending a duplicate when the agent re-runs without new Strava data.
        observation_id=f"plan_outcome_{athlete_id}_{plan.week_start_date}",
    )

    log.info("node_completed", plan_id=plan.id, workouts=len(plan.workouts))
    return {"current_plan": plan, "errors": errors}


def _weekly_system_prompt(state: AgentState, past_observations: list[str]) -> str:
    athlete = state.athlete
    goal = athlete.goal
    days_to_race = (goal.race_date - date.today()).days

    obs_section = ""
    if past_observations:
        obs_section = "\nPAST COACHING OBSERVATIONS (use to personalise the plan):\n" + \
                      "\n".join(f"- {o}" for o in past_observations)

    return f"""You are an expert running coach building a personalised weekly training plan.

ATHLETE:
- Name: {athlete.name}  |  Fitness level: {athlete.fitness_level.value}
- Resting HR: {athlete.resting_heart_rate or "unknown"} bpm  |  Max HR: {athlete.max_heart_rate or "unknown"} bpm
- Weekly mileage target: {athlete.weekly_mileage_target_km} km

RACE GOAL:
- {goal.race_name} — {goal.race_distance_km or "unknown"} km on {goal.race_date} ({days_to_race} days away)
- Target finish: {_format_time(goal.target_finish_seconds)}
- Training city: {goal.training_location.city} at {goal.training_location.altitude_m}m altitude
- Race city: {goal.race_location.city} at {goal.race_location.altitude_m}m altitude
- Altitude change on race day: {goal.altitude_drop_m:+.0f}m ({"lower" if goal.altitude_drop_m > 0 else "higher"} than training — {"expect faster pace" if goal.altitude_drop_m > 0 else "may feel harder"})
- Course elevation gain: {goal.course_elevation_gain_m or "unknown"} m
{obs_section}
PLAN RULES:
- Exactly 3 running days, 3 strength days, 1 rest day (Mon–Sun)
- Structure: Run, Strength, Run, Strength, Run, Strength, Rest
- Check weather before finalising run intensities — adjust pace/zone for heat
- If race is within 3 weeks apply taper: reduce run volume by 20–30%
- HR zones: 1=very easy  2=easy  3=moderate  4=hard  5=max
- Explain every change from last week in changes_from_last_week using valid activity_type values only
- If this is the first week with no previous plan, set changes_from_last_week to an empty array []
- Summary must be 3–4 sentences only: signal → decision → next gate. No headers, no sub-bullets.

Today: {date.today().isoformat()}. Plan week starts: {_next_monday().isoformat()}."""


def _weekly_user_message(state: AgentState) -> str:
    a = state.analysis
    loc = state.athlete.goal.training_location

    last_week = "No previous plan (first week)"
    if state.last_plan:
        last_week = (
            f"Last week total volume: {state.last_plan.total_volume_km:.1f} km  |  "
            f"Reasoning summary: {state.last_plan.reasoning.summary}"
        )

    wellness_section = ""
    if state.wellness:
        lines = []
        for w in sorted(state.wellness, key=lambda x: x.date)[-7:]:  # last 7 days
            parts = [str(w.date)]
            if w.resting_heart_rate:
                parts.append(f"RHR {w.resting_heart_rate} bpm")
            if w.avg_stress is not None:
                parts.append(f"stress {w.avg_stress}/100 ({w.stress_label})")
            if w.sleep_hours:
                parts.append(f"sleep {w.sleep_hours}h")
            if w.avg_cadence_spm:
                parts.append(f"cadence {w.avg_cadence_spm:.0f} spm")
            lines.append("  " + "  |  ".join(parts))
        wellness_section = "\nGARMIN WELLNESS (last 7 days):\n" + "\n".join(lines)

    return f"""TRAINING ANALYSIS — last 14 days:
Load trend: {a.training_load_trend.value}  |  Goal progress: {a.goal_progress.value}
Actual volume: {a.two_week_volume_km:.1f} km  (target: {a.target_two_week_volume_km:.1f} km)
Observations:
{chr(10).join(f"  • {n}" for n in a.notes) or "  • No concerns"}
{wellness_section}
LAST WEEK: {last_week}

Check the weather forecast for {loc.city} (lat {loc.latitude}, lng {loc.longitude}) \
then call create_weekly_plan with the complete plan."""


# ---------------------------------------------------------------------------
# Post-run intra-week adaptation
# ---------------------------------------------------------------------------

def _adapt_post_run(state: AgentState, log, errors: list[str]) -> dict:
    if not state.current_plan or not state.analysis:
        return {"errors": errors}

    injury_notes = [
        n for n in state.analysis.notes
        if "anomaly" in n.lower() or "injury" in n.lower()
    ]
    if not injury_notes:
        log.info("no_adaptation_needed")
        return {"errors": errors}

    today = date.today()
    remaining = [w for w in state.current_plan.workouts if w.date > today]
    if not remaining:
        log.info("no_remaining_workouts")
        return {"errors": errors}

    result = _run_llm_with_tools(
        messages=[{"role": "user", "content": _postrun_user_message(injury_notes, remaining)}],
        tools=[_PROPOSE_ADAPTATION_TOOL],
        system=_postrun_system_prompt(state),
        output_tool="propose_intra_week_adaptation",
        log=log,
    )

    if result is None:
        log.info("no_adaptation_proposed")
        return {"errors": errors}

    adaptation = _parse_adaptation(result, remaining)
    updated_plan = state.current_plan.model_copy(update={"pending_adaptation": adaptation})

    log_episode(
        athlete_id=state.athlete.id,
        episode_type=EpisodeType.plan_adapted,
        data={
            "trigger": adaptation.trigger,
            "affected_dates": [d.isoformat() for d in adaptation.affected_dates],
            "proposed_changes": len(adaptation.proposed_workouts),
        },
    )
    store_observation(
        athlete_id=state.athlete.id,
        text=f"Mid-week adaptation: {adaptation.trigger} — "
             f"{len(adaptation.affected_dates)} workouts modified, pending athlete approval.",
        category="injury_history",
    )

    log.info("adaptation_proposed", trigger=adaptation.trigger, affected=len(adaptation.affected_dates))
    return {"current_plan": updated_plan, "errors": errors}


def _postrun_system_prompt(state: AgentState) -> str:
    athlete = state.athlete
    return f"""You are monitoring {athlete.name}'s training for injury risk.
Max HR: {athlete.max_heart_rate or "unknown"} bpm  |  Fitness: {athlete.fitness_level.value}

An anomaly was detected in a recent workout. Decide if the remaining workouts this week
need to be modified to protect the athlete. Only propose changes for genuine concerns —
do not over-react to minor variations. If changes are warranted, call propose_intra_week_adaptation."""


def _postrun_user_message(injury_notes: list[str], remaining: list[PlannedWorkout]) -> str:
    remaining_text = "\n".join(
        f"  • {w.date} — {w.activity_type.value}"
        f"{f' ({w.target_distance_km} km)' if w.target_distance_km else ''}"
        f"{f' zone {w.target_hr_zone}' if w.target_hr_zone else ''}"
        for w in remaining
    )
    return f"""ANOMALY DETECTED:
{chr(10).join(f"  • {n}" for n in injury_notes)}

REMAINING WORKOUTS THIS WEEK:
{remaining_text}

Propose changes if needed, otherwise do not call the tool."""


# ---------------------------------------------------------------------------
# LLM tool-use loop
# ---------------------------------------------------------------------------

def _run_llm_with_tools(
    messages: list[dict],
    tools: list[dict],
    system: str,
    output_tool: str,
    log,
) -> dict[str, Any] | None:
    _print_llm_context(system, messages)

    total_in = total_out = 0

    for iteration in range(MAX_TOOL_ITERATIONS):
        response = _client.messages.create(
            model=MODEL,
            max_tokens=4096,
            system=system,
            messages=messages,
            tools=tools,
        )
        usage = response.usage
        total_in  += usage.input_tokens
        total_out += usage.output_tokens
        _print_token_usage(iteration, usage.input_tokens, usage.output_tokens, total_in, total_out, response)
        log.debug("llm_response", stop_reason=response.stop_reason, iteration=iteration)

        if response.stop_reason != "tool_use":
            break

        tool_results = []
        output = None

        for block in response.content:
            if block.type != "tool_use":
                continue
            if block.name == output_tool:
                output = block.input
                tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": "Received."})
            else:
                result = _execute_tool(block.name, block.input, log)
                tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": json.dumps(result)})

        if output is not None:
            return output

        messages = messages + [
            {"role": "assistant", "content": response.content},
            {"role": "user", "content": tool_results},
        ]

    return None


def _print_llm_context(system: str, messages: list[dict]) -> None:
    w = 64
    print(f"\n  ┌{'─'*(w-2)}┐")
    print(f"  │{'LLM CONTEXT':^{w-2}}│")
    print(f"  ├{'─'*(w-2)}┤")

    print(f"  │ SYSTEM PROMPT  ({len(system):,} chars){'':{w-32}}│")
    print(f"  ├{'─'*(w-2)}┤")
    for line in system.splitlines():
        # Wrap long lines at (w-4) chars
        while len(line) > w - 4:
            print(f"  │ {line[:w-4]} │")
            line = line[w-4:]
        print(f"  │ {line:<{w-4}} │")

    for msg in messages:
        role = msg["role"].upper()
        content = msg["content"]
        if isinstance(content, str):
            print(f"  ├{'─'*(w-2)}┤")
            print(f"  │ {role} MESSAGE  ({len(content):,} chars){'':{w-32}}│")
            print(f"  ├{'─'*(w-2)}┤")
            for line in content.splitlines():
                while len(line) > w - 4:
                    print(f"  │ {line[:w-4]} │")
                    line = line[w-4:]
                print(f"  │ {line:<{w-4}} │")

    print(f"  └{'─'*(w-2)}┘")


def _print_token_usage(
    iteration: int,
    input_tokens: int, output_tokens: int,
    total_in: int, total_out: int,
    response,
) -> None:
    tools_called = [b.name for b in response.content if b.type == "tool_use"]
    tool_str = f"  tools: {', '.join(tools_called)}" if tools_called else ""
    print(
        f"\n  call #{iteration+1}  "
        f"in={input_tokens:,}  out={output_tokens:,}  "
        f"stop={response.stop_reason}"
        f"{tool_str}"
    )
    if total_in != input_tokens:
        print(f"         cumulative  in={total_in:,}  out={total_out:,}  total={total_in+total_out:,}")


def _execute_tool(name: str, input: dict[str, Any], log) -> Any:
    if name == "get_weather_forecast":
        try:
            forecast = get_forecast(latitude=input["latitude"], longitude=input["longitude"])
            log.info("weather_fetched", city=input.get("city"))
            return forecast.model_dump(mode="json")
        except KeyError:
            log.warning("weather_api_key_missing")
            return {"error": "OPENWEATHERMAP_API_KEY not set — proceed without weather data, assume typical conditions for the training city"}
        except Exception as e:
            log.warning("weather_fetch_failed", error=str(e))
            return {"error": f"Weather unavailable ({e}) — proceed without weather data"}
    return {"error": f"Unknown tool: {name}"}


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

def _parse_weekly_plan(data: dict) -> Plan:
    workouts = [
        PlannedWorkout(
            date=date.fromisoformat(w["date"]),
            activity_type=PlannedActivityType(w["activity_type"]),
            target_distance_km=w.get("target_distance_km"),
            target_duration_seconds=w.get("target_duration_seconds"),
            target_hr_zone=w.get("target_hr_zone"),
            notes=w.get("notes"),
        )
        for w in data["workouts"]
    ]
    r = data["reasoning"]
    reasoning = PlanReasoning(
        training_load_trend=TrainingLoadTrend(r["training_load_trend"]),
        goal_progress=GoalProgress(r["goal_progress"]),
        two_week_volume_km=r["two_week_volume_km"],
        target_two_week_volume_km=r["target_two_week_volume_km"],
        changes_from_last_week=_parse_changes(r.get("changes_from_last_week", [])),
        summary=r["summary"],
    )
    return Plan(
        id=str(uuid.uuid4()),
        created_at=datetime.now(),
        week_start_date=_next_monday(),
        workouts=workouts,
        reasoning=reasoning,
    )


def _parse_changes(raw: list[dict]) -> list[WorkoutChange]:
    changes = []
    valid = {t.value for t in PlannedActivityType}
    for c in raw:
        if c.get("from_activity") not in valid or c.get("to_activity") not in valid:
            continue  # skip entries where LLM used free text instead of enum values
        try:
            changes.append(WorkoutChange(
                date=date.fromisoformat(c["date"]),
                from_activity=PlannedActivityType(c["from_activity"]),
                to_activity=PlannedActivityType(c["to_activity"]),
                reason=c["reason"],
            ))
        except (KeyError, ValueError):
            continue
    return changes


def _parse_adaptation(data: dict, remaining: list[PlannedWorkout]) -> IntraWeekAdaptation:
    affected_dates = [date.fromisoformat(d) for d in data["affected_dates"]]
    proposed = [
        PlannedWorkout(
            date=date.fromisoformat(w["date"]),
            activity_type=PlannedActivityType(w["activity_type"]),
            target_distance_km=w.get("target_distance_km"),
            target_duration_seconds=w.get("target_duration_seconds"),
            target_hr_zone=w.get("target_hr_zone"),
            notes=w.get("notes"),
        )
        for w in data["proposed_workouts"]
    ]
    original = [w for w in remaining if w.date in affected_dates]
    return IntraWeekAdaptation(
        proposed_at=datetime.now(),
        trigger=data["trigger"],
        affected_dates=affected_dates,
        original_workouts=original,
        proposed_workouts=proposed,
        status=AdaptationStatus.pending_approval,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _next_monday() -> date:
    today = date.today()
    days_ahead = (7 - today.weekday()) % 7
    return today + timedelta(days=days_ahead or 7)


def _format_time(seconds: int | None) -> str:
    if not seconds:
        return "finish only (no time target)"
    h, m = divmod(seconds // 60, 60)
    return f"{h}h {m}min"
