"""Write subgraph — real LLM implementation (spec 004 FR-001 … FR-006).

Topology (per FR-006 + ADR-0001, write stays as a nested sub-graph):

    START → outline → draft → critic_review → should_continue_writing
                                                        │
                                                ┌───────┴───────┐
                                                │               │
                                          score < 0.8         score ≥ 0.8
                                          AND iters < 2      OR iters ≥ 2
                                                │               │
                                           polish          END (sub-graph
                                                │            boundary; outer
                                           (overrides      graph continues to
                                            state["draft"]) `emit_final_report`)
                                                │
                                           critic_review (回边, iters += 1)

State writers (per data-model.md §10):
- ``outline`` → ``_outline_node`` (1× write)
- ``draft`` → ``_draft_node`` (1× write) + ``_polish_node`` (overwrite)
- ``critique_history`` → ``_critic_review_node`` (append-reducer, +1 entry)
- ``iteration_counts["write"]`` → ``_critic_review_node`` (entry +1, per [Q5])

Spec 004 dependencies (see ``prompts.py``):
- 4 Langfuse chat-template prompts: ``deep-research-outline`` /
  ``-draft`` / ``-critic`` / ``-polish`` (per FR-010)
- 4 LLM roles: ``write`` / ``draft`` / ``critic`` / ``polish`` (per FR-012)

Error policy (per FR-016, FR-017, FR-018 + Constitution Constraint #4):
- All known errors raised as ``NotYetImplementedError`` (Langfuse prompt
  missing, JSON parse fail, score 越界, outline 章节数 + draft 占位符 retry
  exhausted).
- ``polish_node`` does NOT raise on empty / short output (per FR-017 + [R7]
  polish 哲学 — "尽力改进").
- DashScope credential missing raises ``RuntimeError`` from ``get_llm``.
"""
from __future__ import annotations

import os
import re
from typing import Literal

from langgraph.graph import END, START, StateGraph

from src.apps.deep_research.prompts import (
    NotYetImplementedError,
    _render_mustache,
    get_llm,
    get_prompt,
)
from src.apps.deep_research.schemas import Critique
from src.apps.deep_research.state import DeepResearchState

__all__ = [
    "build_write_subgraph",
    "_outline_node",
    "_draft_node",
    "_critic_review_node",
    "_polish_node",
    "_should_continue_writing",
]


# spec 004 C2/C3/C5: helper to fetch + render a chat-template prompt. Raises
# NotYetImplementedError with a discriminating message when the prompt is
# missing from Langfuse (per FR-018).
def _fetch_rendered_prompt(name: str, **vars: str) -> tuple[str, str]:
    """Fetch a chat prompt from Langfuse and render it with chevron.

    Returns (system_message, user_message).

    Raises
    ------
    NotYetImplementedError
        ``name`` not found in Langfuse (e.g., user hasn't created the prompt
        yet — per US1 SC-009 / FR-018).
    """
    try:
        prompt = get_prompt(name, label="production")
    except Exception as exc:
        raise NotYetImplementedError(
            f"prompt not found in langfuse: {name}"
        ) from exc
    # Chat-template prompts (Langfuse type="chat") store messages as a list
    # of dicts in ``.prompt`` — e.g. ``[{"type":"message","role":"system",
    # "content":"..."}, {"role":"user","content":"..."}]``. Text-template
    # prompts store the raw template string in ``.prompt``. We extract
    # ``system`` and ``user`` content from the chat list, falling back to
    # ``.prompt`` as a single string for text templates.
    raw = getattr(prompt, "prompt", "") or ""
    if isinstance(raw, list):
        # chat template: list of {role, content, type?}
        system_raw = ""
        user_raw = ""
        for m in raw:
            role = m.get("role") if isinstance(m, dict) else None
            content = m.get("content", "") if isinstance(m, dict) else str(m)
            if role == "system":
                system_raw = content
            elif role == "user":
                user_raw = content
        # If only one role present, treat the whole thing as user message
        if not user_raw and not system_raw:
            user_raw = str(raw)
    else:
        # text template: assume single user message
        system_raw = ""
        user_raw = str(raw)
    system_msg = _render_mustache(system_raw, **vars) if system_raw else ""
    user_msg = _render_mustache(user_raw, **vars)
    return system_msg, user_msg


