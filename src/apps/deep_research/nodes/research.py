"""Research subgraph stub (per FR-005, ADR-0001, ADR-0002).

Stub writes 2-3 ``Source`` instances + matching ``SearchEvaluation`` entries
and exits immediately. Spec 003 replaces the body with a real loop:
``gen_query`` → ``Send[websearch × 5]`` → ``eval_search`` (ReRank + LLM Judge)
until ``len(sources) >= 8`` or ``iters >= 3``.
"""
from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from src.apps.deep_research.schemas import SearchEvaluation, Source
from src.apps.deep_research.state import DeepResearchState

__all__ = ["build_research_subgraph"]


# Internal node functions for the subgraph. Each is a stub that returns the
# pinned placeholder shape from FR-005.


def _gen_query(state: DeepResearchState) -> dict:
    """Stub: produce 0 research queries (the stub skips iteration)."""
    return {"research_queries": []}


def _websearch(state: DeepResearchState) -> dict:
    """Stub: write 2-3 Source + matching SearchEvaluation (per FR-005)."""
    return {
        "sources": [
            Source(
                index=1,
                title="<stub source 1>",
                url="https://example.com/1",  # type: ignore[arg-type]
                content="<stub content 1>",
                query_index=0,
            ),
            Source(
                index=2,
                title="<stub source 2>",
                url="https://example.com/2",  # type: ignore[arg-type]
                content="<stub content 2>",
                query_index=0,
            ),
        ],
        "search_evaluations": [
            SearchEvaluation(
                source_index=0,
                relevance=0.9,
                rationale="<stub eval 1>",
                evaluator="rerank",
            ),
            SearchEvaluation(
                source_index=1,
                relevance=0.8,
                rationale="<stub eval 2>",
                evaluator="rerank",
            ),
        ],
    }


def _eval_search(state: DeepResearchState) -> dict:
    """Stub: no-op (the search node already wrote evaluations)."""
    return {}


def build_research_subgraph():
    """Build the research subgraph.

    Returns a compiled ``StateGraph`` with three internal nodes
    (``gen_query`` → ``websearch`` → ``eval_search``) wired in sequence.
    Spec 003 will replace ``_websearch`` with the Send-fanout of 5 websearches
    and add a conditional edge from ``eval_search`` back to ``gen_query``
    for the iteration loop.
    """
    g = StateGraph(DeepResearchState)
    g.add_node("gen_query", _gen_query)
    g.add_node("websearch", _websearch)
    g.add_node("eval_search", _eval_search)
    g.add_edge(START, "gen_query")
    g.add_edge("gen_query", "websearch")
    g.add_edge("websearch", "eval_search")
    g.add_edge("eval_search", END)
    return g.compile()
