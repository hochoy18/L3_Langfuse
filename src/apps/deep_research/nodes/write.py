"""Write subgraph stub (per FR-005, ADR-0001).

Stub writes one pass: ``outline`` → ``draft`` → ``critic_review`` → ``polish``,
then exits. Spec 004 replaces the bodies with real LLM calls and adds a
conditional edge from ``polish`` back to ``critic_review`` for the iteration
loop (until ``critic.score >= 0.8`` or ``iters >= 2``).
"""
from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from src.apps.deep_research.schemas import Critique
from src.apps.deep_research.state import DeepResearchState

__all__ = ["build_write_subgraph"]


def _outline(state: DeepResearchState) -> dict:
    """Stub: write a placeholder outline string."""
    return {"outline": "<stub outline>"}


def _draft(state: DeepResearchState) -> dict:
    """Stub: write a placeholder draft string."""
    return {"draft": "<stub draft>"}


def _critic_review(state: DeepResearchState) -> dict:
    """Stub: append one Critique with score=0.0 (won't satisfy real threshold)."""
    return {
        "critique_history": [
            Critique(score=0.0, comments="<stub critique>", iteration=1),
        ],
    }


def _polish(state: DeepResearchState) -> dict:
    """Stub: write final_report placeholder and exit."""
    return {"final_report": "<stub final_report>"}


def build_write_subgraph():
    """Build the write subgraph.

    Returns a compiled ``StateGraph`` with four internal nodes wired linearly.
    Spec 004 will add a conditional edge ``polish → critic_review`` and
    introduce ``emit_final_report`` for proper Markdown + References assembly.
    """
    g = StateGraph(DeepResearchState)
    g.add_node("outline", _outline)
    g.add_node("draft", _draft)
    g.add_node("critic_review", _critic_review)
    g.add_node("polish", _polish)
    g.add_edge(START, "outline")
    g.add_edge("outline", "draft")
    g.add_edge("draft", "critic_review")
    g.add_edge("critic_review", "polish")
    g.add_edge("polish", END)
    return g.compile()
