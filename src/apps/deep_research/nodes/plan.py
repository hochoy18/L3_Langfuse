"""Plan and confirm_plan stub nodes (per FR-005, FR-008).

``plan_node`` returns a one-element list of ``PlanStep`` (the pinned stub
shape per Q1). ``confirm_plan_node`` honors ``state["auto_confirm_plan"]``:
when True, the graph continues uninterrupted; when False, an
``interrupt({"plan": state["plan"]})`` pauses the graph for user approval.
"""
from __future__ import annotations

from langgraph.types import interrupt

from src.apps.deep_research.schemas import PlanStep
from src.apps.deep_research.state import DeepResearchState

__all__ = ["plan_node", "confirm_plan_node"]


def plan_node(state: DeepResearchState) -> dict:
    """Stub plan_node: return one PlanStep placeholder.

    Per FR-005: ``state["plan"] = [PlanStep(title="<stub plan step 1>")]``.
    Spec 002 replaces this with a real LLM call to ``get_prompt("deep-research-plan")``
    + ``get_llm("plan")``.
    """
    return {
        "plan": [PlanStep(title="<stub plan step 1>")],
    }


def confirm_plan_node(state: DeepResearchState) -> dict:
    """Stub confirm_plan_node: skip interrupt when auto_confirm_plan is True.

    When False, pauses via ``interrupt({"plan": state["plan"]})``; the caller
    resumes with ``Command(resume="approve" | edited_plan)``.
    Spec 002 keeps the same contract.
    """
    if state.get("auto_confirm_plan", False):
        return {}
    interrupt({"plan": state["plan"]})
    return {}