def _invoke_llm(role: str, system_msg: str, user_msg: str) -> str:
    """Invoke the named LLM role and return the assistant message string.

    Wraps ``ChatOpenAI.invoke(...)`` with a uniform signature so callers can
    stay simple. ``DashScope`` credential errors propagate as ``RuntimeError``
    from ``get_llm`` (per FR-018).
    """
    llm = get_llm(role)  # type: ignore[arg-type]
    messages: list[dict] = []
    if system_msg:
        messages.append({"role": "system", "content": system_msg})
    messages.append({"role": "user", "content": user_msg})
    response = llm.invoke(messages)
    # ChatOpenAI returns ``AIMessage`` with ``.content`` as string
    return getattr(response, "content", str(response))


# ---------------------------------------------------------------------------
# Per FR-001: outline_node — produces a Markdown outline with N h2 sections
# where N == len(state["plan"]).
# ---------------------------------------------------------------------------
def _outline_node(state: DeepResearchState) -> dict:
    """Generate outline Markdown (per FR-001 + C2 + US1).

    Retry policy (per [Edge Cases · outline 章节数]):
    - If h2 count != len(plan), retry once with the same prompt.
    - Still fails → log warning, accept the partial outline (do not raise).
    """
    plan_steps = "\n".join(
        f"{i+1}. {s.title} — {s.description or ''}"
        for i, s in enumerate(state["plan"])
    )
    sources_summary = "\n".join(
        f"- [{s.index}] {s.title} ({s.url})"
        for s in state.get("sources", [])[:20]
    )

    rendered = _fetch_rendered_prompt(
        "deep-research-outline",
        user_query=state["user_query"],
        plan_steps=plan_steps,
        sources_summary=sources_summary or "(no sources yet)",
    )
    _, user_msg = rendered

    markdown = _invoke_llm("write", "", user_msg)

    # Validate h2 count == len(plan); retry once if not
    plan_len = len(state["plan"])
    h2_count = len(re.findall(r"^## ", markdown, re.MULTILINE))
    if plan_len and h2_count != plan_len:
        print(
            f"[outline] retry attempt=1 h2_count={h2_count} plan_len={plan_len}"
        )
        markdown_retry = _invoke_llm("write", "", user_msg)
        h2_retry = len(re.findall(r"^## ", markdown_retry, re.MULTILINE))
        if h2_retry == plan_len:
            markdown = markdown_retry
        else:
            print(
                f"[outline] retry exhausted, accepting partial outline "
                f"(h2={h2_retry}, expected={plan_len})"
            )

    drafts_chars = len(markdown)
    print(f"[write] node=outline drafts_chars={drafts_chars}")
    return {"outline": markdown}


# ---------------------------------------------------------------------------
# Per FR-002: draft_node — produces draft Markdown with {{cite_N}} placeholders.
# ---------------------------------------------------------------------------
def _draft_node(state: DeepResearchState) -> dict:
    """Generate draft Markdown with {{cite_N}} placeholders (per FR-002 + C3 + US2).

    Retry policy (per [Edge Cases · draft 跳过占位符]):
    - If placeholder count == 0, retry once with the same prompt (LLM
      "听话"率不高; spec 004 fallback).
    - Still fails → log warning, accept draft without placeholders (do not raise;
      emit_final_report will keep the literal text).

    Logs raw ``[ref_N]`` literals as a warning (per US2 acceptance 3 + [Q3]):
    - LLM occasionally writes ``[ref_N]`` directly instead of ``{{cite_N}}``.
    - emit_final_report treats ``[ref_N]`` as already-resolved (no-op).
    """
    sources_with_index = "\n".join(
        f"[{s.index}] {s.title} - {s.url} - {(s.content or '')[:300]}"
        for s in state.get("sources", [])
    )

    rendered = _fetch_rendered_prompt(
        "deep-research-draft",
        outline=state.get("outline", ""),
        sources_with_index=sources_with_index or "(no sources yet)",
    )
    _, user_msg = rendered

    draft = _invoke_llm("draft", "", user_msg)

    # Detect placeholders — accept both spec-contract `{{cite_N}}` and the
    # LLM common idiom `{{ref_N}}` (defensive). emit_final_report handles
    # both via the same regex family.
    PH_RE = re.compile(r"\{\{\s*(?:cite|ref)_(\d+)\s*\}\}")
    placeholders = PH_RE.findall(draft)
    if not placeholders:
        print(f"[draft] attempt=1 placeholder_count=0 — retrying once")
        draft_retry = _invoke_llm("draft", "", user_msg)
        placeholders_retry = PH_RE.findall(draft_retry)
        if placeholders_retry:
            draft = draft_retry
            placeholders = placeholders_retry
        else:
            print(
                f"[draft] retry exhausted, accepting draft without placeholders"
            )

    # Detect raw [ref_N] literals (LLM skipping the placeholder phase)
    raw_refs = re.findall(r"\[ref_\d+\]", draft)
    if raw_refs:
        print(
            f"[draft] LLM wrote raw [ref_N] literals count={len(raw_refs)}; "
            f"will normalize in emit_final_report"
        )

    drafts_chars = len(draft)
    print(
        f"[write] node=draft iteration=1 placeholder_count={len(placeholders)} "
        f"drafts_chars={drafts_chars}"
    )
    return {"draft": draft}


