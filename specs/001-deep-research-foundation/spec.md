# Feature Specification: Deep Research Foundation

**Feature Branch**: `[001-deep-research-foundation]`

**Created**: 2026-10-18

**Status**: Draft

**Input**: User description: "@src/apps/deep_research/ 这个目录下已经生成了 adr 和 context 文档，如果使用 /speckit-specify 进行拆分实现，应该如何拆分"

**Source of truth**: This spec scopes **only** the foundation layer (package skeleton + schemas + state + empty graph topology + stub nodes). Detailed decisions live in `src/apps/deep_research/CONTEXT.md` and `src/apps/deep_research/docs/adr/0001-0006`. Subsequent specs (002-006) build on this one.

## Clarifications

### Session 2026-10-18

- Q: Stub 节点（plan_node / research 子图 / write 子图）应该返回什么形状的占位数据？→ A: 真实 Pydantic 实例 + 最小占位内容（Plan = `[PlanStep(title="…")]`，`sources = [Source(...)] × 2-3` 条，`final_report = "<stub final_report>"` 裸字符串）。原因是后续 spec 002-006 会逐步替换 stub，使用真实 Pydantic 类型可避免 reducer / 状态 schema 漂移。
- Q: state 各字段用什么类型？Pydantic 实体（`plan: list[PlanStep]` / `sources: list[Source]`）还是原始类型（`plan: list[dict]` / `sources: list[dict]`）？→ A: 集合用 Pydantic + 文本用 str。理由：实体集合（plan / sources / evaluations / critiques）有字段约束和验证价值，state 用 Pydantic 类型让 reducer 写入直接 `state["sources"] = [...]`；文本字段（user_query / outline / draft / final_report）是 LLM 进出字符串，强加 Pydantic 反而绑死结构。
- Q: `run_research()` 在收到 `Command(resume=...)` 但该 thread 当前**没有 pending interrupt** 时应该怎么处理？→ A: `run_research` 检测到无 pending interrupt 时返回 `{"error": "no_pending_interrupt", "thread_id": <thread_id>}` 结构化结果，UI 据此渲染提示。理由：让 langgraph 抛原始 `GraphInterrupt` 异常对 UI 不友好；自动重跑风险太高（可能重新计费、覆盖当前编辑）；结构化错误让 UI 层直接判断。
- Q: `state["iteration_counts"]` 的初始值和由谁写入？空 dict 由 stub 节点首次写入，还是 graph.py 入口就初始化为 `{"research": 0, "write": 0}`？→ A: graph.py 入口初始化为 `{"research": 0, "write": 0}`，stub 节点不动 counter；spec 003/004 节点读 +1。理由：research/write 终止条件读这个 counter；空 dict 方案导致每个 spec 都要先做 key 存在性检查。
- Q: `prompts.py::get_prompt()` 和 `tools.py::DashScopeWebSearch/DashScopeReRank` 这三个 stub 在被调用时应该抛**什么异常类**？→ A: 自定义 `class NotYetImplementedError(RuntimeError): pass`，spec 002+ 能精确捕获。理由：让 spec 002-005 可以 `except NotYetImplementedError as e: ...` 精确捕获并降级到 hardcoded 兜底；与 `RuntimeError` 区分（其他真实错误用 RuntimeError）；不与 Python 的 `NotImplementedError`（用于 abstract method）混淆。

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Verify graph topology (Priority: P1)

As a developer, I can compile the deep research agent's graph and see its full topology (plan → confirm_plan → research → write), so I have confidence that the foundation's structure matches the design before any real LLM call is wired.

**Why this priority**: Without a verifiable topology, every subsequent change risks breaking the orchestration. P1 because this is the prerequisite for all other specs (002-006).

**Independent Test**: A single Python invocation prints a graph visualization that visibly contains the 4-stage topology and the nested research/write sub-graphs.

**Acceptance Scenarios**:

1. **Given** the foundation spec is implemented, **When** the developer runs the prescribed smoke command, **Then** a non-empty graph representation is returned containing `plan`, `confirm_plan`, `research`, `write` nodes and the edges connecting them.
2. **Given** the foundation spec is implemented, **When** the developer inspects the compiled graph, **Then** `research` and `write` are visible as nested sub-graphs (not flat 9 nodes).
3. **Given** the foundation spec is implemented, **When** any import of `src.apps.deep_research.graph` happens, **Then** no ImportError or circular import errors occur.

