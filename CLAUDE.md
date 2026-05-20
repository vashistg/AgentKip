# AI Running Coach
Context file for Claude. Read this at the start of every session.

## What this project is
An agentic AI running coach that loops over athlete data from
Strava/Garmin, analyzes training load, and adapts workout plans.
The intent is to learn agent loop design from scratch using LangGraph.

## Architecture
- **Orchestration:** LangGraph state machine in `agent/graph.py` over the
  `AgentState` dataclass in `agent/state.py`.
- **Nodes** (`agent/nodes/`): `assess` -> `fetch_data` -> `analyze` -> `adapt_plan` -> `wait`.
- **Tools** (`tools/`): thin wrappers around Strava, Garmin, and the plan store.
- **Memory** (`memory/`): episodic = SQLite via SQLAlchemy (`memory/episodic.py`);
  semantic = ChromaDB (`memory/semantic.py`).
- **Models** (`models/`): Pydantic schemas for athlete, workout, plan.
- **Persistence** (`db/`): SQLAlchemy schema + the `running_coach.db` SQLite file.
- **Entry point:** `main.py` runs the agent loop.

## Key design rules
- Every tool returns a Pydantic model, never a raw dict
- Every state transition must be logged via structlog
- AdaptPlan node must include a reasoning field explaining WHY a plan changed
- Never call more than one external API in a single node

## How to run
source .venv/bin/activate
python main.py

## Tests
pytest tests/ -v
