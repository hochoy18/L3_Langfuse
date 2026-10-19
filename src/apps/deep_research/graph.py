"""Build and run the deep research graph (per FR-004, FR-006, FR-007).

Public API:
- ``build_graph(db_uri=None)`` → compiled ``StateGraph`` with PostgresSaver attached
- ``run_research(input, *, thread_id=None, config=None)`` → final state dict
  (or ``{"error": "no_pending_interrupt", "thread_id": ...}`` per Q3,
   or ``{"error": "thread_id_busy", "thread_id": ...}`` per FR-017)

Graph topology (per ADR-0001 + spec 004 [Q6]):

    START → plan → confirm_plan → research → write → emit_final_report → END
                          ↓ (interrupt when auto_confirm_plan=False)
                          plan (when user rejects)

``research``, ``write`` are compiled sub-graphs attached as nodes.
``emit_final_report`` is a top-level node (spec 004 [Q6]: outer-graph, NOT
inside the write sub-graph, so it can also serve as the entry point for
future spec 005 export/send steps).

spec 002 additions (per FR-017):
- ``_acquire_thread_lock(tid)`` — Postgres advisory lock scoped to thread_id,
  1s timeout, auto-released on transaction commit/rollback.
- ``run_research`` acquires the lock before any ``graph.invoke`` /
  ``_has_pending_interrupt`` call. Lock contention returns
  ``{"error": "thread_id_busy", "thread_id": tid}`` instead of throwing.
"""
from __future__ import annotations

import os
import re
import uuid
from contextlib import contextmanager
from typing import Any

import psycopg
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

# spec 002 FR-017: max seconds to wait for the thread_id advisory lock.
_ADVISORY_LOCK_TIMEOUT_S = 1

__all__ = ["build_graph", "run_research", "DEFAULT_DB_URI", "_acquire_thread_lock"]


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


@contextmanager
def _acquire_thread_lock(tid: str):
    """Acquire a Postgres advisory lock scoped to ``tid`` (per FR-017).

    Uses ``pg_advisory_xact_lock(hashtext(tid))`` inside a short transaction;
    the lock is **auto-released** when the transaction ends (commit or
    rollback), so we never hold it past ``graph.invoke``'s scope.

    The lock is acquired with a ``SET LOCAL lock_timeout`` of 1s so that a
    concurrent second process returns ``psycopg.errors.LockNotAvailable``
    within ~1s instead of blocking forever (NFR-001, research.md §R4).

    Yields
    ------
    psycopg.Connection
        Open connection (for inspection / early commit if needed).  Callers
        normally ignore the value; the context manager handles ``__exit__``.

    Raises
    ------
    psycopg.errors.LockNotAvailable
        Another process holds the lock for this ``tid`` and the 1s timeout
        expired. ``run_research`` catches this and returns
        ``{"error": "thread_id_busy", "thread_id": tid}``.
    """
    uri = _normalize_pg_uri(os.environ.get("LANGGRAPH_CHECKPOINT_DB", DEFAULT_DB_URI))
    conn = psycopg.connect(uri, connect_timeout=3)
    try:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute("SET LOCAL lock_timeout = '1s'")
                # ``hashtext`` is built-in; the int4 result fits pg_advisory_xact_lock(bigint).
                # Convert to bigint via the ``::bigint`` cast so pg_advisory_xact_lock's
                # single-arg (bigint) overload is picked.
                cur.execute(
                    "SELECT pg_advisory_xact_lock(hashtext(%s)::bigint)", (tid,)
                )
            # Yield inside the transaction so the lock is held until the
            # ``with conn.transaction():`` block exits.
            yield conn
    finally:
        conn.close()


# Sentinel: ``_NO_CHECKPOINTER`` distinguishes "no checkpointer at all" from
# "default checkpointer (PostgresSaver)". The latter is the production path;
# the former is used by topology tests that don't need persistence.
_NO_CHECKPOINTER = object()


