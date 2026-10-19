<!--
Sync Impact Report
==================
Version change: none (template) → v1.0.0 (initial ratification)
Modified principles:
  - PRINCIPLE_3_NAME: [PRINCIPLE_3_NAME] → "III. Test-First & Coverage Gate (NON-NEGOTIABLE)" (newly filled — per user request "增加一条单元测试优先，切覆盖率>=80%, TDD")
Added sections:
  - All template placeholders filled with project-derived content (LangGraph deep_research + Langfuse + DashScope stack; 7 project gates from plan.md §Constitution Check)
Removed sections: none
Deferred items: none — all placeholders filled
Bump rationale: Initial ratification (template → v1.0.0). User added PRINCIPLE_3 as a NEW substantive principle (Test-First + ≥80% coverage gate + TDD), which on its own would be a MINOR bump; combined with the initial ratification, we ship as v1.0.0.
-->

# L3_Langfuse Constitution

Project codename: **L3_Langfuse** — a Langfuse 实战教学 workspace built around a LangGraph 1.2+ deep-research agent (DashScope LLM + DashScope WebSearch/ReRank + Postgres checkpointer + Langfuse observability), with CI tied to a local `langfuse-deploy` stack.

This constitution governs how features are specified, implemented, tested, and shipped in this workspace. It supersedes informal conventions and applies to every `/speckit-*` workflow output and every PR merged into the default branch.

---

## Core Principles

### I. Module-First & Spec Compatibility (Library-First adaptation)

Every feature is implemented as a **self-contained module** under `src/apps/<app_name>/` with its own:

- `state.py` (TypedDict state schema — frozen once the first spec merges),
- `schemas.py` (Pydantic entities — frozen once the first spec merges),
- `graph.py` (outer graph + cross-process entry points),
- `nodes/` sub-package (per-subgraph node implementations),
- `prompts.py` (Langfuse prompt + LLM factory — only role slots that are specced),
- `tools.py` (external-service integrations — only stubs that are specced).

