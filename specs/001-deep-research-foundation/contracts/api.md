# API Contracts: Deep Research Foundation

**Date**: 2026-10-18
**Feature**: 001-deep-research-foundation

This document defines the public API surface of `src.apps.deep_research` for the foundation spec. All signatures are locked; downstream specs (002–006) MAY add new functions but MUST NOT change the signatures below.

---

## 1. `graph.py` — Primary entry points

### 1.1 `build_graph() -> CompiledStateGraph`

Builds and returns the compiled `StateGraph` with checkpointer attached.

**Returns**: A `langgraph.graph.state.CompiledStateGraph` instance, ready to `.invoke()` / `.stream()`.

**Side effects**:
- Reads `LANGGRAPH_CHECKPOINT_DB` env var.
- Opens a Postgres connection; calls `checkpointer.setup()` to auto-create the `checkpoints` table on first call (per FR-006 / SC-004).
- Idempotent: calling `build_graph()` twice returns two independent compiled graphs (each with its own checkpointer connection). Prefer module-level singleton for performance, but the function itself is pure.

**Raises**:
- `psycopg.OperationalError` (or `psycopg2.OperationalError`) if Postgres is unreachable. Message MUST include the connection string host:port (not credentials) for debuggability.

**Example**:
```python
from src.apps.deep_research.graph import build_graph
g = build_graph()
print(g.get_graph().draw_mermaid())  # SC-001
```

---

### 1.2 `run_research(input: dict | Command, *, thread_id: str | None = None, config: dict | None = None) -> dict`

Runs the deep research graph end-to-end (or resumes from interrupt).

**Parameters**:
- `input`: Either
  - `dict` — initial state. MUST contain at least `user_query: str`. MAY also contain `auto_confirm_plan: bool` (default `False`).
  - `langgraph.types.Command` — resume payload. Typically `Command(resume=<user_input>)` after an interrupt.
- `thread_id` (keyword): LangGraph thread id. If `None`, a `uuid4()` is generated. Caller SHOULD pass an explicit id for cross-process resume (Streamlit).
- `config` (keyword): Forwarded to `RunnableConfig` (`recursion_limit`, callbacks, etc.). MUST NOT override `configurable.thread_id` (that comes from `thread_id`).

**Returns**: A `dict` representing the graph's final state. Shape matches `DeepResearchState` TypedDict (11 fields; see `data-model.md` §2).

**Special return** (per Q3): when `input` is a `Command(resume=...)` and the thread has no pending interrupt, returns `{"error": "no_pending_interrupt", "thread_id": <thread_id>}` instead of raising.

**Side effects**:
- Initializes `state["iteration_counts"] = {"research": 0, "write": 0}` (per Q4).
- Initializes `state["auto_confirm_plan"] = False` (overridable from input dict).
- Persists checkpoints to Postgres under `thread_id`.

**Raises**:
- `pydantic.ValidationError` if input dict fails schema validation (TBD: only if foundation adds input validation; not currently required).
- LangGraph's own errors bubble up unchanged (`GraphRecursionError`, etc.).
- Does NOT raise `GraphInterrupt` on `Command(resume=...)` without pending interrupt — see "Special return" above.

**Example**:
```python
# Initial run
from src.apps.deep_research.graph import run_research
result = run_research({"user_query": "what is X?", "auto_confirm_plan": True}, thread_id="t-1")
assert result["final_report"]  # SC-002

# After interrupt (real impl, spec 002):
from langgraph.types import Command
result = run_research(Command(resume="approve"), thread_id="t-1")
```

---

## 2. `prompts.py` — Langfuse Prompt Management stubs

### 2.1 `class NotYetImplementedError(RuntimeError)`

Stub-call sentinel exception. Exported from `prompts.py` and re-exported by `tools.py`. See [`exceptions.md`](./exceptions.md) for full contract.

### 2.2 `get_prompt(name: str, label: str | None = None) -> str`

