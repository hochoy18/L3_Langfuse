# Tasks: Deep Research Foundation

**Input**: Design documents from `/specs/001-deep-research-foundation/`
- `plan.md` (required) — tech stack, structure
- `spec.md` (required) — 3 user stories (P1 topology, P2 smoke, P3 schemas)
- `data-model.md` (optional) — 12 Pydantic entities + 11-field state schema
- `contracts/api.md` + `contracts/exceptions.md` (optional) — `NotYetImplementedError` + `run_research` / `build_graph` signatures
- `research.md` (optional) — 10 locked decisions (D1-D10)
- `quickstart.md` (optional) — SC-001..SC-006 verification recipes

**Tests**: The spec's `## Success Criteria` (SC-001..SC-006) are measurable, so test tasks ARE part of this plan.

**Organization**: Tasks grouped by user story (P1, P2, P3) to enable independent implementation and testing of each.

**Branch**: `001-deep-research-foundation` | **Date**: 2026-10-18

---

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1, US2, US3)
- Include exact file paths in descriptions

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project initialization — create directory structure and `__init__.py` skeletons.

- [X] T001 Create `src/apps/deep_research/nodes/__init__.py` to make the nodes sub-package importable
- [X] T002 [P] Create `src/apps/deep_research/__init__.py` (initially empty; populated in T018)
- [X] T003 [P] Create `tests/apps/deep_research/__init__.py` and `tests/apps/deep_research/conftest.py` with shared fixtures (`fresh_thread_id`, `langgraph_db_uri`)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core infrastructure that MUST be complete before ANY user story can be implemented.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [X] T004 [P] Define `class NotYetImplementedError(RuntimeError)` plus stub `get_prompt(name, label)` and `get_llm(role)` in `src/apps/deep_research/prompts.py` (per FR-009 / D5 / contracts/exceptions.md)
- [X] T005 [P] Define `class DashScopeWebSearch(BaseTool)` and `class DashScopeReRank(BaseTool)` as stubs in `src/apps/deep_research/tools.py`; re-export `NotYetImplementedError` from `prompts` (per FR-010 / D5)
- [X] T006 Define `DeepResearchState` TypedDict in `src/apps/deep_research/state.py` with all 11 fields explicit per FR-003 (Pydantic entities for collections, `str` for text, `dict[str,int]` for counters, `bool` for control; `Annotated[..., operator.add]` on `sources` / `search_evaluations` / `critique_history` / `messages`)
- [X] T007 Define all 12 Pydantic entity models in `src/apps/deep_research/schemas.py` per FR-002: `UserQuery, Plan, PlanStep, ResearchQuery, Source, SearchEvaluation, Outline, Draft, Critique, PolishedDraft, FinalReport, Reference` (per data-model.md §1 field tables; NFR-001: `frozen=False`)

**Checkpoint**: Foundation ready — `from src.apps.deep_research.prompts import get_prompt, NotYetImplementedError` works; `from src.apps.deep_research.state import DeepResearchState` works; `from src.apps.deep_research.schemas import *` works. User story implementation can now begin.

---

## Phase 3: User Story 1 - Verify graph topology (Priority: P1) 🎯 MVP

**Goal**: `build_graph()` returns a compiled `StateGraph` whose topology visibly contains 4 top-level nodes (`plan` / `confirm_plan` / `research` / `write`) plus 2 nested sub-graphs, with no ImportError or circular import.

**Independent Test**: Run `python -c "from src.apps.deep_research.graph import build_graph; print(build_graph().get_graph().draw_mermaid())"` and confirm mermaid output mentions all 4 node names.

### Implementation for User Story 1

