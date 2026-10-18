"""Schema round-trip and validation tests (SC-003).

Per FR-002: all 12 Pydantic entities must round-trip via
``model_dump_json()`` → ``model_validate_json()`` without data loss, and
constructing without required fields must raise ``ValidationError``.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

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


# Minimal valid samples for each of the 12 entities.
# Fields are exactly the required ones (per data-model.md §1).
SAMPLES: list[tuple[type, dict]] = [
    (UserQuery, {"text": "x"}),
    (Plan, {"steps": [PlanStep(title="s1")]}),
    (PlanStep, {"title": "s1"}),
    (ResearchQuery, {"text": "q", "target_step": 0, "iteration": 1}),
    (
        Source,
        {
            "index": 1,
            "title": "t",
            "url": "https://example.com",
            "content": "c",
            "query_index": 0,
        },
    ),
    (
        SearchEvaluation,
        {"source_index": 0, "relevance": 0.9, "rationale": "r", "evaluator": "rerank"},
    ),
    (Outline, {"markdown": "# h1\n## h2", "step_to_section": {0: "h2"}}),
    (Draft, {"markdown": "d"}),
    (Critique, {"score": 0.5, "comments": "c", "iteration": 1}),
    (PolishedDraft, {"markdown": "p", "parent_critique_index": 0}),
    (FinalReport, {"markdown": "r", "reference_count": 1, "section_count": 1}),
    (
        Reference,
        {
            "ref_id": 1,
            "source": Source(
                index=1,
                title="t",
                url="https://example.com",  # type: ignore[arg-type]
                content="c",
                query_index=0,
            ),
            "cited_in_sections": [0],
        },
    ),
]


@pytest.mark.parametrize("cls,sample", SAMPLES, ids=[cls.__name__ for cls, _ in SAMPLES])
def test_round_trip_preserves_all_fields(cls, sample):
    """``model_dump_json`` → ``model_validate_json`` must preserve every field."""
    entity = cls(**sample)
    raw = entity.model_dump_json()
    restored = cls.model_validate_json(raw)
    assert restored.model_dump() == entity.model_dump(), (
        f"round-trip lost data for {cls.__name__}"
    )


@pytest.mark.parametrize("cls,_", SAMPLES, ids=[cls.__name__ for cls, _ in SAMPLES])
def test_missing_required_field_raises_validation_error(cls, _):
    """Constructing without required fields must raise ``ValidationError``."""
    with pytest.raises(ValidationError):
        cls()