# ---------------------------------------------------------------------------
# spec 004 FR-007 + FR-008: emit_final_report — outer-graph node (per [Q6]).
# Not in the write sub-graph: emit depends on the FULL state (all sources
# aggregated across research, plus the polished draft) and is the boundary
# between the write loop and any future spec-005 export/send step.
# ---------------------------------------------------------------------------
def _emit_final_report_node(state: DeepResearchState) -> dict:
    """Produce FinalReport markdown (per FR-007 + FR-008 + [Q3] + [Q7]).

    Algorithm (per CitationPolicy + ReferenceListPolicy in CONTEXT.md §6):
    1. Take ``state["draft"]`` as the polished Markdown body.
    2. ``re.sub(r'\\{\\{cite_(\\d+)\\}\\}', lambda m: f'[ref_{m.group(1)}]', draft)``
       — replaces every ``{{cite_N}}`` placeholder with ``[ref_N]`` inline
       citation. Already-resolved ``[ref_N]`` literals pass through unchanged.
    3. ``[Q3] / [R2]``: placeholder N that has no matching ``Source.index``
       is dropped (regex returns `None`), NOT replaced — the literal token
       does not appear in the final report.
    4. ``Source.index`` duplicate defense (per [Q7] / [R6]): rebuild an
       ``index → position`` map via ``enumerate`` over ``state["sources"]``;
       drop duplicates silently (do NOT raise).
    5. Assemble ``## References`` section sorted by Source.index ascending.
       Each line is ``{i}. [{title}]({url}) — score: {score:.2f}`` (per FR-007(c)
       + plan §实现路径概要 + data-model.md L166: numbered markdown link).
    6. Return ``{"final_report": markdown_str}``.
    """
    draft_text = state.get("draft", "") or ""
    sources = list(state.get("sources") or [])

    # (4) Source.index duplicate defense (per [Q7] / [R6]) — rebuild a unique
    # ``[1..N]`` mapping via enumerate; drop collisions silently.
    sources_indexed: list[tuple[int, Any]] = []
    seen: set[int] = set()
    for pos, src in enumerate(sources, start=1):
        try:
            original_idx = int(getattr(src, "index", pos))
        except (TypeError, ValueError):
            original_idx = pos
        if original_idx in seen:
            print(
                f"[emit] duplicate Source.index={original_idx} skipped at "
                f"pos={pos}"
            )
            continue
        seen.add(original_idx)
        sources_indexed.append((original_idx, src))

    # (3) [Q3] valid set for placeholder replacement — only N in this set
    # are substituted by ``[ref_N]``; any other placeholder digit is dropped.
    # Accept BOTH `{{cite_N}}` (spec-contract form per FR-002) and `{{ref_N}}`
    # (LLM common idiom that the draft prompt sometimes triggers; defensive).
    valid_indices = {idx for idx, _ in sources_indexed}

    def _resolve_placeholder(match: re.Match) -> str:
        n = int(match.group(1))
        if n not in valid_indices:
            # per [Q3] — drop, do not raise
            return ""
        return f"[ref_{n}]"

    # Use a non-anchored regex that allows optional whitespace between the
    # opening braces and the ``cite_``/``ref_`` keyword (LLM occasionally
    # produces `{{ cite_N }}`). Capture only the integer.
    placeholder_re = re.compile(r"\{\{\s*(?:cite|ref)_(\d+)\s*\}\}")
    resolved = placeholder_re.sub(_resolve_placeholder, draft_text)

    # (5) ## References section — sorted by Source.index ascending; format
    # per ReferenceListPolicy (FR-007(c) + plan §实现路径概要 + data-model.md L166):
    # ``{i}. [{title}]({url}) — score: {score:.2f}`` — numbered list with
    # markdown link + ``score:`` prefix (no parens). `i` is the position in
        # the sorted-by-`Source.index`-ascending list (== Source.index when
        # Source.index is 1..N global unique).
    sources_sorted = sorted(sources_indexed, key=lambda x: x[0])
    ref_lines = ["", "", "## References", ""]
    for i, (idx, src) in enumerate(sources_sorted, start=1):
        title = getattr(src, "title", "(no title)")
        url = str(getattr(src, "url", ""))
        score = getattr(src, "score", None)
        score_suffix = f"score: {score:.2f}" if score is not None else "score: n/a"
        ref_lines.append(f"{i}. [{title}]({url}) — {score_suffix}")

    final_report = resolved.rstrip() + "\n" + "\n".join(ref_lines) + "\n"
    print(
        f"[emit] final_report chars={len(final_report)} "
        f"references={len(sources_sorted)}"
    )
    return {"final_report": final_report}


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
    # spec 004 FR-009 + [Q6]: emit_final_report is a top-level outer-graph
    # node (not in the write sub-graph).  Wired write → emit_final_report →
    # END so any spec-005 export/send step can hook between emit and END.
    g.add_node("emit_final_report", _emit_final_report_node)

    g.add_edge(START, "plan")
    g.add_edge("plan", "confirm_plan")
    # Conditional edge after confirm_plan: re-plan on "edit:", else continue.
    # Using a conditional edge (not Command(goto="plan")) avoids the
    # "Can receive only one value per step" error in LangGraph 1.2.x when the
    # last HumanMessage in state["messages"] starts with "edit:".
    g.add_conditional_edges(
        "confirm_plan",
        _after_confirm_plan,
        {"plan": "plan", "research": "research"},
    )
    g.add_edge("research", "write")
    g.add_edge("write", "emit_final_report")
    g.add_edge("emit_final_report", END)

    compiled = g.compile(checkpointer=checkpointer)
    if saver_ctx is not None:
        compiled._deep_research_saver_ctx = saver_ctx  # type: ignore[attr-defined]
    return compiled


