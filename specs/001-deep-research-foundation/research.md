# Research: Deep Research Foundation

**Date**: 2026-10-18
**Feature**: 001-deep-research-foundation
**Source spec**: [spec.md](./spec.md)

This document records the technical decisions that are **locked** for the foundation spec. Each decision references the source (ADR, Q-clarification, or upstream constraint) and lists alternatives that were rejected.

---

## D1. LangGraph version: 1.2.11

**Decision**: Pin to `langgraph==1.2.11` (latest stable as of 2026-10).

**Rationale**: The whole deep research design (interrupt / Command(resume) / Send API / PostgresSaver) is built on LangGraph 1.2.x features. Pinning avoids API drift mid-development.

**Alternatives considered**:
- `langgraph` 0.x — rejected: missing `Command(resume=...)` and Send API ergonomics.
- `langgraph` dev/trunk — rejected: unstable for a learning project.

**Source**: `pyproject.toml` already pins `langgraph==1.2.11`; ADR-0003 (interrupt) and ADR-0002 (Send) reference this version explicitly.

---

## D2. State field types: Pydantic entities for collections, `str` for free-form text

**Decision**:
- Collections (`plan`, `research_queries`, `sources`, `search_evaluations`, `critique_history`) → typed `list[EntityModel]`.
- Free-form text (`user_query`, `outline`, `draft`, `final_report`) → `str`.
- Counters (`iteration_counts`) → `dict[str, int]`.
- Control flag (`auto_confirm_plan`) → `bool`.

**Rationale**: Entity collections benefit from Pydantic validation (one schema source of truth = `CONTEXT.md` §2). Free-form text is LLM I/O and would just be wrapped-and-unwrapped otherwise.

**Alternatives considered**:
- All `dict` — rejected: no validation, downstream specs 002-006 would re-validate.
- All Pydantic — rejected: `FinalReport` Pydantic wrapping raw Markdown adds no value (no nested structure inside Markdown).

**Source**: Q2 clarification (2026-10-18) in `spec.md` `## Clarifications`.

---

## D3. Stub return shape: real Pydantic instances

**Decision**: Stub nodes return real Pydantic instances with minimal placeholder content:
- `plan_node` → `[PlanStep(title="<stub plan step 1>")]`
- Research subgraph → `[Source(title, url, content), ...]` × 2–3 + matching `[SearchEvaluation(...)]`
- Write subgraph → `outline="<stub outline>"`, `draft="<stub draft>"`, `[Critique(...)]` × 1, `final_report="<stub final_report>"`

**Rationale**: Reduces reducer / state schema drift when specs 002-006 replace stubs. A test asserting `state["sources"][0].url` works the same against stubs and real nodes.

**Alternatives considered**:
- Stubs return `dict` — rejected: every downstream spec would need `model_validate(state["sources"])` boilerplate.
- Stubs return Pydantic but with all fields `Optional` — rejected: loses validation, complicates type hints.

**Source**: Q1 clarification; FR-005 in spec.md.

---

## D4. `iteration_counts` initialized at graph entry, not by stubs

**Decision**: `run_research()` writes `state["iteration_counts"] = {"research": 0, "write": 0}` and `state["auto_confirm_plan"] = False` at the entry point. Stub nodes MUST NOT modify it.

**Rationale**: Spec 003/004 will read `iteration_counts["research"]` to check termination (`>= 3` or `>= 8 sources`). Initializing in `graph.py` means stub replacement does not need to know about counter initialization.

**Alternatives considered**:
- Empty `{}` with lazy init — rejected: every spec 003/004 node does `state["iteration_counts"].setdefault("research", 0)` boilerplate.
- Helper `bump(state, key)` — rejected: over-engineering for foundation.

**Source**: Q4 clarification; FR-004 in spec.md.

---

## D5. Stub error class: `NotYetImplementedError(RuntimeError)`

**Decision**: Custom exception class, exported from both `prompts.py` and `tools.py`.

```python
class NotYetImplementedError(RuntimeError):
    """Raised by foundation-spec stubs to signal 'replace me in a later spec'."""
    pass
```

