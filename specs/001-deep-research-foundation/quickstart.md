# Quickstart: Verify Deep Research Foundation

**Date**: 2026-10-18
**Feature**: 001-deep-research-foundation

This is a **validation guide** for the foundation spec. Each section maps to one Success Criterion (SC-001 through SC-006) from the spec. Run the commands below after implementation; all should pass.

---

## Prerequisites

1. **Python 3.11+** with the project's `pyproject.toml` deps installed.
2. **`langfuse-deploy` Postgres running**:
   ```bash
   cd langfuse-deploy && docker compose up -d postgres
   # verify:
   docker compose ps postgres   # should show "running"
   psql "postgresql://postgres:postgres@localhost:5432/postgres" -c "SELECT 1"
   ```
3. **Env var** (optional; default in code follows ADR-0005):
   ```bash
   export LANGGRAPH_CHECKPOINT_DB="postgresql://postgres:postgres@localhost:5432/postgres"
   ```
4. **No Langfuse UI / DashScope required** — foundation stubs do not call either.

---

## SC-001: Graph topology compiles and prints

**What it proves**: The 4-node outer graph (plan / confirm_plan / research / write) + nested sub-graphs compile; no import errors.

```bash
python -c "from src.apps.deep_research.graph import build_graph; print(build_graph().get_graph().draw_mermaid())"
```

**Expected**: A non-empty mermaid string containing the four node names. Example (truncated):
```
graph TD
    START --> plan
    plan --> confirm_plan
    confirm_plan --> research
    research --> write
    write --> END
```

**Failure modes**:
- `ImportError: cannot import name 'build_graph'` → `graph.py` is not exporting the function.
- `mermaid` output missing `research` / `write` → sub-graphs not attached; check `graph.add_node("research", build_research_subgraph())`.
- Empty mermaid output → graph is not compiled; check `.compile()` is called.

---

## SC-002: End-to-end smoke reaches END

**What it proves**: Stub nodes drive the graph from START to END; `final_report` is non-empty; the state shape contract from FR-005 holds.

```bash
python <<'PY'
from src.apps.deep_research.graph import run_research
from src.apps.deep_research.schemas import PlanStep, Source, SearchEvaluation, Critique

result = run_research(
    {"user_query": "test", "auto_confirm_plan": True},
    thread_id="foundation-smoke",
)

# Final report is non-empty
assert isinstance(result["final_report"], str) and result["final_report"], "final_report empty"
print("final_report:", result["final_report"])

# Stub return shape contract (per FR-005)
assert isinstance(result["plan"], list) and len(result["plan"]) >= 1
assert all(isinstance(s, PlanStep) for s in result["plan"])

assert isinstance(result["sources"], list) and 2 <= len(result["sources"]) <= 3
assert all(isinstance(s, Source) for s in result["sources"])

assert isinstance(result["search_evaluations"], list) and len(result["search_evaluations"]) >= 1
assert all(isinstance(e, SearchEvaluation) for e in result["search_evaluations"])

assert isinstance(result["critique_history"], list) and len(result["critique_history"]) >= 1
assert all(isinstance(c, Critique) for c in result["critique_history"])

# iteration_counts initialized correctly (per Q4)
assert result["iteration_counts"] == {"research": 0, "write": 0}, result["iteration_counts"]

print("SC-002 OK")
PY
```

**Expected**: prints `final_report: <stub final_report>` then `SC-002 OK`.

**Failure modes**:
- `result["final_report"]` empty → stub `write` subgraph not writing; check `nodes/write.py`.
- `result["plan"]` empty → `plan_node` not writing; check `nodes/plan.py`.
- `iteration_counts` not `{"research": 0, "write": 0}` → `run_research` entry-point init not wired (per FR-004).

---

## SC-003: All 14 entities round-trip via Pydantic

