"""Plan and confirm_plan nodes — ✅ spec 002 real implementation
(Langfuse Prompt Management + LangGraph interrupt + edit branch).

Replaces the foundation spec 001 stubs with real LLM-driven plan generation
and a human-in-the-loop confirm step.

``plan_node`` calls DashScope qwen-plus-2025-04-28 (via ``get_llm("plan")``)
with the Langfuse ``deep-research-plan`` prompt and parses the response into
``list[PlanStep]``.  Includes exponential-backoff retry on 429/5xx, single
retry on JSON parse failure / empty plan, and Pydantic validation against
``Plan.steps`` (3-7 entries).

``confirm_plan_node`` invokes ``interrupt({"plan": [...]})`` (or short-circuits
on ``auto_confirm_plan=True``).  On ``Command(resume="edit: <text>")`` it
returns ``{"messages": [new_msg]}`` so the next ``plan_node`` invocation sees
the user's feedback; routing back to ``plan`` is done by
``graph.py::_after_confirm_plan`` conditional edge (LangGraph 1.2.x rejects
``Command(goto="plan", update=...)`` because it would multi-write the
``LastValue`` ``plan`` channel in one tick).

spec 002 changes vs foundation
-------------------------------
- plan_node:  real LLM (was 1-step stub)
- confirm_plan_node:  edit resume branch (was approve-only)
- helper ``_extract_user_feedback`` so plan_node's second pass can pass the
  user's edit text into chevron-rendered Mustache (with the ``user_feedback``
  block, see ``prompts.py:1-14``)
"""
from __future__ import annotations

import json
import time
from typing import Any

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage
from langgraph.types import interrupt
from openai import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    RateLimitError,
)

from src.apps.deep_research.prompts import NotYetImplementedError, get_llm, get_prompt
from src.apps.deep_research.prompts import _render_mustache
from src.apps.deep_research.schemas import Plan, PlanStep
from src.apps.deep_research.state import DeepResearchState

# spec 002 FR-002: 3-7 PlanStep entries per plan.
MIN_STEPS = 3
MAX_STEPS = 7
PLAN_PROMPT_NAME = "deep-research-plan"
PLAN_PROMPT_LABEL = "production"

# spec 002 FR-006(d) + research §R5: hand-rolled exponential backoff for
# 429/5xx.  We deliberately avoid the `tenacity` library to keep the dep
# graph minimal (FR-016: no new deps).
_HTTP_RETRY_STATUSES = (429, 500, 502, 503, 504)
_HTTP_NO_RETRY_STATUSES = (400, 401, 403)
_MAX_RETRIES = 2
_BACKOFF_BASE_S = 1.0
_BACKOFF_EXP = 2.0

# spec 002 FR-006(a)(b): JSON parse failure / empty plan retry once without
# backoff (the prompt is the same, the LLM may have returned junk on the
# first try).
_JSON_RETRY_COUNT = 1

__all__ = [
    "plan_node",
    "confirm_plan_node",
    "_extract_user_feedback",
    "_invoke_with_retry",
    "MIN_STEPS",
    "MAX_STEPS",
]


def _extract_user_feedback(state: DeepResearchState) -> str | None:
    """Return the most recent HumanMessage content (minus ``"edit:"`` prefix).

    spec 002 FR-003: confirm_plan_node injects ``HumanMessage(content="edit: <text>")``
    into ``state["messages"]``; the second ``plan_node`` call reads the most
    recent such HumanMessage to populate ``user_feedback`` for the Langfuse
    prompt.  Returns ``None`` when no prior edit exists.
    """
    messages: list[AnyMessage] = state.get("messages", []) or []
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage):
            content = (msg.content or "").strip()
            if content.startswith("edit:"):
                return content[len("edit:") :].strip()
            # A HumanMessage without "edit:" prefix is not user feedback for us.
            return None
    return None


def _build_chat_messages(
    compiled_prompt: str, user_query: str
) -> list[dict[str, str]]:
    """Wrap a compiled prompt + user query into a 2-message OpenAI chat payload.

    spec 002 R1 + contracts §C3: we use ``text``-type Langfuse prompts and
    build the chat messages client-side.  The compiled Mustache template goes
    into the ``system`` role; the original ``user_query`` into the ``user``
    role so the LLM has both the static instructions and the dynamic ask.
    """
    return [
        {"role": "system", "content": compiled_prompt},
        {"role": "user", "content": user_query},
    ]


