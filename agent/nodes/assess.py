import structlog

from agent.state import AgentState
from db.schema import load_athlete

logger = structlog.get_logger()


def assess(state: AgentState) -> dict:
    log = logger.bind(node="assess", run_phase=state.run_phase, athlete_id=state.athlete_id)
    log.info("node_started")

    notes = []
    errors = list(state.errors)

    # Load athlete profile from DB
    athlete = load_athlete(state.athlete_id)
    if athlete is None:
        errors.append(f"assess: athlete {state.athlete_id!r} not found")
        log.error("athlete_not_found")
        return {"athlete": None, "cleared_to_train": False, "assessment_notes": notes, "errors": errors}

    log = log.bind(athlete_name=athlete.name)

    # Any confirmed injury flag blocks training until manually cleared
    if athlete.injury_flags:
        notes.append(f"Active injury flags: {', '.join(athlete.injury_flags)}")
        log.warning("injury_flags_present", flags=athlete.injury_flags)
        return {"athlete": athlete, "cleared_to_train": False, "assessment_notes": notes, "errors": errors}

    log.info("node_completed", cleared_to_train=True)
    return {"athlete": athlete, "cleared_to_train": True, "assessment_notes": notes, "errors": errors}