---

### User Story 2 - End-to-end smoke (Priority: P2)

As a developer, I can invoke `run_research()` end-to-end with stub nodes and reach END with a placeholder `final_report`, proving that state reducers, the checkpointer, and the run loop all wire correctly before any real LLM call is added.

**Why this priority**: P2 because it depends on US1 (compilable graph) but unlocks the full pipeline integration test pattern reused by specs 002-006.

**Independent Test**: A single Python invocation with a stub `user_query` reaches END with a non-empty `final_report` field and a populated `thread_id`.

**Acceptance Scenarios**:

1. **Given** `auto_confirm_plan=True` (skips the user-interaction pause), **When** the developer invokes `run_research()` with a simple query, **Then** the graph reaches END and `result['final_report']` is a non-empty string.
2. **Given** the invocation completes, **When** the developer inspects the Postgres `checkpoints` table, **Then** at least one checkpoint row exists for the `thread_id` used.
3. **Given** the same `thread_id` is reused with a different input, **When** the developer invokes `run_research()` again, **Then** the run starts fresh without state contamination.

---

### User Story 3 - Typed entity schemas (Priority: P3)

As a developer, I have typed Pydantic models for every entity defined in `CONTEXT.md` §2, so downstream specs (002-006) have a stable data contract to operate against and never silently drift from the glossary.

**Why this priority**: P3 because the foundation can compile and smoke-test without typed entities (stubs use dicts), but typed entities prevent subtle bugs in later specs.

**Independent Test**: All entity classes round-trip JSON serialization and validation without errors.

**Acceptance Scenarios**:

1. **Given** the foundation spec is implemented, **When** the developer imports any entity class from `schemas.py`, **Then** a usable Pydantic BaseModel subclass is returned (not an empty placeholder).
3. **Given** a serialized entity JSON, **When** the developer validates it back via `Model.model_validate_json()`, **Then** the round-tripped object equals the original (no field loss).
4. **Given** an entity is missing a required field, **When** the developer tries to construct it, **Then** Pydantic raises a `ValidationError` with a clear field-level message.

---

### Edge Cases

- **Postgres unreachable on first run**: What happens when `langfuse-deploy`'s Postgres is not running when `build_graph()` is called? The system should fail fast with a clear connection error (not silently fall back to in-memory and lose interrupt state).
- **Missing Langfuse prompts**: What happens when `prompts.py::get_prompt()` is called for a prompt that doesn't exist in Langfuse? Stubs in this spec don't actually fetch prompts, but the API surface must exist and raise a clear "prompt not found" error rather than returning `None`.
- **Thread ID collision**: What happens when two parallel runs use the same `thread_id`? Per LangGraph semantics, this causes state corruption — the system should document that `thread_id` must be unique per run.
- **Schema evolution mid-development**: What happens when CONTEXT.md adds a new entity but the foundation spec is already implemented? The developer can add the new entity to `schemas.py` without breaking existing imports.

---

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The package MUST exist at `src/apps/deep_research/` with the file layout documented in `src/apps/deep_research/README.md` (CONTEXT.md and ADRs referenced, not duplicated).
- **FR-002**: The package MUST expose `schemas.py` defining Pydantic BaseModels for every entity in `CONTEXT.md` §2: UserQuery, Plan, PlanStep, ResearchQuery, Source, SearchEvaluation, Outline, Draft, Critique, PolishedDraft, FinalReport, Reference. (ResearchIteration and WriteIteration are state counters, not entities.)
- **FR-003**: The package MUST expose `state.py` defining a `DeepResearchState` TypedDict containing all 11 fields from the design tree, with the following explicit type per field (Pydantic entities for collections, `str` for free-form text, `dict` for counters, `bool` for control flags):
  - `user_query: str` — raw text input
  - `plan: list[PlanStep]` — Pydantic entity list
  - `research_queries: list[ResearchQuery]` — Pydantic entity list
  - `sources: Annotated[list[Source], operator.add]` — Pydantic entity list with append-reducer
  - `search_evaluations: Annotated[list[SearchEvaluation], operator.add]` — Pydantic entity list with append-reducer
  - `outline: str` — raw text
  - `draft: str` — raw text
  - `critique_history: Annotated[list[Critique], operator.add]` — Pydantic entity list with append-reducer
  - `final_report: str` — raw text (Markdown string)
  - `iteration_counts: dict[str, int]` — counter map, initialized to `{"research": 0, "write": 0}` by `graph.py::run_research()` at the entry point (per FR-004); no reducer (full overwrite); stub nodes MUST NOT modify it
  - `messages: Annotated[list[AnyMessage], operator.add]` — LangChain message list with append-reducer
  - `auto_confirm_plan: bool` — control flag (default `False`), no reducer