- [X] T008 [P] [US1] Implement `plan_node` (returns `{"plan": [PlanStep(title="<stub plan step 1>")]}`) and `confirm_plan_node` (returns `{}` when `state["auto_confirm_plan"]` is True, else `interrupt({"plan": state["plan"]})`) in `src/apps/deep_research/nodes/plan.py` (per FR-005 / FR-008)
- [X] T009 [P] [US1] Implement `build_research_subgraph()` in `src/apps/deep_research/nodes/research.py` with stub `gen_query` / `websearch` / `eval_search` writing `{"sources": [Source(title, url, content) × 2-3], "search_evaluations": [SearchEvaluation(...) × matching]}` and exiting immediately (per FR-005 / ADR-0001)
- [X] T010 [P] [US1] Implement `build_write_subgraph()` in `src/apps/deep_research/nodes/write.py` with stub `outline` / `draft` / `critic_review` / `polish` writing `{"outline": "<stub outline>", "draft": "<stub draft>", "critique_history": [Critique(score=0.0, comments="<stub critique>", iteration=1)], "final_report": "<stub final_report>"}` (per FR-005)
- [X] T011 [US1] Implement `build_graph()` in `src/apps/deep_research/graph.py` that constructs `StateGraph(DeepResearchState)`, adds the 4 top-level nodes, wires edges `START → plan → confirm_plan → research → write → END`, attaches `research` and `write` as compiled sub-graphs, and compiles with `PostgresSaver.from_conn_string(LANGGRAPH_CHECKPOINT_DB or default)` calling `.setup()` for table auto-creation (per FR-004 / FR-006 / ADR-0001 / D7)
- [X] T012 [US1] Add test `test_sc_001_topology` in `tests/apps/deep_research/test_foundation.py` that calls `build_graph()`, prints `draw_mermaid()`, and asserts all 4 node names appear in the output (per quickstart.md SC-001)

**Checkpoint**: At this point, `SC-001` passes. Topology is verified; nothing else is yet.

---

## Phase 4: User Story 2 - End-to-end smoke (Priority: P2)

**Goal**: `run_research()` drives the graph from START to END with stub nodes; final state has a non-empty `final_report`; PostgresSaver persists checkpoints; same `thread_id` reused twice does not leak state; `Command(resume=...)` without pending interrupt returns structured error (per Q3).

**Independent Test**: Run quickstart.md SC-002 / SC-004 / SC-005 commands and confirm all pass.

### Implementation for User Story 2

- [X] T013 [US2] Implement `run_research(input, *, thread_id=None, config=None) -> dict` in `src/apps/deep_research/graph.py` that: (1) generates uuid4 if `thread_id is None`; (2) initializes `state["iteration_counts"] = {"research": 0, "write": 0}` and `state["auto_confirm_plan"] = False` (overridable from input dict); (3) detects `Command(resume=...)` and returns `{"error": "no_pending_interrupt", "thread_id": <id>}` when no pending interrupt; (4) calls `graph.invoke(state, config={"configurable": {"thread_id": thread_id}, **config})`; (5) returns the final state (per FR-004 / FR-007 / D6)
- [X] T014 [US2] Add test `test_sc_002_end_to_end_smoke` in `tests/apps/deep_research/test_foundation.py` that invokes `run_research({"user_query": "test", "auto_confirm_plan": True}, thread_id="sc-002")` and asserts `final_report` is non-empty, `plan` has ≥1 `PlanStep`, `sources` has 2-3 `Source` instances, `search_evaluations` and `critique_history` are non-empty, and `iteration_counts == {"research": 0, "write": 0}` (per FR-005 / quickstart.md SC-002)
- [X] T015 [P] [US2] Add test `test_sc_004_checkpoints_auto_create` in `tests/apps/deep_research/test_foundation.py` that drops the `checkpoints` table via a conftest fixture, calls `build_graph()`, and verifies the table is recreated by querying `information_schema.tables` (per FR-006 / quickstart.md SC-004)
- [X] T016 [P] [US2] Add test `test_sc_005_no_state_leakage` in `tests/apps/deep_research/test_foundation.py` that calls `run_research` twice with the same `thread_id` but different `user_query`, asserts both runs complete and the second run's `user_query` is the new value (not the first run's), then queries Postgres to confirm two distinct `checkpoint_id` rows (per quickstart.md SC-005)
- [X] T017 [P] [US2] Add test `test_sc_002_no_pending_interrupt_contract` in `tests/apps/deep_research/test_foundation.py` that calls `run_research(Command(resume="approve"), thread_id="never-interrupted")` and asserts the return is `{"error": "no_pending_interrupt", "thread_id": "never-interrupted"}` (per FR-007 / D6 / quickstart.md Q3)

