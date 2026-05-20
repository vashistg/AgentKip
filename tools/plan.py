import structlog

from db.schema import load_plan_from_db, save_plan_to_db
from models.plan import Plan

logger = structlog.get_logger()


def save_plan(plan: Plan, athlete_id: str) -> Plan:
    """Persist a plan to the database. Returns the saved plan unchanged."""
    log = logger.bind(tool="plan.save", plan_id=plan.id, athlete_id=athlete_id)
    save_plan_to_db(plan, athlete_id)
    log.info("plan_saved", week_start=plan.week_start_date)
    return plan


def load_current_plan(athlete_id: str) -> Plan | None:
    """Load the active (current week) plan for the athlete."""
    log = logger.bind(tool="plan.load_current", athlete_id=athlete_id)
    plan = load_plan_from_db(athlete_id, current=True)
    log.info("plan_loaded" if plan else "no_plan_found")
    return plan


def load_last_plan(athlete_id: str) -> Plan | None:
    """Load the previous week's plan for week-over-week diff in adapt_plan."""
    log = logger.bind(tool="plan.load_last", athlete_id=athlete_id)
    plan = load_plan_from_db(athlete_id, current=False)
    log.info("plan_loaded" if plan else "no_previous_plan")
    return plan