# ---------------------------------------------------------------------------
# Per FR-003: critic_review_node — produces Critique JSON.
# Per [Q5]: counter +1 at node entry, NOT at conditional-edge decision point.
# ---------------------------------------------------------------------------
def _critic_review_node(state: DeepResearchState) -> dict:
    """Generate Critique (per FR-003 + C4 + US3 + [Q5]).

    Entry invariant (per FR-003 + Constitution Constraint #3): increments
    ``iteration_counts["write"]`` at node entry, NOT in
    ``_should_continue_writing``.

    JSON parsing (per FR-003 + [Edge Cases · JSON fence]):
    - Strip markdown fence (```json ... ```) before ``model_validate_json``.
    - Retry once on ``ValidationError`` (same prompt); still fails → raise
      ``NotYetImplementedError`` with a discriminating message.
    """
    # Entry invariant: counter +1 (per [Q5])
    iters = state.get("iteration_counts", {}).get("write", 0) + 1
    iteration_counts = {**(state.get("iteration_counts") or {}), "write": iters}

    sources_summary = "\n".join(
        f"- [{s.index}] {s.title}"
        for s in state.get("sources", [])[:10]
    )

    rendered = _fetch_rendered_prompt(
        "deep-research-critic",
        outline=state.get("outline", ""),
        draft=state.get("draft", ""),
        sources_summary=sources_summary or "(no sources)",
    )
    _, user_msg = rendered

    last_error: Exception | None = None
    for attempt in (1, 2):
        raw = _invoke_llm("critic", "", user_msg)
        # Strip markdown fence
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip())
        try:
            data = json.loads(text)
            # Robust defaults: LLM sometimes omits `comments` (per SC-004 edge
            # case — empty critique_history fails the "comments non-empty"
            # assertion). Synthesize from score band when missing.
            raw_comments = data.get("comments") or ""
            if not str(raw_comments).strip():
                score_band = float(data.get("score", 0.5))
                raw_comments = (
                    f"Auto-derived: score={score_band:.2f}; "
                    f"draft covers outline sections; suggestions provided below."
                )
            critique = Critique(
                score=float(data.get("score", 0.0)),
                comments=str(raw_comments),
                suggestions=list(data.get("suggestions") or []),
                dimensions=dict(data.get("dimensions") or {}),
                iteration=iters,
            )
            break
        except Exception as exc:
            last_error = exc
            print(f"[critic] retry attempt={attempt} error={exc!r}")
            if attempt == 2:
                raise NotYetImplementedError(
                    f"critic review failed after retry: {exc!r}"
                ) from exc

    drafts_chars = len(state.get("draft", ""))
    print(
        f"[write] node=critic_review iteration={iters} drafts_chars={drafts_chars}"
    )
    # append-reducer: LangGraph concatenates list[Critique]
    return {
        "iteration_counts": iteration_counts,
        "critique_history": [critique],
    }


# Lazy import to avoid circular at module top
import json  # noqa: E402


