from datetime import datetime
from typing import Optional

import chromadb
import structlog

logger = structlog.get_logger()

# One persistent ChromaDB client shared across the process
_chroma = chromadb.PersistentClient(path="db/chroma")


def _collection(athlete_id: str) -> chromadb.Collection:
    """Get or create a per-athlete collection."""
    return _chroma.get_or_create_collection(
        name=f"athlete_{athlete_id}",
        metadata={"hnsw:space": "cosine"},
    )


def store_observation(
    athlete_id: str,
    text: str,
    category: str,
    observation_id: Optional[str] = None,
) -> str:
    """
    Store a coaching observation about the athlete.

    category examples: "performance_pattern", "injury_history",
                       "weather_response", "plan_outcome"

    Returns the ID of the stored observation.
    """
    import uuid
    obs_id = observation_id or str(uuid.uuid4())
    col = _collection(athlete_id)
    col.upsert(
        ids=[obs_id],
        documents=[text],
        metadatas=[{
            "athlete_id": athlete_id,
            "category": category,
            "created_at": datetime.utcnow().isoformat(),
        }],
    )
    logger.debug("observation_stored", athlete_id=athlete_id, category=category, id=obs_id)
    return obs_id


def retrieve_relevant(
    athlete_id: str,
    query: str,
    n_results: int = 5,
    category: Optional[str] = None,
) -> list[str]:
    """
    Retrieve observations most relevant to the query using cosine similarity.
    Used by adapt_plan to surface past patterns when building a new plan.
    """
    col = _collection(athlete_id)
    if col.count() == 0:
        return []

    where = {"athlete_id": athlete_id}
    if category:
        where["category"] = category

    results = col.query(
        query_texts=[query],
        n_results=min(n_results, col.count()),
        where=where,
    )
    docs = results.get("documents", [[]])[0]
    logger.debug("observations_retrieved", athlete_id=athlete_id, count=len(docs))
    return docs


def delete_observation(athlete_id: str, observation_id: str) -> None:
    """Remove a specific observation — used when a pattern is no longer valid."""
    col = _collection(athlete_id)
    col.delete(ids=[observation_id])
    logger.debug("observation_deleted", athlete_id=athlete_id, id=observation_id)
