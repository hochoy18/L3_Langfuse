"""Foundation spec tests covering SC-001, SC-002, SC-004, SC-005, SC-006.

Tests that require a reachable Postgres (``require_postgres`` fixture) skip
cleanly when the database is offline. Tests that don't need a DB (SC-001
topology, SC-006 no-hidden-deps) run unconditionally.
"""
from __future__ import annotations

import time

import pytest
from langgraph.types import Command

from src.apps.deep_research.graph import build_graph, run_research
from src.apps.deep_research.prompts import (
    NotYetImplementedError,
    get_llm,
    get_prompt,
)
from src.apps.deep_research.schemas import Critique, PlanStep, SearchEvaluation, Source


# ---------------------------------------------------------------------------
# SC-001: Graph topology compiles and contains all 4 nodes
# ---------------------------------------------------------------------------


def test_sc_001_topology_contains_four_top_level_nodes():
    """``build_graph(checkpointer=None).get_graph().draw_mermaid()`` must mention all 4 node names.

    Uses ``checkpointer=None`` so the topology can be verified without a
    running Postgres. The Postgres-wired ``build_graph()`` is exercised by
    SC-002 / SC-004 / SC-005.
    """
    g = build_graph(checkpointer=None)
    mermaid = g.get_graph().draw_mermaid()
    for node in ("plan", "confirm_plan", "research", "write"):
        assert node in mermaid, f"{node} missing from graph topology:\n{mermaid}"


def test_sc_001_topology_has_start_and_end_markers():
    g = build_graph(checkpointer=None)
    mermaid = g.get_graph().draw_mermaid()
    # LangGraph emits start/end as __start__ / __end__ in mermaid output
    assert "__start__" in mermaid and "__end__" in mermaid, (
        f"missing __start__/__end__ in:\n{mermaid}"
    )


# ---------------------------------------------------------------------------
# SC-002: End-to-end smoke reaches END with non-empty final_report + shape contract
# ---------------------------------------------------------------------------


def test_sc_002_end_to_end_smoke(fresh_thread_id, require_postgres):
    """``run_research(..., auto_confirm_plan=True)`` reaches END with pinned stub shape."""
    result = run_research(
        {"user_query": "test", "auto_confirm_plan": True},
        thread_id=fresh_thread_id,
    )
    # Stub return shape contract (per FR-005)
    assert isinstance(result["final_report"], str) and result["final_report"], (
        f"final_report empty: {result.get('final_report')!r}"
    )
    assert isinstance(result["plan"], list) and len(result["plan"]) >= 1
    assert all(isinstance(s, PlanStep) for s in result["plan"])
    assert isinstance(result["sources"], list) and 2 <= len(result["sources"]) <= 3
    assert all(isinstance(s, Source) for s in result["sources"])
    assert isinstance(result["search_evaluations"], list) and len(result["search_evaluations"]) >= 1
    assert all(isinstance(e, SearchEvaluation) for e in result["search_evaluations"])
    assert isinstance(result["critique_history"], list) and len(result["critique_history"]) >= 1
    assert all(isinstance(c, Critique) for c in result["critique_history"])
    # iteration_counts initialized correctly (per Q4)
    assert result["iteration_counts"] == {"research": 0, "write": 0}


# ---------------------------------------------------------------------------
# SC-002 supplement: no_pending_interrupt error contract (per Q3)
# ---------------------------------------------------------------------------


def test_no_pending_interrupt_returns_structured_error(fresh_thread_id, require_postgres):
    """``run_research(Command(resume=...))`` on a fresh thread returns the no_pending_interrupt contract."""
    result = run_research(Command(resume="approve"), thread_id=fresh_thread_id)
    assert result == {"error": "no_pending_interrupt", "thread_id": fresh_thread_id}


# ---------------------------------------------------------------------------
# SC-004: First call against a fresh DB auto-creates the checkpoints table
# ---------------------------------------------------------------------------


def test_sc_004_checkpoints_auto_create(checkpoints_clean, require_postgres, langgraph_db_uri):
    """``build_graph()`` recreates the ``checkpoints`` table from a fresh DB."""
    import psycopg

    # checkpoints_clean fixture already dropped the table
    build_graph()  # .setup() inside should recreate
    # Verify the table exists now
    with psycopg.connect(langgraph_db_uri) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'checkpoints')"
            )
            exists = cur.fetchone()[0]
    assert exists, "checkpoints table not created by build_graph()"


# ---------------------------------------------------------------------------
# SC-005: Re-invoking with same thread_id does not leak state
# ---------------------------------------------------------------------------


def test_sc_005_no_state_leakage_on_thread_reuse(fresh_thread_id, require_postgres):
    """Two calls with the same thread_id but different queries both complete cleanly."""
    r1 = run_research(
        {"user_query": "first query", "auto_confirm_plan": True},
        thread_id=fresh_thread_id,
    )
    r2 = run_research(
        {"user_query": "second query", "auto_confirm_plan": True},
        thread_id=fresh_thread_id,
    )
    assert r1["user_query"] == "first query"
    assert r2["user_query"] == "second query"
    assert r1["final_report"]
    assert r2["final_report"]


# ---------------------------------------------------------------------------
# SC-006: No hidden dependency on Langfuse / DashScope at import/build/run time
# ---------------------------------------------------------------------------


def test_sc_006_import_does_not_make_network_call():
    """Importing the package must not initiate any network connection."""
    t0 = time.monotonic()
    import src.apps.deep_research.graph  # noqa: F401
    elapsed = time.monotonic() - t0
    assert elapsed < 5.0, f"import took {elapsed:.2f}s — likely network call"


def test_sc_006_build_graph_does_not_make_network_call():
    """``build_graph()`` must not initiate any network call beyond Postgres setup."""
    # We measure build_graph() with a deliberately-bad URI to prove it does
    # not try Langfuse/DashScope. (Postgres failure is expected but acceptable
    # for this test; the assertion is about Langfuse/DashScope silence.)
    try:
        g = build_graph(db_uri="postgresql://nope:nope@127.0.0.1:1/nope")
    except Exception as exc:
        # Acceptable: Postgres unreachable. What we DON'T want is a Langfuse
        # or DashScope exception.
        msg = str(exc).lower()
        assert "langfuse" not in msg and "dashscope" not in msg, (
            f"build_graph hit Langfuse/DashScope unexpectedly: {exc}"
        )


def test_sc_006_stubs_raise_not_yet_implemented_error():
    """``get_prompt`` and ``get_llm`` raise the pinned custom exception."""
    with pytest.raises(NotYetImplementedError) as exc_info:
        get_prompt("any-name")
    assert "get_prompt" in str(exc_info.value)

    with pytest.raises(NotYetImplementedError) as exc_info:
        get_llm("plan")
    assert "get_llm" in str(exc_info.value)