- **FR-004**: The package MUST expose `graph.py` defining `build_graph()` that returns a compiled StateGraph containing exactly 4 top-level nodes (plan / confirm_plan / research / write), where research and write are nested sub-graphs per ADR-0001. `run_research()` MUST initialize `state["iteration_counts"] = {"research": 0, "write": 0}` and `state["auto_confirm_plan"] = False` (overridable from input dict) at the entry point so stub nodes and downstream specs have a stable counter starting point.
- **FR-005**: The package MUST expose `nodes/{plan,research,write}.py` with stub functions sufficient to drive the graph from START to END with non-empty placeholder values, without invoking any real LLM. Stub return shapes are pinned as a contract for downstream spec replacement:
  - `plan_node` returns `state["plan"] = [PlanStep(title="<stub plan step 1>")]` (one-element list of `PlanStep`).
  - Research subgraph stubs return `state["sources"] = [Source(title="<stub source 1>", url="https://example.com/1", content="<stub content>"), Source(...)]` (2–3 `Source` instances) and `state["search_evaluations"] = [SearchEvaluation(source_index=0, relevance=0.9, rationale="<stub eval>")]` (one entry per source).
  - Write subgraph stubs return `state["outline"] = "<stub outline>"`, `state["draft"] = "<stub draft>"`, `state["critique_history"] = [Critique(score=0.0, comments="<stub critique>")]` (one entry), and `state["final_report"] = "<stub final_report>"` (raw string).
- **FR-006**: The package MUST wire `PostgresSaver` (via env var `LANGGRAPH_CHECKPOINT_DB`) per ADR-0005, with auto-setup of the `checkpoints` table on first run.
- **FR-007**: `run_research()` MUST accept both an initial `user_query` dict and a `Command(resume=...)` payload, returning the graph's final state. The error contract for malformed calls is: when a `Command(resume=...)` is received but the thread has **no pending interrupt**, `run_research()` MUST return a structured result `{"error": "no_pending_interrupt", "thread_id": <thread_id>}` instead of raising — so the UI layer (spec 005) can render a friendly message and stop. The function MUST also accept an optional `config: dict` parameter (forwarded to LangGraph's `RunnableConfig`) so callers can override `thread_id`, `recursion_limit`, etc.
- **FR-008**: The `auto_confirm_plan` state field MUST exist on `DeepResearchState` and be honored by the `confirm_plan` stub (skip interrupt when True) — this enables Lesson 0007's offline experiment mode without code changes.
- **FR-009**: `prompts.py` MUST expose `get_prompt(name, label)` and `get_llm(role)` API surfaces as stubs. When called during the foundation spec, both MUST raise a custom `NotYetImplementedError(RuntimeError)` with message `"<name> not yet implemented in foundation spec"` so subsequent specs (002-005) can `except NotYetImplementedError` to distinguish "stub" from "real code error" and fall back to hardcoded values. The class MUST be exported from `prompts.py` and `tools.py` so callers can import it.
- **FR-010**: `tools.py` MUST expose `DashScopeWebSearch` and `DashScopeReRank` BaseTool subclasses as stubs. When invoked during the foundation spec, both MUST raise the same `NotYetImplementedError` (re-exported from `tools.py`) so subsequent specs can import them without ImportError and catch stub calls uniformly.

### Key Entities *(include if feature involves data)*

All entities are defined in `src/apps/deep_research/CONTEXT.md` §2. This spec does not redefine them — it requires their typed representation in code. The 14 entities:

| Entity | Purpose |
|---|---|
| UserQuery | Research question input |
| Plan | Structured research plan (list of PlanStep) |
| PlanStep | One research section/chapter |
| ResearchQuery | One search query string |
| Source | One raw search result (URL/title/content/score) |
| SearchEvaluation | Relevance evaluation of one Source |
| Outline | Structured report outline (Markdown skeleton) |
| Draft | Initial report draft |
| Critique | LLM-as-Judge review of a Draft |
| PolishedDraft | Draft after one polish iteration |
| FinalReport | Markdown report with `[ref_N]` citations |
| Reference | Inline citation in FinalReport (view of a Source) |

### Non-Functional Requirements

- **NFR-001**: All Pydantic models MUST be immutable-safe (frozen=False to allow state updates) but use `Field(...)` annotations for clarity.
- **NFR-002**: Stub nodes MUST NOT make any network calls (zero LLM cost during foundation verification).
- **NFR-003**: The graph MUST compile successfully even when Langfuse is unreachable (Prompts are fetched lazily, not at compile time).

---

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A developer can execute `python -c "from src.apps.deep_research.graph import build_graph; print(build_graph().get_graph().draw_mermaid())"` and see a valid graph representation containing all 4 nodes (`plan`, `confirm_plan`, `research`, `write`) and the START/END markers.
- **SC-002**: A developer can execute `python -c "from src.apps.deep_research.graph import run_research; print(run_research({'user_query': 'test', 'auto_confirm_plan': True}, thread_id='foundation-smoke')['final_report'])"` and see a non-empty string returned (placeholder text from stub nodes). Additionally, the returned state MUST contain a non-empty `plan` list (≥1 `PlanStep`), a `sources` list (2–3 `Source` instances), a `search_evaluations` list (1+ entry), a `critique_history` list (1+ entry), and a `final_report` string — verifying the stub return shape contract from FR-005 holds end-to-end.
- **SC-003**: All 14 entities from CONTEXT.md §2 have a corresponding Pydantic class in `schemas.py`, and each successfully round-trips via `model_dump_json()` → `model_validate_json()` without data loss.
- **SC-004**: First call to `build_graph()` against a fresh Postgres (no pre-existing `checkpoints` table) succeeds and auto-creates the table without manual SQL.
- **SC-005**: Re-invoking `run_research()` with the same `thread_id` twice produces two distinct runs with two distinct checkpoints in Postgres (no state leakage).
- **SC-006**: All 10 functional requirements (FR-001 through FR-010) are verifiable by import + smoke test, with no hidden dependency on Langfuse / DashScope being reachable.

---

## Assumptions

- The Postgres instance from `langfuse-deploy` is reachable on `localhost:5432` (per ADR-0005). If not running, the developer must run `cd langfuse-deploy && docker compose up -d postgres` first.
- The `DASHSCOPE_API_KEY` env var does NOT need to be set for this spec (stubs don't make API calls).
- Langfuse UI is not required to be running for this spec (Prompts are only fetched when get_prompt() is called, not at graph compile time).
- The developer is the sole user (cai_he himself) — no multi-tenancy, no auth requirements for this foundation.
- Subsequent specs (002-006) will replace stubs incrementally; this spec does not commit to any specific LLM call patterns.

---

## Out of Scope

- Real LLM calls for plan generation (covered by spec 002)
- Real web search via DashScope (covered by spec 003)
- ReRank score evaluation (covered by spec 003)
- Real Draft/Critique generation (covered by spec 004)
- Streamlit UI (covered by spec 005)
- Langfuse observability hooks (covered by spec 006)

## Dependencies

- ADR-0001 (nested sub-graphs) — required for FR-004
- ADR-0005 (PostgresSaver) — required for FR-006
- CONTEXT.md §2 (entities) — required for FR-002

---

## Notes

- FR-005 now pins the stub return shapes (after Q1 clarification) so downstream specs (002-006) have a stable contract to replace; placeholder **text content** (e.g., the exact `<stub ...>` string) is still the implementer's choice.
- The 11-state-field schema matches what specs 002-006 will write to; if any spec needs an additional state field, it should add it via a follow-up spec, not by editing this foundation in place.
- The graph topology check (SC-001) intentionally uses `draw_mermaid()` as a black-box verifier — the implementer should not over-engineer this check.