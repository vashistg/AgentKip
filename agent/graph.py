import sqlite3

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.sqlite import SqliteSaver

from agent.state import AgentState
from agent.nodes.assess import assess
from agent.nodes.fetch_data import fetch_data
from agent.nodes.analyze import analyze
from agent.nodes.adapt_plan import adapt_plan
from agent.nodes.wait import wait

CHECKPOINT_DB = "db/checkpoints.db"

_STEPS = ["assess", "fetch_data", "analyze", "adapt_plan", "wait"]
_TOTAL = len(_STEPS)


def _progress(name: str, fn):
    """Wrap a node to print a step banner before and a summary line after."""
    step = _STEPS.index(name) + 1

    def wrapper(state: AgentState) -> dict:
        print(f"\n[{step}/{_TOTAL}] {name} {'─' * (38 - len(name))}")
        result = fn(state)
        _summarise(name, state, result)
        return result

    return wrapper


def _summarise(name: str, state: AgentState, result: dict) -> None:
    errors = result.get("errors") or []
    if errors:
        for e in errors:
            print(f"  ✗ {e}")
        return

    if name == "assess":
        cleared = result.get("cleared_to_train", False)
        athlete = result.get("athlete") or state.athlete
        who = athlete.name if athlete else state.athlete_id
        icon = "✓" if cleared else "✗"
        status = "cleared to train" if cleared else "blocked from training"
        print(f"  {icon} {who} — {status}")
        for note in (result.get("assessment_notes") or []):
            print(f"    ↳ {note}")

    elif name == "fetch_data":
        athlete = result.get("athlete")
        count = len(athlete.recent_workouts) if athlete and athlete.recent_workouts else 0
        source = (result.get("workout_source") or "unknown").capitalize()
        wellness = result.get("wellness") or []
        wellness_str = f"  +  {len(wellness)} wellness day(s)" if wellness else ""
        print(f"  ✓ {count} workout(s) from {source}{wellness_str}")

    elif name == "analyze":
        a = result.get("analysis")
        if a:
            print(f"  ✓ trend={a.training_load_trend.value}  "
                  f"progress={a.goal_progress.value}  "
                  f"actual={a.two_week_volume_km:.1f}km / target={a.target_two_week_volume_km:.1f}km (2-week)")
        for note in (a.notes if a else []):
            print(f"    ↳ {note}")

    elif name == "adapt_plan":
        plan = result.get("current_plan")
        if plan:
            print(f"  ✓ plan generated — {plan.total_volume_km:.1f}km week of {plan.week_start_date}")

    elif name == "wait":
        phase = result.get("run_phase")
        print(f"  ✓ phase → {phase}")


def _route_after_assess(state: AgentState) -> str:
    if state.errors or not state.cleared_to_train:
        return "wait"
    return "fetch_data"


def _route_or_error(state: AgentState) -> str:
    """Short-circuit to wait if a node wrote an error."""
    if state.errors:
        return "wait"
    return "continue"


def _route_after_wait(state: AgentState) -> str:
    """Exit the loop once the race date has passed — the agent's mission is complete."""
    if state.athlete and state.athlete.goal.is_complete:
        return END
    return "assess"


def build_graph() -> StateGraph:
    workflow = StateGraph(AgentState)

    workflow.add_node("assess",      _progress("assess",      assess))
    workflow.add_node("fetch_data",  _progress("fetch_data",  fetch_data))
    workflow.add_node("analyze",     _progress("analyze",     analyze))
    workflow.add_node("adapt_plan",  _progress("adapt_plan",  adapt_plan))
    workflow.add_node("wait",        _progress("wait",        wait))

    workflow.set_entry_point("assess")

    workflow.add_conditional_edges(
        "assess",
        _route_after_assess,
        {
            "fetch_data": "fetch_data",
            "wait": "wait",
        },
    )

    workflow.add_conditional_edges(
        "fetch_data",
        _route_or_error,
        {
            "continue": "analyze",
            "wait": "wait",
        },
    )

    workflow.add_conditional_edges(
        "analyze",
        _route_or_error,
        {
            "continue": "adapt_plan",
            "wait": "wait",
        },
    )

    # adapt_plan always hands off to wait — either to hold for user approval
    # (post_run phase) or to sleep until the next cycle (weekly_replan phase)
    workflow.add_edge("adapt_plan", "wait")

    # wait loops back to assess, or exits when the race goal is complete
    workflow.add_conditional_edges(
        "wait",
        _route_after_wait,
        {
            "assess": "assess",
            END: END,
        },
    )

    conn = sqlite3.connect(CHECKPOINT_DB, check_same_thread=False)
    checkpointer = SqliteSaver(conn)
    return workflow.compile(checkpointer=checkpointer)