```bash
python <<'PY'
import json
from src.apps.deep_research.schemas import (
    UserQuery, Plan, PlanStep, ResearchQuery,
    Source, SearchEvaluation, Outline, Draft,
    Critique, PolishedDraft, FinalReport, Reference,
)

# Construct one of each with minimal valid fields
samples = [
    UserQuery(text="x"),
    Plan(steps=[PlanStep(title="s1")]),
    PlanStep(title="s1"),
    ResearchQuery(text="q", target_step=0, iteration=1),
    Source(index=1, title="t", url="https://example.com", content="c", query_index=0),
    SearchEvaluation(source_index=0, relevance=0.9, rationale="r", evaluator="rerank"),
    Outline(markdown="# h1\n## h2", step_to_section={0: "h2"}),
    Draft(markdown="d"),
    Critique(score=0.5, comments="c", iteration=1),
    PolishedDraft(markdown="p", parent_critique_index=0),
    FinalReport(markdown="r", reference_count=1, section_count=1),
    Reference(ref_id=1, source=Source(index=1, title="t", url="https://example.com", content="c", query_index=0), cited_in_sections=[0]),
]

for entity in samples:
    raw = entity.model_dump_json()
    restored = type(entity).model_validate_json(raw)
    assert restored.model_dump() == entity.model_dump(), f"round-trip lost data: {type(entity).__name__}"
    # Required-field validation
    try:
        type(entity)()
    except Exception:
        pass  # expected: missing required fields
    else:
        raise AssertionError(f"{type(entity).__name__} accepted empty construction; should require fields")

print(f"SC-003 OK ({len(samples)} entities round-tripped)")
PY
```

**Expected**: prints `SC-003 OK (12 entities round-tripped)`.

Note: spec says 14 entities, but `ResearchIteration` and `WriteIteration` are state counters (per FR-002 note), so only 12 Pydantic models are tested.

---

## SC-004: First call auto-creates the `checkpoints` table

```bash
# 1. Drop the table (simulate fresh DB)
docker exec -i $(docker compose -f langfuse-deploy/docker-compose.yml ps -q postgres) \
  psql -U postgres -d postgres -c "DROP TABLE IF EXISTS checkpoints;"

# 2. Call build_graph() — it should auto-create
python -c "from src.apps.deep_research.graph import build_graph; g = build_graph(); print('built')"

# 3. Verify the table exists
docker exec -i $(docker compose -f langfuse-deploy/docker-compose.yml ps -q postgres) \
  psql -U postgres -d postgres -c "\dt checkpoints"
```

**Expected**: step 2 prints `built`; step 3 lists the `checkpoints` table.

**Failure modes**:
- `psycopg.OperationalError` on step 2 → Postgres not running or wrong connection string.
- Step 3 doesn't show `checkpoints` → `checkpointer.setup()` not called in `build_graph()`.

---

## SC-005: Re-invoking with same `thread_id` does not leak state

```bash
python <<'PY'
from src.apps.deep_research.graph import run_research
THREAD = "sc-005-thread"

# First run
r1 = run_research({"user_query": "first", "auto_confirm_plan": True}, thread_id=THREAD)
# Second run with same thread_id but different query
r2 = run_research({"user_query": "second", "auto_confirm_plan": True}, thread_id=THREAD)

# Both must complete (no exception, no deadlock)
assert r1["final_report"]
assert r2["final_report"]

# Both must reflect their own input — no state contamination
assert r1["user_query"] == "first"
assert r2["user_query"] == "second"

# Plan should also reflect the current run, not the previous one
assert all(s.title != "<stub plan step 1 - first run>" for s in r1["plan"] + r2["plan"])

print("SC-005 OK")
PY
```

Then verify the DB has **two distinct checkpoint rows** for this thread:
```bash
docker exec -i $(docker compose -f langfuse-deploy/docker-compose.yml ps -q postgres) \
  psql -U postgres -d postgres -c "SELECT checkpoint_id, thread_id FROM checkpoints WHERE thread_id = 'sc-005-thread';"
```

**Expected**: 2 rows with different `checkpoint_id` but same `thread_id`.

**Failure modes**:
- Only 1 row → `run_research` is reusing prior state without re-init. Check that `state["iteration_counts"]` and `state["auto_confirm_plan"]` are re-initialized.
- 2nd run deadlocks / times out → check `confirm_plan` stub correctly handles `auto_confirm_plan=True`.

---

## SC-006: No hidden dependency on Langfuse / DashScope

**What it proves**: All FR-001..FR-010 are verifiable without those services being up.