def _invoke_with_retry(llm, messages: list[dict[str, str]]) -> str:
    """Invoke ``llm`` and return the assistant content string.

    spec 002 FR-006(d)(e) + research §R5:
    - HTTP 429 / 500 / 502 / 503 / 504 → exponential backoff (1s, 2s), up to
      ``_MAX_RETRIES=2`` retries.  After exhaustion, raise
      ``NotYetImplementedError("plan generation failed: dashscope rate limited")``.
    - HTTP 400 / 401 / 403 → no retry, propagate.
    - Other exceptions propagate.

    ``print(f"[retry] plan_retry_count={N}")`` is a spec 006 placeholder for
    the eventual Langfuse span attribute (NFR-001).
    """
    attempt = 0
    while True:
        try:
            resp = llm.invoke(messages)
            # ChatOpenAI returns AIMessage; extract content
            content = getattr(resp, "content", None)
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                # Some chat models return list[dict] for content
                return "".join(
                    c.get("text", "") if isinstance(c, dict) else str(c)
                    for c in content
                )
            return str(content) if content is not None else ""
        except BadRequestError as e:
            # 400/401/403 etc. — no retry
            status = getattr(e, "status_code", None) or 0
            if status in _HTTP_NO_RETRY_STATUSES or status < 500:
                raise NotYetImplementedError(
                    f"plan generation failed: bad request ({status}): {e}"
                ) from e
            raise
        except (RateLimitError, APIConnectionError, APITimeoutError) as e:
            status = getattr(e, "status_code", None) or 0
            if status in _HTTP_RETRY_STATUSES or isinstance(
                e, (APIConnectionError, APITimeoutError)
            ):
                if attempt >= _MAX_RETRIES:
                    print(
                        f"[retry] plan_retry_count={attempt} (exhausted, giving up)"
                    )
                    raise NotYetImplementedError(
                        f"plan generation failed: dashscope rate limited after "
                        f"{attempt} retries"
                    ) from e
                backoff = _BACKOFF_BASE_S * (_BACKOFF_EXP ** attempt)
                print(
                    f"[retry] plan_retry_count={attempt + 1} "
                    f"after {type(e).__name__}({status}), sleeping {backoff}s"
                )
                time.sleep(backoff)
                attempt += 1
                continue
            raise
        except AuthenticationError as e:
            # 401 → no retry
            raise NotYetImplementedError(
                f"plan generation failed: auth error: {e}"
            ) from e


def _parse_plan_response(content: str) -> list[dict[str, Any]]:
    """Parse the LLM's text response into a list of step dicts.

    spec 002 FR-002 + FR-006(a): LLM is instructed to return a JSON array
    only (no markdown fences), but we still strip fences defensively.

    Returns ``list[dict]``.  Raises ``ValueError`` on parse failure so the
    caller can decide to retry (FR-006(a)).
    """
    text = content.strip()
    # Strip ```json ... ``` fences if present
    if text.startswith("```"):
        # Remove first line (```json or ```) and last fence
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    parsed = json.loads(text)
    if not isinstance(parsed, list):
        raise ValueError(f"expected JSON array, got {type(parsed).__name__}")
    return parsed


def _validate_plan(steps_dicts: list[dict[str, Any]]) -> list[PlanStep]:
    """Convert raw dicts to ``PlanStep`` and validate against ``Plan`` schema.

    spec 002 FR-002 + FR-006(c) + data-model §2:
    - 3-7 entries enforced by ``Plan.steps`` Pydantic constraint.
    - Each entry validated as ``PlanStep``.
    """
    plan = Plan(steps=[PlanStep(**s) for s in steps_dicts])
    if len(plan.steps) > MAX_STEPS:
        raise NotYetImplementedError(
            f"plan steps exceed max_steps={MAX_STEPS} (got {len(plan.steps)})"
        )
    if len(plan.steps) < MIN_STEPS:
        raise NotYetImplementedError(
            f"plan steps below min_steps={MIN_STEPS} (got {len(plan.steps)})"
        )
    return plan.steps