**Checkpoint**: At this point, `SC-002`, `SC-004`, `SC-005` pass. End-to-end smoke is verified.

---

## Phase 5: User Story 3 - Typed entity schemas (Priority: P3)

**Goal**: All 12 Pydantic entity models can be constructed, JSON-serialized, and JSON-deserialized without data loss; missing required fields raise `ValidationError`; importing any entity class does not make network calls; stubs raise `NotYetImplementedError` (not other exceptions).

**Independent Test**: Run quickstart.md SC-003 / SC-006 commands and confirm all pass.

### Tests for User Story 3

- [X] T018 [P] [US3] Add parametrized round-trip test in `tests/apps/deep_research/test_schemas.py` that for each of the 12 entities (`UserQuery, Plan, PlanStep, ResearchQuery, Source, SearchEvaluation, Outline, Draft, Critique, PolishedDraft, FinalReport, Reference`) constructs a minimal valid instance, calls `model_dump_json()` then `model_validate_json()`, and asserts the round-tripped object equals the original (per FR-002 / quickstart.md SC-003)
- [X] T019 [P] [US3] Add validation test in `tests/apps/deep_research/test_schemas.py` that for each entity tries to construct it with no arguments and asserts `pydantic.ValidationError` is raised (per FR-002 / quickstart.md SC-003)
- [X] T020 [US3] Add test `test_sc_006_no_hidden_dependencies` in `tests/apps/deep_research/test_foundation.py` that: (1) imports `src.apps.deep_research.graph` and asserts it returns within 1s (no network); (2) calls `build_graph()` and asserts it returns without raising; (3) calls `get_prompt("any")` and `get_llm("plan")` and asserts both raise `NotYetImplementedError` (per FR-009 / FR-010 / D5 / quickstart.md SC-006)

**Checkpoint**: At this point, `SC-001`, `SC-002`, `SC-003`, `SC-004`, `SC-005`, `SC-006` all pass. Foundation spec is fully verified.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Make the package importable as a public API and verify the spec checklist one more time.

- [X] T021 [P] Update `src/apps/deep_research/__init__.py` to re-export the public API: `build_graph`, `run_research`, `NotYetImplementedError` (from `.prompts`), and the 12 Pydantic entity classes from `.schemas`
- [X] T022 [P] Verify `src/apps/deep_research/README.md` exists (or create one) that points to `CONTEXT.md` and `docs/adr/0001-0006` per FR-001; if creating, document the file layout and the "stub by design" warning
- [X] T023 Run `pytest tests/apps/deep_research/ -v` and verify all tests pass (one execution for the full test suite)
- [X] T024 [P] Run quickstart.md SC-001..SC-006 commands end-to-end as a final integration check (outside pytest) to confirm the documented recipes still work

**Checkpoint**: All 6 SCs pass via both pytest and quickstart.md. Spec 001 ready for /speckit-implement.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately
- **Foundational (Phase 2)**: Depends on Setup completion — **BLOCKS all user stories**
- **User Stories (Phase 3-5)**: All depend on Foundational completion; can proceed in priority order (P1 → P2 → P3) or in parallel
- **Polish (Phase 6)**: Depends on all desired user stories being complete

### User Story Dependencies

- **User Story 1 (P1)**: Can start after Foundational (Phase 2) — no dependencies on other stories
- **User Story 2 (P2)**: Can start after Foundational (Phase 2) — depends on US1's `build_graph()` being present (T011)
- **User Story 3 (P3)**: Can start after Foundational (Phase 2) — does NOT depend on US1/US2 (entities are created in Phase 2)

### Within Each User Story

