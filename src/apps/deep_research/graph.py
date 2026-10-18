"""Build and run the deep research graph (per FR-004, FR-006, FR-007).

Public API:
- ``build_graph(db_uri=None)`` → compiled ``StateGraph`` with PostgresSaver attached
- ``run_research(input, *, thread_id=None, config=None)`` → final state dict
  (or ``{"error": "no_pending_interrupt", "thread_id": ...}`` per Q3)

Graph topology (per ADR-0001):

    START → plan → confirm_plan → research → write → END
                          ↓ (interrupt when auto_confirm_plan=False)
                          plan (when user rejects)

``research`` and ``write`` are compiled sub-graphs attached as nodes.
"""
from __future__ import annotations

import os
import uuid
from typing import Any

from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from src.apps.deep_research.nodes.plan import confirm_plan_node, plan_node
from src.apps.deep_research.nodes.research import build_research_subgraph
from src.apps.deep_research.nodes.write import build_write_subgraph
from src.apps.deep_research.pydantic_serde import PydanticJsonPlusSerializer
from src.apps.deep_research.state import DeepResearchState

# ADR-0005 default connection string. Override via LANGGRAPH_CHECKPOINT_DB env var.
DEFAULT_DB_URI = "postgresql://postgres:postgres@localhost:5432/postgres"

__all__ = ["build_graph", "run_research", "DEFAULT_DB_URI"]


def _resolve_db_uri(db_uri: str | None) -> str:
    """Pick the explicit arg, then env var, then ADR-0005 default.

    Also normalizes SQLAlchemy-style driver suffixes (e.g. ``postgresql+asyncpg://``)
    down to ``postgresql://`` so the sync ``psycopg`` driver can consume the URI.
    The asyncpg convention is preserved in ``.env`` for forward-compatibility with
    spec 002+ (AsyncPostgresSaver); today the foundation still uses the sync saver.
    """
    if db_uri:
        return _normalize_pg_uri(db_uri)
    env = os.environ.get("LANGGRAPH_CHECKPOINT_DB", DEFAULT_DB_URI)
    return _normalize_pg_uri(env)


def _normalize_pg_uri(uri: str) -> str:
    """Strip SQLAlchemy driver suffix from a Postgres URI.

    Examples
    --------
    ``postgresql+asyncpg://u:p@h:5432/d`` → ``postgresql://u:p@h:5432/d``
    ``postgresql+psycopg://u:p@h:5432/d`` → ``postgresql://u:p@h:5432/d``
    ``postgresql://u:p@h:5432/d``        → ``postgresql://u:p@h:5432/d`` (unchanged)
    """
    import re

    return re.sub(r"^postgresql\+\w+://", "postgresql://", uri)


# Sentinel: ``_NO_CHECKPOINTER`` distinguishes "no checkpointer at all" from
# "default checkpointer (PostgresSaver)". The latter is the production path;
# the former is used by topology tests that don't need persistence.
_NO_CHECKPOINTER = object()