def plan_node(state: DeepResearchState) -> dict:
    """Generate the research Plan via LLM (spec 002 FR-001 + contracts §C3).

    Pulls the ``deep-research-plan`` prompt from Langfuse, compiles Mustache
    with ``user_query`` + optional ``user_feedback`` (from the most recent
    HumanMessage in ``state["messages"]`` per FR-003), invokes
    ``qwen-plus-2025-04-28`` via DashScope, and writes the validated plan to
    ``state["plan"]``.

    Returns
    -------
    dict
        ``{"plan": list[PlanStep]}`` — overwrites ``state["plan"]`` per
        ``operator.add``-less overwrite semantics (Q2).

    Raises
    ------
    NotYetImplementedError
        JSON parse failed after retry, validation failed (3-7 steps), or
        HTTP 429/5xx exhausted retries.
    """
    user_query = state.get("user_query", "")
    user_feedback = _extract_user_feedback(state)

    # Fetch prompt + LLM (per FR-004 + FR-005; raises if env missing).
    prompt = get_prompt(PLAN_PROMPT_NAME, label=PLAN_PROMPT_LABEL)
    llm = get_llm("plan")

    # Compile Mustache client-side (chevron); Langfuse 4.x compile() doesn't
    # handle {{#if}}-style blocks, so we render the whole template ourselves.
    template = prompt.prompt
    compiled = _render_mustache(
        template,
        user_query=user_query,
        max_steps=MAX_STEPS,
        user_feedback=user_feedback or "",
    )

    messages = _build_chat_messages(compiled, user_query)

    # First attempt + immediate JSON-retry on parse failure (FR-006(a))
    last_error: Exception | None = None
    for attempt in range(_JSON_RETRY_COUNT + 1):
        try:
            content = _invoke_with_retry(llm, messages)
            steps_dicts = _parse_plan_response(content)
            if len(steps_dicts) == 0:
                # FR-006(b) path: empty list — retry once without backoff
                last_error = NotYetImplementedError("plan generation failed: empty plan")
                if attempt < _JSON_RETRY_COUNT:
                    print(f"[retry] plan_retry_count={attempt + 1} (empty list)")
                    continue
                raise last_error
            steps = _validate_plan(steps_dicts)
            return {"plan": steps}
        except NotYetImplementedError:
            # Validation errors propagate (don't retry — schema won't change)
            raise
        except ValueError as e:
            # JSON parse failure — retry once
            last_error = e
            if attempt < _JSON_RETRY_COUNT:
                print(
                    f"[retry] plan_retry_count={attempt + 1} (JSON parse: {e})"
                )
                continue
            raise NotYetImplementedError(
                f"plan generation failed: JSON parse error after retry: {e}"
            ) from e

    # Unreachable, but keep type-checkers happy
    raise NotYetImplementedError(
        f"plan generation failed: exhausted retries (last error: {last_error})"
    )


def confirm_plan_node(state: DeepResearchState) -> dict:
    """Pause for user plan confirmation (spec 002 FR-007 + FR-008).

    - ``auto_confirm_plan=True`` → return ``{}`` without calling ``interrupt()``.
    - First call (or after ``plan_node`` re-generation) →
      ``interrupt({"plan": [PlanStep.model_dump(...) for ...]})``. On LangGraph
      1.2+ the graph is frozen in ``__interrupt__`` state and ``run_research``
      returns the state dict (with ``__interrupt__`` key) as a normal result —
      it does NOT raise ``GraphInterrupt``.
    - ``Command(resume="approve")`` (or any non-edit string) → return ``{}``.
    - ``Command(resume="edit: <text>")`` (len > 5) → append a ``HumanMessage``
      to ``state["messages"]`` so the next ``plan_node`` pass picks it up via
      ``_extract_user_feedback``. The conditional edge in ``graph.py`` routes
      back to ``plan`` when the most recent message starts with ``"edit:"``.

    We deliberately do NOT return ``Command(goto="plan", update=...)`` here:
    LangGraph 1.2.x rejects multi-write to the same LastValue channel in one
    tick, and the ``plan`` channel would receive both the existing value
    (carried over by the Command routing) and the fresh value from
    ``plan_node``. A conditional edge avoids the write conflict entirely.
    """
    if state.get("auto_confirm_plan", False):
        # Short-circuit: skip interrupt for Lesson 0007 experiments.
        return {}

    # spec 002 FR-007 + research §R6: payload must be a dict (not a list) and
    # PlanStep instances must be serialized via model_dump(mode="json") so
    # HttpUrl and other Pydantic types round-trip through msgpack.
    # On RESUME the state was deserialized from the Postgres checkpoint by
    # PydanticJsonPlusSerializer, which returns plain ``dict``s (not
    # ``PlanStep``).  Guard with ``hasattr`` so both shapes work.
    def _dump(s: Any) -> Any:
        return s.model_dump(mode="json") if hasattr(s, "model_dump") else s

    payload = {"plan": [_dump(s) for s in (state.get("plan") or [])]}

    user_decision: Any = interrupt(payload)

    # Resume handling per FR-008 + Q2/Q8:
    # - str starts with "edit:" and len > 5  →  append HumanMessage; conditional
    #   edge in graph.py will route back to "plan".
    # - otherwise (approve, unknown, non-str)  →  return {} to let the
    #   conditional edge send the flow to "research".
    if (
        isinstance(user_decision, str)
        and user_decision.startswith("edit:")
        and len(user_decision) > 5
    ):
        new_msg = HumanMessage(content=user_decision)
        return {"messages": [new_msg]}
    return {}