- Stub nodes (T008-T010) before `build_graph()` (T011)
- `build_graph()` (T011) before `run_research()` (T013)
- `run_research()` (T013) before US2's tests (T014-T017)
- Schemas (T007) before schema tests (T018-T019)

### Parallel Opportunities

- Phase 1: T001 + T002 + T003 can all run in parallel (different files)
- Phase 2: T004 + T005 can run in parallel; T006 + T007 can run in parallel after T004/T005
- Phase 3: T008 + T009 + T010 can run in parallel; T011 depends on all three; T012 depends on T011
- Phase 4: T015 + T016 + T017 (tests) can run in parallel after T013 + T014
- Phase 5: T018 + T019 can run in parallel; T020 depends on T004 (for `NotYetImplementedError` import)
- Phase 6: T021 + T022 can run in parallel; T023 + T024 depend on T021 + T022

---

## Parallel Example: User Story 1

```bash
# Launch all stub node implementations together:
Task: "Implement plan_node + confirm_plan_node in src/apps/deep_research/nodes/plan.py"
Task: "Implement build_research_subgraph() in src/apps/deep_research/nodes/research.py"
Task: "Implement build_write_subgraph() in src/apps/deep_research/nodes/write.py"

# Then (sequentially):
Task: "Implement build_graph() in src/apps/deep_research/graph.py"
Task: "Add SC-001 topology test in tests/apps/deep_research/test_foundation.py"
```

## Parallel Example: User Story 2

```bash
# After T013 (run_research) + T014 (SC-002 test):
Task: "Add SC-004 checkpoints auto-create test in tests/apps/deep_research/test_foundation.py"
Task: "Add SC-005 no state leakage test in tests/apps/deep_research/test_foundation.py"
Task: "Add no_pending_interrupt contract test in tests/apps/deep_research/test_foundation.py"
```

## Parallel Example: User Story 3

```bash
# T018 + T019 can run in parallel (different parametrized cases in test_schemas.py):
Task: "Add round-trip tests for 12 entities in tests/apps/deep_research/test_schemas.py"
Task: "Add required-field validation tests in tests/apps/deep_research/test_schemas.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup (T001-T003)
2. Complete Phase 2: Foundational (T004-T007)
3. Complete Phase 3: User Story 1 (T008-T012)
4. **STOP and VALIDATE**: Run `python -c "from src.apps.deep_research.graph import build_graph; print(build_graph().get_graph().draw_mermaid())"` and verify the 4-node topology
5. Optional: commit at this point as the first green checkpoint

### Incremental Delivery

1. Complete Setup + Foundational → Foundation ready (entities importable, stubs raise `NotYetImplementedError`)
2. Add US1 → Topology verified (MVP! — `build_graph().get_graph().draw_mermaid()` shows 4 nodes)
3. Add US2 → End-to-end smoke works (`run_research` reaches END, no LLM cost)
4. Add US3 → Schemas verified (12 entities round-trip; required fields validated)
5. Polish → `__init__.py` re-exports + README + final pytest + quickstart.md validation

### Parallel Team Strategy

With multiple developers (not applicable here since sole developer, but listed for completeness):

1. Team completes Setup + Foundational together
2. After Foundational:
   - Dev A: US1 (nodes + build_graph + topology test)
   - Dev B: US2 (run_research + smoke tests)
   - Dev C: US3 (entity round-trip tests)
3. US2 depends on US1's `build_graph`; US3 is fully independent of US1/US2
4. Polish phase: any developer

---

## Notes

- [P] tasks = different files, no dependencies on incomplete work
- [Story] label maps task to specific user story for traceability
- Each user story is independently completable and testable
- All stubs must return Pydantic instances (per Q1) — not dicts — to avoid reducer drift in 002-006
- All `run_research` calls with `Command(resume=...)` MUST handle the no-pending-interrupt case (per Q3) — handled in T013
- Stub nodes MUST NOT make network calls (NFR-002) — verified by SC-006 (T020)
- The graph MUST compile when Langfuse is unreachable (NFR-003) — verified implicitly because `prompts.get_prompt` is never called in foundation; verified explicitly by SC-006 (T020)