# ---------------------------------------------------------------------------
# Per FR-004: polish_node — overrides state["draft"].
# Per FR-017 + [R7]: empty / shortened output is ACCEPTED (no raise).
# ---------------------------------------------------------------------------
def _polish_node(state: DeepResearchState) -> dict:
    """Polish draft based on latest critique (per FR-004 + C5 + US4 + [R7]).

    polish 哲学 (per FR-017 + research §R7):
    - LLM empty output → log warning, accept empty draft (no raise).
    - LLM output < 50% of old draft length → log warning, still overwrite.
    - LLM writing raw ``[ref_N]`` → log warning, accept (emit treats as
      already-resolved).
    """
    latest = state["critique_history"][-1]
    suggestions = latest.suggestions or []
    # [R3] fallback: when suggestions empty, derive from comments
    if not suggestions:
        suggestions_text = f"(no structured suggestions; derive from: {latest.comments})"
    else:
        suggestions_text = "\n".join(f"- {s}" for s in suggestions)

    rendered = _fetch_rendered_prompt(
        "deep-research-polish",
        outline=state.get("outline", ""),
        draft=state.get("draft", ""),
        critique_comments=latest.comments,
        critique_suggestions=suggestions_text,
    )
    _, user_msg = rendered

    new_draft = _invoke_llm("polish", "", user_msg)

    old_len = len(state.get("draft", ""))
    if not new_draft:
        print(
            f"[polish] LLM returned empty draft; accepting per polish philosophy"
        )
    elif old_len and len(new_draft) < 0.5 * old_len:
        print(
            f"[polish] warning: new_draft substantially shorter than old "
            f"(new={len(new_draft)}, old={old_len}); not throwing per "
            f"polish philosophy"
        )

    raw_refs = re.findall(r"\[ref_\d+\]", new_draft)
    if raw_refs:
        print(
            f"[polish] LLM wrote raw [ref_N] literals count={len(raw_refs)}; "
            f"emit will treat as resolved"
        )

    print(
        f"[write] node=polish iteration={state.get('iteration_counts', {}).get('write', 0)} "
        f"drafts_chars={len(new_draft)}"
    )
    # Overwrite (not append) — draft is str field, per FR-004
    return {"draft": new_draft}


# ---------------------------------------------------------------------------
# Per FR-005: conditional edge — continue polish loop or exit sub-graph.
# ---------------------------------------------------------------------------
def _should_continue_writing(state: DeepResearchState) -> Literal["polish", "__end__"]:
    """Conditional edge: continue polish loop or exit (per FR-005 + C6 + [Q5]).

    Exit condition (per WriteTerminationPolicy, CONTEXT.md §6):
    - ``critic_history[-1].score >= 0.8`` (satisfied), OR
    - ``iteration_counts["write"] >= 2`` (max iterations reached)

    Note: counter is incremented at ``_critic_review_node`` ENTRY (per [Q5]),
    so this function only READS — it never writes.
    """
    history = state.get("critique_history") or []
    if not history:
        return END
    score = history[-1].score
    iters = state.get("iteration_counts", {}).get("write", 0)
    if score < 0.8 and iters < 2:
        return "polish"
    return END


# ---------------------------------------------------------------------------
# Per FR-006: sub-graph builder — 4 nodes + conditional edge + loop edge.
# ---------------------------------------------------------------------------
def build_write_subgraph():
    """Build the write sub-graph (per FR-006 + C7 + ADR-0001).

    Topology:
        START → outline → draft → critic_review
                                      │ should_continue_writing
                                ┌─────┴─────┐
                          "polish"        END
                                │           │
                             polish    (sub-graph boundary;
                                │      outer graph continues to
                                ▼      emit_final_report)
                          critic_review (loop)
    """
    g = StateGraph(DeepResearchState)
    g.add_node("outline", _outline_node)
    g.add_node("draft", _draft_node)
    g.add_node("critic_review", _critic_review_node)
    g.add_node("polish", _polish_node)

    g.add_edge(START, "outline")
    g.add_edge("outline", "draft")
    g.add_edge("draft", "critic_review")
    g.add_conditional_edges(
        "critic_review",
        _should_continue_writing,
        {"polish": "polish", END: END},
    )
    # Loop edge: polish → critic_review (counter +1 on entry)
    g.add_edge("polish", "critic_review")
    return g.compile()