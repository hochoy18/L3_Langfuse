"""Pydantic entity models (per FR-002, NFR-001).

12 entity models matching ``CONTEXT.md`` §2. ``frozen=False`` (NFR-001) to
allow state updates; ``Field(...)`` annotations for documentation.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


# Base config: frozen=False so state updates work, validate_assignment=True so
# mutating a field triggers validation (NFR-001 spirit).
class _Base(BaseModel):
    model_config = ConfigDict(frozen=False, validate_assignment=True)


class UserQuery(_Base):
    """Raw research question input from the user."""

    text: str = Field(..., description="The natural-language research question")
    thread_id: str | None = Field(default=None, description="Optional caller-supplied thread id")


class PlanStep(_Base):
    """One research section/chapter in the plan."""

    title: str = Field(..., description="Section heading as it will appear in the report")
    description: str | None = Field(default=None, description="Optional longer prompt hint")
    depends_on: list[int] = Field(default_factory=list, description="Indices of prerequisite steps")


class Plan(_Base):
    """Structured research plan (list of PlanStep)."""

    steps: list[PlanStep] = Field(..., description="Ordered list of research sections")


class ResearchQuery(_Base):
    """One search query string produced by the research stage."""

    text: str = Field(..., description="The search query string")
    target_step: int = Field(..., description="Index into Plan.steps this query serves")
    iteration: int = Field(..., description="Which research iteration (1, 2, 3...) this query belongs to")


class Source(_Base):
    """One raw search result."""

    index: int = Field(..., description="Position in the final report's References list (1-based, matches [ref_N])")
    title: str = Field(..., description="Page/article title")
    url: HttpUrl = Field(..., description="Canonical URL")
    content: str = Field(..., description="Snippet or full content (truncated for token budget)")
    score: float | None = Field(default=None, description="Optional relevance score from ReRank (0.0-1.0)")
    query_index: int = Field(..., description="Which ResearchQuery produced this source")


class SearchEvaluation(_Base):
    """Relevance evaluation of one Source."""

    source_index: int = Field(..., description="Position in state['sources'] this evaluation rates")
    relevance: float = Field(..., description="0.0-1.0 (ReRank score or LLM Judge score)")
    rationale: str = Field(..., description="One-sentence justification (LLM Judge only)")
    evaluator: Literal["rerank", "llm_judge"] = Field(
        ..., description="Which strategy produced this evaluation"
    )


class Outline(_Base):
    """Structured report outline (Markdown skeleton)."""

    markdown: str = Field(..., description="Skeleton markdown (h1 + h2 + h3 hierarchy)")
    step_to_section: dict[int, str] = Field(
        default_factory=dict, description="Map PlanStep.index → heading text"
    )


class Draft(_Base):
    """Initial report draft."""

    markdown: str = Field(..., description="Full report draft (still missing [ref_N] resolution)")
    outline_ref: str | None = Field(default=None, description="Back-reference to the Outline")


class Critique(_Base):
    """LLM-as-Judge review of a Draft."""

    score: float = Field(..., description="Overall quality 0.0-1.0 (WriteTerminationPolicy threshold: 0.8)")
    comments: str = Field(..., description="Free-form feedback from the LLM critic")
    dimensions: dict[str, float] = Field(
        default_factory=dict, description="Optional per-dimension scores (e.g. {'clarity': 0.9})"
    )
    iteration: int = Field(..., description="Which write iteration (1, 2...) this critique belongs to")


class PolishedDraft(_Base):
    """Draft after one polish iteration."""

    markdown: str = Field(..., description="Markdown after one polish iteration")
    parent_critique_index: int = Field(
        ..., description="Position in state['critique_history'] that motivated this polish"
    )


class FinalReport(_Base):
    """Markdown report with [ref_N] citations."""

    markdown: str = Field(..., description="The final markdown (with [ref_N] inline + ## References section)")
    reference_count: int = Field(..., description="Number of ## References entries")
    section_count: int = Field(..., description="Number of h2 chapters (must equal len(Plan.steps))")


class Reference(_Base):
    """Inline citation in FinalReport (value view of a Source)."""

    ref_id: int = Field(..., description="Matches the [ref_N] in FinalReport.markdown (1-based)")
    source: Source = Field(..., description="Embedded Source (not a pointer — Reference is a value view)")
    cited_in_sections: list[int] = Field(
        default_factory=list, description="Step indices where this reference is cited"
    )


__all__ = [
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