Modules MUST NOT introduce new top-level state fields, rename existing env vars, or
modify a frozen schema without an ADR + a spec amendment. (See Section 2 #1, #2.)

**Rationale:** the workspace is a teaching artifact; spec 001 sets the foundation
schema and every subsequent spec (`002` plan-confirm, `003` research-loop, `004`
write-loop, `005` Streamlit UI, `006` Langfuse hooks) MUST be purely additive.
The Q2 clarification (no `operator.add` reducer on Pydantic collections) and the
Q4 clarification (counters initialize at graph entry) are consequences of this
principle.

### II. CLI-Grade Smoke Tests (CLI Interface adaptation)

Every module MUST expose a runnable smoke-test entry point so a `python -c "..."`
single-line script can exercise the happy path end-to-end. Smoke tests:

- Read from environment (`.env`), never from hard-coded secrets.
- Print human-readable progress (e.g., `[retry] plan_retry_count=2`) and a final
  PASS/FAIL verdict with assertions.
- Are invoked from `quickstart.md` per spec; CI does NOT require a heavier
  framework in early specs.

**Rationale:** the project does not adopt pytest until spec 005; until then,
`quickstart.md`-style assertions are the contract test surface. The CLI pattern
also doubles as a debugging tool.

### III. Test-First & Coverage Gate (NON-NEGOTIABLE) — added per user request

Test-Driven Development (TDD) is **mandatory** for every new feature and every
bug fix:

1. **Tests written FIRST.** Every PR that adds or changes behavior MUST land its
   failing tests in the same change set (Red → Green → Refactor). Tests written
   after the implementation are a merge blocker.
2. **Coverage gate.** Line + branch coverage on changed/added code MUST be
   `>= 80%`. Coverage below the threshold is a merge blocker.
3. **Coverage categories.** The 80% gate MUST be hit across three categories of
   tests:
   - **Unit** — pure-function behavior, isolated via mocks/fakes (e.g., a
     `DashScopeWebSearch._run` mock returning `status_code=429` MUST have a
     matching test).
   - **Edge cases** — empty inputs, boundary values, `None`/`""`/empty-list
     paths, schema-rejected inputs.
   - **Error paths** — every `raise` site MUST have a matching test asserting the
     exception type and message fragment.
4. **Spec-driven test scope.** Tests are scoped to the spec's `contracts/`
   entries (`TC-1`, `TC-2`, …). `quickstart.md` SC scripts count as integration
   smoke tests and complement — they do NOT replace — the unit/edge/error
   coverage requirement.
5. **Coverage tool.** `pytest --cov=src/apps/<module> --cov-branch --cov-fail-under=80`
   is the canonical command; CI MUST run it for every PR (see Section 3).
6. **Exceptions.** Trivial scaffolding (an empty `__init__.py`, a one-line
   docstring) does not require tests. Any non-trivial exception MUST be
   documented in the spec's `contracts/` README.

**Rationale:** without TDD, the project accumulates "works on my machine" code
and silent regressions in long-lived LangGraph state. The 80% threshold is a
floor, not a ceiling — mission-critical paths (state mutation contracts,
external-API exception mapping) target higher. Coverage enforcement lives in
CI so the gate is mechanical, not a reviewer judgement call.

### IV. Integration Testing at Module Boundaries

Integration tests are required whenever a change crosses a module boundary:

- **Node ↔ Tool** — every LangGraph node MUST have an integration test that
  invokes it with a representative `state` dict and asserts the return shape
  (per the node's `contracts/ §C3` entry).
- **Tool ↔ External SDK** — DashScope / Langfuse / Postgres integrations MUST
  have at least one mock-based test for the **failure path** (HTTP 429, rate
  limit silent skip, network unreachable). Real-network tests are out of scope
  unless the spec explicitly opts in.
- **Schema ↔ State** — Pydantic serde round-trips (via `PydanticJsonPlusSerializer`)
  MUST have a property-style test asserting `model.model_dump(mode="json")`
  then re-load equals the original.

**Rationale:** the LangGraph sub-graph reducer doubling bug (per the
`LangGraph sub-graph reducer doubling` project memory) and the
`HttpUrl msgpack non-native` serde bug both shipped because no integration
test covered the boundary. The principle is the prevention.

### V. Observability, Versioning, Simplicity

- **Observability.** Every external call (DashScope LLM / WebSearch / ReRank,
  Langfuse prompt fetch, Postgres checkpoint write) MUST log a single-line
  `[retry]` / `[research]` / `[rerank]` style entry including a numeric
  counter and elapsed time. Structured logging (JSON) is deferred to spec 006
  Langfuse hook integration.
- **Versioning.** Specs follow SemVer: MAJOR for schema/contract breaking
  changes, MINOR for new functionality, PATCH for clarifications.
  Constitutions follow SemVer too — see Governance.
- **Simplicity.** YAGNI. The default DashScope model is pinned to one entry
  in `DASHSCOPE_MODEL`; the LLM factory exposes exactly the roles specced
  for the current release (others raise `NotYetImplementedError`). Do NOT
  pre-wire hooks, prompt slots, or env vars for un-specced features.

---

## Project Constraints

These constraints are derived from the `specs/<NNN>-*/plan.md` §Constitution
Check tables and from existing ADRs (`docs/adr/0001` … `0006`). They apply to
every PR.

1. **State schema invariants.** Do NOT add top-level fields to
   `DeepResearchState`; do NOT rename `iteration_counts` keys or any
   `env var` declared by an earlier spec. New state goes into
   `iteration_counts[<new_key>]` or as a field on an existing Pydantic
   entity. (`spec 001 FR-003` + `spec 002 FR-016`)
2. **Prompt text never in source.** Every prompt MUST be fetched from
   Langfuse at runtime via `prompts.get_prompt(name, label=...)`; the
   Python source MUST NOT contain prompt body literals. (`spec 002 NFR-003`)
3. **`iteration_counts` increment discipline.** The "start a new round"
   semantic is uniform: increment the counter at the **node entry**, not
   at the conditional-edge decision point. (`spec 002 NFR-002` + `spec 003 Q4`)
4. **Error propagation contract.** DashScope silent skips
   (`status_code != 20000`, `search_results is None or empty`) MUST raise
   an explicit `NotYetImplementedError` with a discriminating message —
   never swallowed. ReRank failure is the one exception (silent degrade
   to LLM Judge path per `spec 003 FR-017`). (`spec 002 FR-006` + ADR-0002)
5. **Postgres is the only state source.** No in-process caches for state
   that already lives in `checkpoints`. Advisory locks (`pg_advisory_xact_lock`)
   scope per `thread_id`. (`spec 002 FR-013` + ADR-0005)
6. **Mustache rendered client-side via chevron.** Langfuse 4.x
   `text.compile()` does NOT parse `{{#var}}` sections — we render with
   `chevron` so authors can use full Mustache. (`spec 002 FR-001 + FR-014`)
7. **Env-var hygiene.** New env vars require an ADR. Existing vars
   (`USE_RERANK`, `DASHSCOPE_API_KEY`, `DASHSCOPE_MODEL`,
   `DASHSCOPE_API_BASE_URL`, `LANGGRAPH_CHECKPOINT_DB`, `LANGFUSE_*`)
   MUST NOT be renamed.
8. **Library versions.** `pyproject.toml` pins `langgraph>=1.2.11`,
   `langchain-openai>=1.6.2`, `chevron>=0.14.0`, `psycopg[binary]>=3.3.5`,
   `langfuse>=3.0`, `dashscope>=1.14.0`. Adding a new dependency requires
   an ADR and a SPEC amendment; `pip install` ad-hoc is a merge blocker.

---

## Development Workflow

1. **Spec-driven only.** No code change lands without a `/speckit-specify`
   → `/speckit-plan` → `/speckit-tasks` → `/speckit-implement` trace in
   `specs/NNN-*/`. Ad-hoc fixes without a spec MUST be split into a new
   spec or explicitly noted as `out of scope` of the current spec.
2. **TDD enforced in CI.** The `pytest --cov=... --cov-fail-under=80` gate
   runs on every PR (added when spec 005 lands pytest). Until then, the
   spec's `quickstart.md` SC scripts MUST be runnable from a fresh clone
   and MUST exit non-zero on failure — that script is the contract test.
3. **Spec-conformance check.** Every PR description MUST cite the spec
   task IDs it closes (`T012`, `T034`, …). Reviewers MUST reject PRs that
   close tasks not in the active spec.
4. **No silent fallbacks.** Code paths that "look like they work" via a
   `try/except: pass` are forbidden unless explicitly specced
   (ReRank → LLM Judge degrade is the only currently-approved case).
5. **Plan-mode for non-trivial changes.** Any change touching
   `state.py`, `schemas.py`, `graph.py`, or introducing a new dependency
   MUST go through `/speckit-plan` (or the equivalent Claude Code plan
   mode) before implementation.
6. **Branch hygiene.** Each spec lands on its own branch
   (`NNN-<feature-slug>`) and merges to `master` via a single PR. The
   `M src/apps/deep_research/README.md` and untracked `specs/NNN-*/`
   combo is a code-review red flag — README MUST NOT advertise a feature
   the code does not yet contain.

---

## Governance

- **Supremacy.** This constitution supersedes all informal conventions,
  reviewer preferences, and AI-generated suggestions. Conflicts are
  resolved in favor of the constitution; if a conflict is unresolvable,
  propose an amendment.
- **Amendment procedure.**
  1. Open a PR that updates `.specify/memory/constitution.md`.
  2. The PR MUST include the Sync Impact Report at the top of the file
     (HTML comment) documenting version bump rationale, modified
     principles, added/removed sections, and deferred TODOs.
  3. Versioning follows SemVer:
     - **MAJOR** — backward-incompatible governance/principle removal or
       redefinition.
     - **MINOR** — new principle or section added, or materially
       expanded guidance.
     - **PATCH** — clarifications, wording, typo fixes, non-semantic
       refinements.
  4. Merge requires one reviewer approval AND no unresolved
     `TODO(<FIELD_NAME>)` markers in the body.
- **Compliance review.** Every `/speckit-plan` output MUST include a
  Constitution Check table mapping each Principle / Constraint to its
  status (`✓` / `✗` / `N/A`). A `✗` MUST be justified in the plan's
  Complexity Tracking section.
- **Runtime guidance.** Detailed runbooks live in
  `src/apps/deep_research/CONTEXT.md` and the per-spec `quickstart.md`;
  the constitution does not duplicate those.

---

**Version**: 1.0.0 | **Ratified**: 2026-10-19 | **Last Amended**: 2026-10-19