def build_graph(
    db_uri: str | None = None,
    *,
    checkpointer=_NO_CHECKPOINTER,
):
    """Build the compiled deep research graph with a PostgresSaver attached.

    Per FR-004 + FR-006 + ADR-0001:
    - 4 top-level nodes: plan / confirm_plan / research / write
    - research and write are compiled sub-graphs
    - PostgresSaver attaches for cross-process resume; ``.setup()`` auto-creates
      the ``checkpoints`` table on first call (SC-004)

    Parameters
    ----------
    db_uri : str | None
        Postgres connection URI. Used only when the default PostgresSaver is
        being built. Defaults to ``LANGGRAPH_CHECKPOINT_DB`` env var, then
        ADR-0005 default.
    checkpointer : optional
        Pre-built checkpointer instance. Defaults to a PostgresSaver built
        from ``db_uri``. Pass an explicit ``None`` to build the graph WITHOUT
        any checkpointer (used by topology tests that don't need persistence).
    """
    saver_ctx = None
    if checkpointer is _NO_CHECKPOINTER:
        # Default: build PostgresSaver
        uri = _resolve_db_uri(db_uri)
        saver_ctx = PostgresSaver.from_conn_string(uri)
        checkpointer = saver_ctx.__enter__()
        # ``setup()`` runs ``CREATE TABLE IF NOT EXISTS`` for all migrations and
        # is safe to call repeatedly. We propagate exceptions rather than swallow
        # them so callers see real connection/schema problems instead of silent
        # ``checkpoints`` table absence downstream.
        checkpointer.setup()
        # Inject Pydantic-aware serde so Pydantic models in the state
        # (``Source``, ``PlanStep``, ``Critique``, …) can be msgpack-serialized
        # into the ``checkpoints`` table. Default JsonPlusSerializer fails with
        # ``TypeError: Type is not msgpack serializable: Source``.
        checkpointer.serde = PydanticJsonPlusSerializer()
    elif checkpointer is None:
        # Explicit ``None`` — no checkpointer at all (topology-only tests)
        checkpointer = None

    g = StateGraph(DeepResearchState)
    g.add_node("plan", plan_node)
    g.add_node("confirm_plan", confirm_plan_node)
    g.add_node("research", build_research_subgraph())
    g.add_node("write", build_write_subgraph())

    g.add_edge(START, "plan")
    g.add_edge("plan", "confirm_plan")
    g.add_edge("confirm_plan", "research")
    g.add_edge("research", "write")
    g.add_edge("write", END)

    compiled = g.compile(checkpointer=checkpointer)
    if saver_ctx is not None:
        compiled._deep_research_saver_ctx = saver_ctx  # type: ignore[attr-defined]
    return compiled


def _has_pending_interrupt(graph, config: dict) -> bool:
    """Return True if the given thread currently has a pending interrupt.

    LangGraph represents a pending interrupt as a non-empty ``tasks`` list on
    the latest state snapshot.
    """
    try:
        snapshot = graph.get_state(config)
    except Exception:
        return False
    tasks = getattr(snapshot, "tasks", None) or []
    return len(tasks) > 0


def run_research(
    input: dict | Command,
    *,
    thread_id: str | None = None,
    config: dict | None = None,
) -> dict:
    """Run the deep research graph end-to-end (or resume from an interrupt).

    Per FR-007 + Q3 + Q4:
    - ``input`` is either a dict (initial state) or a ``Command(resume=...)``
    - If a ``Command(resume=...)`` arrives but the thread has no pending
      interrupt, returns ``{"error": "no_pending_interrupt", "thread_id": ...}``
    - Initializes ``state["iteration_counts"] = {"research": 0, "write": 0}``
      and ``state["auto_confirm_plan"] = False`` (overridable from input dict)
    - The compiled graph is created lazily per call; callers wanting to share
      a graph can pre-build and pass via ``config["configurable"]["graph"]``
      (not in foundation spec; reserved for spec 005 Streamlit reuse).
    """
    tid = thread_id or str(uuid.uuid4())
    base_config: dict = {"configurable": {"thread_id": tid}}
    if config:
        # Don't let caller overwrite thread_id from config
        merged = {**config, "configurable": {**config.get("configurable", {}), "thread_id": tid}}
        base_config = merged

    # Special contract: Command(resume=...) on a thread with no pending interrupt
    if isinstance(input, Command):
        # Lazily build a graph to check the snapshot. We need a graph to read state;
        # in spec 005 we'll cache it.
        g = build_graph()
        if not _has_pending_interrupt(g, base_config):
            return {"error": "no_pending_interrupt", "thread_id": tid}
        return g.invoke(input, config=base_config)

    # Initial run: build graph, init state, invoke
    g = build_graph()
    initial: dict = dict(input) if isinstance(input, dict) else {}
    initial.setdefault("iteration_counts", {"research": 0, "write": 0})
    initial.setdefault("auto_confirm_plan", False)
    initial.setdefault("messages", [])
    initial.setdefault("plan", [])
    initial.setdefault("research_queries", [])
    initial.setdefault("sources", [])
    initial.setdefault("search_evaluations", [])
    initial.setdefault("critique_history", [])
    initial.setdefault("outline", "")
    initial.setdefault("draft", "")
    initial.setdefault("final_report", "")

    return g.invoke(initial, config=base_config)