```bash
# 1. Confirm no network call on import
python -c "import src.apps.deep_research.graph"  # should NOT hang on network

# 2. Confirm no network call on build_graph
python -c "from src.apps.deep_research.graph import build_graph; build_graph()"  # should NOT hang

# 3. Confirm stub raises NotYetImplementedError
python <<'PY'
from src.apps.deep_research.prompts import get_prompt, get_llm, NotYetImplementedError

for fn, args in [(get_prompt, ("any",)), (get_llm, ("plan",))]:
    try:
        fn(*args)
    except NotYetImplementedError as e:
        print(f"OK: {fn.__name__} raised NotYetImplementedError: {e}")
    else:
        raise AssertionError(f"{fn.__name__} did not raise")
PY
```

**Expected**: all 3 commands return within 1 second; the third prints `OK: get_prompt raised NotYetImplementedError: ...` and `OK: get_llm raised NotYetImplementedError: ...`.

**Failure modes**:
- `import` or `build_graph()` hangs → some module is making a network call at import/compile time. Check NFR-003.
- Stub doesn't raise `NotYetImplementedError` → wrong exception class wired (per Q5 contract).

---

## Running all SCs as pytest

A single pytest file (`tests/apps/deep_research/test_foundation.py`) should cover all six SCs:

```python
# tests/apps/deep_research/test_foundation.py
import pytest
from src.apps.deep_research.graph import build_graph, run_research
from src.apps.deep_research.prompts import get_prompt, get_llm, NotYetImplementedError
from src.apps.deep_research.schemas import (
    PlanStep, Source, SearchEvaluation, Critique,
    UserQuery, Plan, ResearchQuery, Outline, Draft,
    PolishedDraft, FinalReport, Reference,
)

def test_sc_001_topology():
    g = build_graph()
    mermaid = g.get_graph().draw_mermaid()
    for node in ("plan", "confirm_plan", "research", "write"):
        assert node in mermaid, f"{node} missing from {mermaid}"

def test_sc_002_end_to_end_smoke():
    result = run_research({"user_query": "test", "auto_confirm_plan": True}, thread_id="sc-002")
    assert result["final_report"]
    assert isinstance(result["plan"], list) and len(result["plan"]) >= 1
    assert 2 <= len(result["sources"]) <= 3
    assert len(result["search_evaluations"]) >= 1
    assert len(result["critique_history"]) >= 1
    assert result["iteration_counts"] == {"research": 0, "write": 0}

@pytest.mark.parametrize("cls,sample", [
    (UserQuery, {"text": "x"}),
    (Plan, {"steps": [PlanStep(title="s")]}),
    # ... all 12 entities
])
def test_sc_003_round_trip(cls, sample):
    entity = cls(**sample)
    raw = entity.model_dump_json()
    restored = cls.model_validate_json(raw)
    assert restored.model_dump() == entity.model_dump()

def test_sc_004_auto_setup_checkpoints():
    # 1. drop the table (conftest.py fixture)
    # 2. build_graph() succeeds
    # 3. table exists
    g = build_graph()
    # additional assertion: query information_schema
    ...

def test_sc_005_no_state_leakage():
    r1 = run_research({"user_query": "first", "auto_confirm_plan": True}, thread_id="sc-005")
    r2 = run_research({"user_query": "second", "auto_confirm_plan": True}, thread_id="sc-005")
    assert r1["user_query"] == "first"
    assert r2["user_query"] == "second"

def test_sc_006_no_hidden_dependencies():
    import src.apps.deep_research.graph  # must not hang
    build_graph()  # must not hang
    with pytest.raises(NotYetImplementedError):
        get_prompt("any")
    with pytest.raises(NotYetImplementedError):
        get_llm("plan")
```

```bash
pytest tests/apps/deep_research/test_foundation.py -v
```

**Expected**: 6 tests pass (or 6 + N parametrized for SC-003).

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `ImportError: No module named 'src.apps.deep_research'` | `pyproject.toml` doesn't include `src` in `packages` | Add `src` to `[tool.setuptools.packages.find] where = ["src"]` |
| `psycopg.OperationalError: connection to server at "localhost" (::1), port 5432 failed` | langfuse-deploy Postgres not running | `cd langfuse-deploy && docker compose up -d postgres` |
| `mermaid` output empty | `build_graph()` not calling `.compile()` | Add `.compile(checkpointer=...)` at the end of `build_graph()` |
| Stub doesn't raise `NotYetImplementedError` | Old exception class was used | Migrate to `NotYetImplementedError(RuntimeError)` per Q5 |
| `LangGraph` import error | `langgraph<1.2.11` installed | `pip install 'langgraph==1.2.11'` |
