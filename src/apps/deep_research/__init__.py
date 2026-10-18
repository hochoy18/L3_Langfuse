"""Public API for the deep_research package.

The foundation spec exposes the following:
- :func:`build_graph` — compile the StateGraph with PostgresSaver
- :func:`run_research` — run end-to-end or resume from interrupt
- :class:`NotYetImplementedError` — sentinel for stub calls
- 12 Pydantic entity classes from :mod:`.schemas`
- State TypedDict from :mod:`.state`

Sub-modules (``nodes``, ``prompts``, ``tools``) remain importable directly but
are not re-exported here.
"""
from __future__ import annotations

from src.apps.deep_research.graph import DEFAULT_DB_URI, build_graph, run_research
from src.apps.deep_research.prompts import NotYetImplementedError
from src.apps.deep_research.schemas import (
    Critique,
    Draft,
    FinalReport,
    Outline,
    Plan,
    PlanStep,
    PolishedDraft,
    Reference,
    ResearchQuery,
    SearchEvaluation,
    Source,
    UserQuery,
)
from src.apps.deep_research.state import DeepResearchState

__all__ = [
    "build_graph",
    "run_research",
    "DEFAULT_DB_URI",
    "NotYetImplementedError",
    "DeepResearchState",
    "UserQuery",
    "Plan",
    "PlanStep",
    "ResearchQuery",
    "Source",
    "SearchEvaluation",
    "Outline",
    "Draft",
    "Critique",
    "PolishedDraft",
    "FinalReport",
    "Reference",
]
