from langgraph.graph import StateGraph, END

from agent.state import AgentState
from agent.nodes.assess import assess
from agent.nodes.fetch_data import fetch_data
from agent.nodes.analyze import analyze
from agent.nodes.adapt_plan import adapt_plan
from agent.nodes.wait import wait


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

    workflow.add_node("assess", assess)
    workflow.add_node("fetch_data", fetch_data)
    workflow.add_node("analyze", analyze)
    workflow.add_node("adapt_plan", adapt_plan)
    workflow.add_node("wait", wait)

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

    return workflow.compile()