def _after_confirm_plan(state: DeepResearchState) -> str:
    """Decide the next node after ``confirm_plan`` (per spec 002 FR-008).

    Returns
    -------
    str
        ``"plan"`` if the most recent ``HumanMessage`` in ``state["messages"]``
        starts with ``"edit:"`` (the user wants the plan regenerated);
        ``"research"`` otherwise (approved → continue to the research stage).
    """
    msgs = state.get("messages", []) or []
    for msg in reversed(msgs):
        if getattr(msg, "type", None) == "human" or isinstance(msg, dict) and msg.get("type") == "human":
            content = msg.get("content", "") if isinstance(msg, dict) else getattr(msg, "content", "")
            if isinstance(content, str) and content.startswith("edit:"):
                return "plan"
            return "research"
    return "research"


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

    Per FR-007 + Q3 + Q4 + spec 002 FR-017:
    - ``input`` is either a dict (initial state) or a ``Command(resume=...)``
    - If a ``Command(resume=...)`` arrives but the thread has no pending
      interrupt, returns ``{"error": "no_pending_interrupt", "thread_id": ...}``
    - If the thread_id advisory lock is held by another process, returns
      ``{"error": "thread_id_busy", "thread_id": ...}`` without invoking the
      graph (FR-017 + research §R4).
    - Initializes ``state["iteration_counts"] = {"research": 0, "write": 0}``
      and ``state["auto_confirm_plan"] = False`` (overridable from input dict)
    - The compiled graph is created lazily per call (per FR-014: every call
      must build a fresh graph so cross-process resume works).
    """
    tid = thread_id or str(uuid.uuid4())
    base_config: dict = {"configurable": {"thread_id": tid}}
    if config:
        # Don't let caller overwrite thread_id from config
        merged = {**config, "configurable": {**config.get("configurable", {}), "thread_id": tid}}
        base_config = merged

    # Helper: invoke a callable under the per-thread advisory lock.  Returns
    # either the callable's result, or a thread_id_busy error dict.
    def _under_lock(fn):
        try:
            with _acquire_thread_lock(tid):
                return fn()
        except psycopg.errors.LockNotAvailable:
            return {"error": "thread_id_busy", "thread_id": tid}

    # Special contract: Command(resume=...) on a thread with no pending interrupt
    if isinstance(input, Command):
        def _resume():
            g = build_graph()
            if not _has_pending_interrupt(g, base_config):
                return {"error": "no_pending_interrupt", "thread_id": tid}
            return g.invoke(input, config=base_config)
        return _under_lock(_resume)

    # Initial run: build graph, init state, invoke
    def _initial():
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
    return _under_lock(_initial)