Stub: raises `NotYetImplementedError("get_prompt not yet implemented in foundation spec")`.

**Parameters**:
- `name`: Prompt name in Langfuse (e.g., `"deep-research-plan"`).
- `label`: Optional Langfuse label (e.g., `"production"`, `"staging"`).

**Returns (real impl, spec 002+)**: Prompt text as `str`.

### 2.3 `get_llm(role: Literal["plan", "research", "write", "critic"]) -> ChatOpenAI`

Stub: raises `NotYetImplementedError("get_llm not yet implemented in foundation spec")`.

**Parameters**:
- `role`: Which LLM role to instantiate. The foundation spec does not pin the exact role strings, but the four listed are the canonical set (spec 002 introduces `plan`, spec 003 introduces `research`, spec 004 introduces `write` and `critic`).

**Returns (real impl, spec 002+)**: A `langchain_openai.ChatOpenAI` configured with the role-appropriate model (per ADR-0004: Qwen via DashScope).

---

## 3. `tools.py` — DashScope integration stubs

### 3.1 `class DashScopeWebSearch(BaseTool)`

Stub `BaseTool` subclass for DashScope web search.

**Stub behavior**: `__init__` succeeds; `_run(self, query: str, **kwargs)` raises `NotYetImplementedError("DashScopeWebSearch not yet implemented in foundation spec")`.

**Real behavior (spec 003)**: Calls `dashscope.Generation.call(model="qwen-max", prompt=query, enable_search=True)` and parses the result into `list[Source]`.

### 3.2 `class DashScopeReRank(BaseTool)`

Stub `BaseTool` subclass for DashScope ReRank.

**Stub behavior**: `__init__` succeeds; `_run(self, query: str, documents: list[str], **kwargs)` raises `NotYetImplementedError("DashScopeReRank not yet implemented in foundation spec")`.

**Real behavior (spec 003)**: Calls `dashscope.TextReRank.call(model="gte-rerank-v2", query=query, documents=documents)` and returns `list[dict]` with `index` and `relevance_score`.

---

## 4. `schemas.py` — Re-exports

All 14 Pydantic entity models are importable directly:

```python
from src.apps.deep_research.schemas import (
    UserQuery, Plan, PlanStep, ResearchQuery,
    Source, SearchEvaluation, Outline, Draft,
    Critique, PolishedDraft, FinalReport, Reference,
)
```

Field-level details: see [`data-model.md`](../data-model.md) §1.

---

## 5. `nodes/{plan,research,write}.py` — Internal node functions

These are **internal** (not re-exported from `__init__.py`), but their signatures are pinned so spec 003/004 can replace them.

### 5.1 `plan.plan_node(state: DeepResearchState) -> dict`

Stub: returns `{"plan": [PlanStep(title="<stub plan step 1>")]}`.

Real impl (spec 002): calls `prompts.get_prompt("deep-research-plan", label="production")` + `prompts.get_llm("plan")`, parses structured output.

### 5.2 `plan.confirm_plan_node(state: DeepResearchState) -> dict`

Stub: if `state["auto_confirm_plan"]` is True, returns `{}` (no-op, graph continues). If False, calls `interrupt({"plan": state["plan"]})` and the caller must `Command(resume=...)` to continue.

### 5.3 `research.build_research_subgraph() -> CompiledStateGraph`

Returns a compiled subgraph with 3 nodes: `gen_query` → `websearch × 5` (Send) → `eval_search`. Stub version writes 2–3 `Source` + matching `SearchEvaluation` and exits.

### 5.4 `write.build_write_subgraph() -> CompiledStateGraph`

Returns a compiled subgraph with 4 nodes: `outline` → `draft` → `critic_review` → `polish` (loop). Stub version writes one pass and exits.

---

## 6. Versioning policy

The foundation spec is **1.x**. Breaking changes to the API above (signature changes, return shape changes, removing a function) are forbidden and require a new spec. Additive changes (new optional params, new exported functions) are allowed in 002-006.
