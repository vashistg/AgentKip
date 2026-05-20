from datetime import datetime
from enum import Enum
from typing import Any, Optional

import structlog
from sqlalchemy import Column, DateTime, JSON, String, desc
from sqlalchemy.orm import Session

from db.schema import Base, engine

logger = structlog.get_logger()


class EpisodeType(str, Enum):
    workout_completed  = "workout_completed"   # athlete logged a run or strength session
    plan_generated     = "plan_generated"      # weekly plan was created by adapt_plan
    plan_adapted       = "plan_adapted"        # intra-week adaptation was proposed
    adaptation_approved = "adaptation_approved" # athlete approved a proposed change
    adaptation_rejected = "adaptation_rejected" # athlete rejected a proposed change
    injury_flagged     = "injury_flagged"      # injury detected or manually reported
    cycle_completed    = "cycle_completed"     # one full assess→adapt loop finished


class EpisodeRow(Base):
    __tablename__ = "episodes"

    id           = Column(String,   primary_key=True)
    athlete_id   = Column(String,   nullable=False, index=True)
    episode_type = Column(String,   nullable=False, index=True)
    event_date   = Column(DateTime, nullable=False, index=True)
    data         = Column(JSON,     nullable=False)
    created_at   = Column(DateTime, nullable=False, default=datetime.utcnow)


def init_episodic_db() -> None:
    Base.metadata.create_all(engine)


def log_episode(
    athlete_id: str,
    episode_type: EpisodeType,
    data: dict[str, Any],
    event_date: Optional[datetime] = None,
) -> None:
    """Append a new episode to the athlete's history."""
    import uuid
    with Session(engine) as session:
        row = EpisodeRow(
            id=str(uuid.uuid4()),
            athlete_id=athlete_id,
            episode_type=episode_type.value,
            event_date=event_date or datetime.utcnow(),
            data=data,
            created_at=datetime.utcnow(),
        )
        session.add(row)
        session.commit()
    logger.debug("episode_logged", athlete_id=athlete_id, type=episode_type)


def get_recent_episodes(
    athlete_id: str,
    limit: int = 20,
    episode_type: Optional[EpisodeType] = None,
) -> list[dict[str, Any]]:
    """Return the most recent episodes, optionally filtered by type."""
    with Session(engine) as session:
        query = session.query(EpisodeRow).filter_by(athlete_id=athlete_id)
        if episode_type:
            query = query.filter_by(episode_type=episode_type.value)
        rows = query.order_by(desc(EpisodeRow.event_date)).limit(limit).all()
    return [
        {
            "id": r.id,
            "type": r.episode_type,
            "event_date": r.event_date.isoformat(),
            "data": r.data,
        }
        for r in rows
    ]


def get_episodes_since(
    athlete_id: str,
    since: datetime,
    episode_type: Optional[EpisodeType] = None,
) -> list[dict[str, Any]]:
    """Return all episodes after a given datetime."""
    with Session(engine) as session:
        query = (
            session.query(EpisodeRow)
            .filter(EpisodeRow.athlete_id == athlete_id)
            .filter(EpisodeRow.event_date >= since)
        )
        if episode_type:
            query = query.filter_by(episode_type=episode_type.value)
        rows = query.order_by(desc(EpisodeRow.event_date)).all()
    return [
        {
            "id": r.id,
            "type": r.episode_type,
            "event_date": r.event_date.isoformat(),
            "data": r.data,
        }
        for r in rows
    ]
