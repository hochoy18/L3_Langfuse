"""DeepResearchState TypedDict (per FR-003).

11-field schema with explicit types per Q2 clarification, plus a 12th
``raw_sources`` channel introduced in spec 003 US3 to disambiguate the
Send fan-in writes from the eval_search renumbering.

Why ``raw_sources`` exists (the Send fan-in reducer-doubling problem)
--------------------------------------------------------------------
``state["sources"]`` is the **final, renumbered** source list that spec 004
write_subgraph consumes.  It is written by exactly one node
(``_eval_search_node``), so the spec 001 Q2 "plain list, overwrite
semantics" works.

The Send fan-out workers (US2) need a different channel that supports
concurrent writes from 1-5 workers without the
``InvalidUpdateError: Can receive only one value per step`` error that
LangGraph 1.2.x raises on plain lists.  They write to ``raw_sources``,
which carries a dedupe-by-identity reducer (per
[[langgraph-subgraph-reducer-doubling]] memory).  ``_eval_search_node``
reads ``raw_sources``, runs the dedup + ReRank + filter + renumber
pipeline, and writes the final list to ``sources`` (plain overwrite).

Identity: ``(query_index, url)`` — NOT including ``index``, because
``index`` is a "global position" that changes from 0 (placeholder) to
1..N (renumbered).  The dedupe ensures that if two workers happen to
return the same (query_index, url) — e.g., the Bailian App returning
the same page for two different queries — only one survives.
"""
from __future__ import annotations

import operator
from typing import Annotated

from langchain_core.messages import AnyMessage
from typing_extensions import TypedDict

from src.apps.deep_research.schemas import (
    Critique,
    PlanStep,
    ResearchQuery,
    SearchEvaluation,
    Source,
)


def _merge_sources_by_identity(left: list, right: list) -> list:
    """Reducer for ``raw_sources``: dedupe-by-identity, first-seen wins.

    spec 003 US2 needs this because the Send fan-out writes 1-5 worker
    deltas concurrently.  Plain ``operator.add`` causes the parent graph
    to re-append the subgraph's already-merged output (per
    [[langgraph-subgraph-reducer-doubling]] memory).  The dedupe-by-
    identity reducer is called per-worker-delta (LangGraph 1.2.x
    internally sequences the fan-in writes), so the result accumulates
    the workers' deltas with duplicates collapsed.

    Identity is ``(query_index, url)`` — see module docstring for why
    ``index`` is NOT part of the key.

    Semantics: first-seen wins (additive).  This is correct for US2
    (the workers' deltas are additive, never replacement).  For US3
    (eval_search renumbering), the workers' writes go to ``raw_sources``
    (this channel) and the renumbered output goes to the separate
    ``state["sources"]`` channel — so this reducer never sees renumbered
    data, only the workers' index=0 placeholder writes.
    """
    seen: set = set()
    merged: list = []
    for s in (left or []) + (right or []):
        if isinstance(s, dict):
            key = (s.get("query_index"), s.get("url"))
        else:
            # Pydantic model
            key = (s.query_index, str(s.url))
        if key in seen:
            continue
        seen.add(key)
        merged.append(s)
    return merged


def _merge_queries_by_identity(left: list, right: list) -> list:
    """Reducer for ``research_queries``: same dedupe-by-identity strategy.

    Identity = ``(text, target_step, iteration)`` (no URL on ResearchQuery).
    First-seen wins (additive accumulation across Send workers).
    """
    seen: set = set()
    merged: list = []
    for q in (left or []) + (right or []):
        if isinstance(q, dict):
            key = (q.get("text"), q.get("target_step"), q.get("iteration"))
        else:
            key = (q.text, q.target_step, q.iteration)
        if key in seen:
            continue
        seen.add(key)
        merged.append(q)
    return merged


class DeepResearchState(TypedDict):
    """State flowing through the deep research graph.

    Field type rationale (per Q2 + spec 003 US2/US3):
    - ``raw_sources`` → dedupe-by-identity reducer (Send fan-in target;
      carries index=0 placeholder sources from websearch workers).
    - ``sources`` → plain ``list[Source]`` overwrite (final renumbered
      output from _eval_search_node; spec 004 write_subgraph consumes it).
    - ``research_queries`` → dedupe-by-identity reducer (Send fan-in
      target; carries ResearchQuery from websearch workers).
    - Other Pydantic collections → overwrite (one writer per tick).
    - Text → ``str`` (LLM I/O; no value in Pydantic wrapping).
    - Counters → ``dict[str, int]`` (initialized by run_research per Q4).
    - Control → ``bool``.
    """

    # User input
    user_query: str
    # Plan + research artifacts (Pydantic collections, overwrite)
    plan: list[PlanStep]
    # spec 003 US2: Send fan-out writes per-worker deltas; reducer dedupes.
    # NOTE: this carries index=0 placeholder sources from websearch workers
    # only — _eval_search_node reads from here and writes the renumbered
    # list to the plain ``sources`` field below.
    raw_sources: Annotated[list[Source], _merge_sources_by_identity]
    research_queries: Annotated[list[ResearchQuery], _merge_queries_by_identity]
    # spec 003 US3: final renumbered source list (plain overwrite; written
    # only by _eval_search_node).  spec 004 write_subgraph consumes this.
    sources: list[Source]
    search_evaluations: list[SearchEvaluation]
    # Write stage outputs (raw text)
    outline: str
    draft: str
    # spec 004: write loop can run 1-2 critic iterations; need append-reducer
    # so each critique is preserved (without reducer, second critic overwrites
    # first). ``operator.add`` is safe here because ``_critic_review_node`` is
    # the ONLY writer to this field, both inside and outside the write
    # sub-graph — no Send fan-in, no multi-writer. See [[langgraph-subgraph-reducer-doubling]]
    # if a future spec adds a second writer.
    critique_history: Annotated[list[Critique], operator.add]
    final_report: str
    # Counters (initialized by run_research; stub nodes do not touch)
    iteration_counts: dict[str, int]
    # LangChain message log (always empty in foundation; spec 002+ uses)
    messages: Annotated[list[AnyMessage], operator.add]
    # Control flag
    auto_confirm_plan: bool


__all__ = [
    "DeepResearchState",
    "_merge_sources_by_identity",
    "_merge_queries_by_identity",
]
