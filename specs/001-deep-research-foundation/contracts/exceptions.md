# Exception Contract: `NotYetImplementedError`

**Date**: 2026-10-18
**Feature**: 001-deep-research-foundation
**Source**: Q5 clarification; FR-009/FR-010 in spec.md

---

## Definition

```python
# In src/apps/deep_research/prompts.py
class NotYetImplementedError(RuntimeError):
    """Raised by foundation-spec stubs to signal 'replace me in a later spec'."""
    pass
```

Re-exported from `src/apps/deep_research/tools.py`:
```python
# In tools.py
from src.apps.deep_research.prompts import NotYetImplementedError  # re-export
```

---

## When raised

| Module | Function | Trigger | Message format |
|---|---|---|---|
| `prompts.py` | `get_prompt(name, label)` | Always (stub) | `f"get_prompt not yet implemented in foundation spec: {name}"` |
| `prompts.py` | `get_llm(role)` | Always (stub) | `f"get_llm not yet implemented in foundation spec: {role}"` |
| `tools.py` | `DashScopeWebSearch._run` | Always (stub) | `f"DashScopeWebSearch not yet implemented in foundation spec: query={query[:50]}"` |
| `tools.py` | `DashScopeReRank._run` | Always (stub) | `f"DashScopeReRank not yet implemented in foundation spec: query={query[:50]}"` |

---

## Catch pattern for downstream specs (002+)

```python
from src.apps.deep_research.prompts import NotYetImplementedError, get_prompt
from src.apps.deep_research.tools import DashScopeWebSearch

try:
    prompt = get_prompt("deep-research-plan", label="production")
except NotYetImplementedError:
    prompt = HARDCODED_PLAN_PROMPT  # fallback for offline / foundation mode
```

Specs 002-006 are expected to follow this pattern whenever they touch a stub.

---

## Why not `NotImplementedError` (Python builtin)?

Python's `NotImplementedError` is intended for **abstract methods** that subclasses must override. Using it for "this code path isn't wired yet" confuses readers and IDE refactoring tools. `NotYetImplementedError` makes the intent explicit.

## Why not a custom base class hierarchy?

We considered `DeepResearchError` → `StubError` → `NotYetImplementedError`, but the foundation spec only has one error category. A flat class is enough; the hierarchy can be added when spec 002 introduces real LLM errors (rate limits, timeouts) that need distinguishing.

---

## Subclass policy

Downstream specs MAY subclass `NotYetImplementedError` for finer-grained handling, but MUST NOT replace the base class. `try: ... except NotYetImplementedError: ...` MUST continue to catch all stubs in spec 001 + 002-006.
