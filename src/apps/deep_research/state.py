"""DeepResearchState TypedDict (per FR-003).

11-field schema with explicit types per Q2 clarification:
- Pydantic entity collections use plain ``list[Entity]`` (overwrite semantics)
- Free-form text uses ``str``
- Counters use ``dict[str, int]`` (initialized at graph entry per Q4)
- Control flag uses ``bool``
- ``messages`` uses ``operator.add`` reducer (LLM chat-history style appends)

Why no reducer on Pydantic collections
--------------------------------------
Initial design used ``Annotated[list[...], operator.add]`` (per Q2 "append-
reducer") but LangGraph 1.2.x applies the parent reducer to a sub-graph's
already-reduced output, doubling the value. Each stub (and the planned real
node implementations) returns the **full** current list, not a delta, so plain
``list`` (overwrite) matches the contract.
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


class DeepResearchState(TypedDict):
    """State flowing through the deep research graph.

    Field type rationale (per Q2):
    - Collections → Pydantic entity list, overwrite semantics
    - Text → ``str`` (LLM I/O; no value in Pydantic wrapping)
    - Counters → ``dict[str, int]`` (initialized at graph entry per Q4)
    - Control → ``bool``
    """

    # User input
    user_query: str
    # Plan + research artifacts (Pydantic collections, overwrite)
    plan: list[PlanStep]
    research_queries: list[ResearchQuery]
    sources: list[Source]
    search_evaluations: list[SearchEvaluation]
    # Write stage outputs (raw text)
    outline: str
    draft: str
    critique_history: list[Critique]
    final_report: str
    # Counters (initialized by run_research; stub nodes do not touch)
    iteration_counts: dict[str, int]
    # LangChain message log (always empty in foundation; spec 002+ uses)
    messages: Annotated[list[AnyMessage], operator.add]
    # Control flag
    auto_confirm_plan: bool


__all__ = ["DeepResearchState"]