**Rationale**: Specs 002+ need to catch stub calls uniformly and fall back to hardcoded values. `NotImplementedError` (Python's built-in) is for abstract methods; `RuntimeError` is too generic.

**Alternatives considered**:
- `NotImplementedError` — rejected: conflated with abstract-method semantics.
- `RuntimeError("...")` — rejected: other real errors are also `RuntimeError`; can't distinguish.
- `warnings.warn(...)` + raise — rejected: warn is silent; we want a hard fail.

**Source**: Q5 clarification; FR-009/FR-010 in spec.md.

---

## D6. `run_research` error contract: structured `no_pending_interrupt`

**Decision**: When `Command(resume=...)` is passed but no interrupt is pending, return `{"error": "no_pending_interrupt", "thread_id": <id>}` instead of raising.

**Rationale**: Streamlit UI (spec 005) needs a typed way to distinguish "user refreshed page" from "real error". A structured return is the cleanest API.

**Alternatives considered**:
- Let `langgraph.errors.GraphInterrupt` bubble up — rejected: leaks langgraph internals to UI; harder to test.
- Auto-rerun as fresh start — rejected: could re-charge LLM credits, override user's in-progress edits.
- Custom `NoPendingInterruptError` — rejected: structured return is more discoverable than exception class.

**Source**: Q3 clarification; FR-007 in spec.md.

---

## D7. PostgreSQL checkpointer: `langgraph.checkpoint.postgres.PostgresSaver`

**Decision**: Use `PostgresSaver` from `langgraph.checkpoint.postgres`, connection string from env var `LANGGRAPH_CHECKPOINT_DB`. Auto-create `checkpoints` table via `checkpointer.setup()` on first call.

**Rationale**: Reuses `langfuse-deploy`'s existing Postgres (no new infra); cross-process resume works (Streamlit spec 005 needs this); PostgresSaver is the official LangGraph persistence backend.

**Alternatives considered**:
- `SqliteSaver` — rejected: single-process only, breaks Streamlit cross-session resume.
- `MemorySaver` — rejected: lost on process restart.
- Custom checkpointer — rejected: re-inventing the wheel.

**Source**: ADR-0005; FR-006 in spec.md.

---

## D8. Graph topology: 4 top-level nodes, 2 nested sub-graphs

**Decision**:
```
START → plan → confirm_plan → research → write → END
                  ↓ (interrupt when auto_confirm_plan=False)
                  plan (loop back on reject)
```

Where `research` and `write` are compiled sub-graphs (`StateGraph` instances added as nodes via `.add_node("research", build_research_subgraph())`).

**Rationale**: Mirrors the design tree in CONTEXT.md §4; sub-graphs keep individual node counts manageable (research = gen_query / 5×websearch / eval_search = 7 nodes; write = outline / draft / critic / polish = 4 nodes).

**Alternatives considered**:
- 1 flat graph with 11 nodes — rejected: too hard to read; sub-graphs also let specs 003/004 swap in real impls without touching the outer graph.
- 3-level deep (outer / research / write-with-its-own-subgraphs) — rejected: over-engineering for the current design.

**Source**: ADR-0001; FR-004 in spec.md.

---

## D9. Streamlit UI: NOT in this spec

**Decision**: `app.py` (Streamlit entry point) is owned by spec 005. This spec only exposes `run_research()` and `build_graph()` for programmatic use.

**Rationale**: Spec 005 has its own scope (UI + session_state + interrupt resume). Mixing UI code into the foundation creates import cycles (`streamlit` is heavy, would slow foundation tests).

**Alternatives considered**:
- Add a minimal `app.py` here — rejected: scope creep; spec 005 is the right owner.

**Source**: Out of Scope section of spec.md; ADR-0006 (Streamlit) lists this explicitly.

---

## D10. Langfuse observability: NOT in this spec

**Decision**: `prompts.py` is a stub (raises `NotYetImplementedError`); no `@observe()` decorators; no `CallbackHandler`. Spec 006 owns observability.

**Rationale**: Spec 006 needs ALL real LLM calls to be in place (specs 002/003/004) before the trace tree is meaningful. Stub nodes have no LLM calls to trace.

**Alternatives considered**:
- Add `@observe()` to stub nodes — rejected: would log empty traces, polluting Langfuse UI.

**Source**: Out of Scope section of spec.md; ADR-0006.
