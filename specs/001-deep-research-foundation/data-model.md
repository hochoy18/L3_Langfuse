# Data Model: Deep Research Foundation

**Date**: 2026-10-18
**Feature**: 001-deep-research-foundation
**Source**: `src/apps/deep_research/CONTEXT.md` §2 (entities) + spec.md FR-002 / FR-003 (state)

This document defines:
1. **14 Pydantic entity models** that make up the domain (`schemas.py`).
2. **11-field state TypedDict** that flows through the graph (`state.py`).
3. **Entity → state field mapping** and **field reducers**.

---

## 1. Entity Models (`schemas.py`)

All entities inherit from `pydantic.BaseModel` (NFR-001: `frozen=False` to allow state updates). Fields use `Field(...)` for documentation.

### 1.1 UserQuery
| Field | Type | Description |
|---|---|---|
| text | `str` | Raw research question from the user |
| thread_id | `str` | Optional caller-supplied thread id (default: auto-generated) |

### 1.2 Plan
| Field | Type | Description |
|---|---|---|
| steps | `list[PlanStep]` | Ordered list of research sections (per Q1: stub returns 1 step; real impl returns N) |

### 1.3 PlanStep
| Field | Type | Description |
|---|---|---|
| title | `str` | Section/chapter title (e.g. "Background", "Methodology") |
| description | `str \| None` | Optional longer prompt hint for the research stage |
| depends_on | `list[int]` | Indices of prerequisite steps (default: `[]`) |

### 1.4 ResearchQuery
| Field | Type | Description |
|---|---|---|
| text | `str` | The search query string |
| target_step | `int` | Index into `Plan.steps` this query serves |
| iteration | `int` | Which research iteration (1, 2, 3...) this query belongs to |

### 1.5 Source
| Field | Type | Description |
|---|---|---|
| index | `int` | Position in the final report's References list (1-based, matches `[ref_N]`) |
| title | `str` | Page/article title |
| url | `HttpUrl` | Canonical URL |
| content | `str` | Snippet or full content (truncated for token budget) |
| score | `float \| None` | Optional relevance score from ReRank (0.0–1.0) |
| query_index | `int` | Which `ResearchQuery` produced this source |

### 1.6 SearchEvaluation
| Field | Type | Description |
|---|---|---|
| source_index | `int` | Position in `state["sources"]` this evaluation rates |
| relevance | `float` | 0.0–1.0 (ReRank score or LLM Judge score) |
| rationale | `str` | One-sentence justification (LLM Judge only) |
| evaluator | `Literal["rerank", "llm_judge"]` | Which strategy produced this evaluation |

### 1.7 Outline
| Field | Type | Description |
|---|---|---|
| markdown | `str` | Skeleton markdown (h1 + h2 + h3 hierarchy) |
| step_to_section | `dict[int, str]` | Map `PlanStep.index → heading text` |

### 1.8 Draft
| Field | Type | Description |
|---|---|---|
| markdown | `str` | Full report draft (still missing `[ref_N]` resolution) |
| outline_ref | `str \| None` | Back-reference to the Outline that produced this draft |

### 1.9 Critique
| Field | Type | Description |
|---|---|---|
| score | `float` | Overall quality 0.0–1.0 (WriteTerminationPolicy threshold: 0.8) |
| comments | `str` | Free-form feedback from the LLM critic |
| dimensions | `dict[str, float]` | Optional per-dimension scores (e.g. `{"clarity": 0.9, "depth": 0.7}`) |
| iteration | `int` | Which write iteration (1, 2...) this critique belongs to |

### 1.10 PolishedDraft
| Field | Type | Description |
|---|---|---|
| markdown | `str` | Markdown after one polish iteration |
| parent_critique_index | `int` | Position in `state["critique_history"]` that motivated this polish |

### 1.11 FinalReport
| Field | Type | Description |
|---|---|---|
| markdown | `str` | The final markdown (with `[ref_N]` inline + `## References` section) |
| reference_count | `int` | Number of `## References` entries (= number of distinct sources used) |
| section_count | `int` | Number of h2 chapters (must equal `len(Plan.steps)`) |

### 1.12 Reference
| Field | Type | Description |
|---|---|---|
| ref_id | `int` | Matches the `[ref_N]` in `FinalReport.markdown` (1-based) |
| source | `Source` | Embedded `Source` (not a pointer — Reference is a value view of Source) |
| cited_in_sections | `list[int]` | Step indices where this reference is cited |

### 1.13–1.14: Reserved (out of spec scope)
- `ResearchIteration`, `WriteIteration` are **state counters** (in `iteration_counts`), not entities. Not in `schemas.py`.

---

## 2. State Schema (`state.py`)

11-field `TypedDict` with explicit reducers. Per Q2 clarification: Pydantic for collections, `str` for text, `dict` for counters, `bool` for control.

| Field | Type | Reducer | Init |
|---|---|---|---|
| `user_query` | `str` | (overwrite) | from input dict |
| `plan` | `list[PlanStep]` | (overwrite) | plan_node writes |
| `research_queries` | `list[ResearchQuery]` | (overwrite) | research gen_query writes |
| `sources` | `list[Source]` | `Annotated[..., operator.add]` | research websearch appends |
| `search_evaluations` | `list[SearchEvaluation]` | `Annotated[..., operator.add]` | research eval_search appends |
| `outline` | `str` | (overwrite) | write outline_node writes |
| `draft` | `str` | (overwrite) | write draft_node writes |
| `critique_history` | `list[Critique]` | `Annotated[..., operator.add]` | write critic_review appends |
| `final_report` | `str` | (overwrite) | emit_final_report writes |
| `iteration_counts` | `dict[str, int]` | (overwrite, full replace) | `run_research` entry writes `{"research": 0, "write": 0}` |
| `messages` | `list[AnyMessage]` | `Annotated[..., operator.add]` | (always empty in foundation; spec 002+ uses) |
| `auto_confirm_plan` | `bool` | (overwrite) | `run_research` entry writes `False`, overridable from input dict |

**Key invariant**: `Annotated[list[X], operator.add]` means the node returns a list and LangGraph appends to existing list. Nodes MUST return the **delta** (new items only), not the full list.

---

## 3. Relationships

```
UserQuery ── produces ──▶ Plan ── contains ──▶ PlanStep
                              │
                              ▼
                       ResearchQuery (multiple per step)
                              │
                              ▼ (websearch)
                           Source ──── evaluated by ────▶ SearchEvaluation
                              │
                              ▼ (after collect)
                       Reference (embedded view, used in FinalReport)

Outline ── uses ──▶ PlanStep (via Outline.step_to_section)
Draft ── produced from ──▶ Outline
Critique ── rates ──▶ Draft
PolishedDraft ── applies ──▶ Critique
FinalReport ── cites ──▶ Reference (via [ref_N] inline)
```

### Cardinality
- 1 `UserQuery` → 1 `Plan`
- 1 `Plan` → N `PlanStep` (N ≥ 1)
- 1 `PlanStep` → M `ResearchQuery` across all iterations (M = 5 per iteration × N iterations in real impl; 0 in stub)
- 1 `ResearchQuery` → K `Source` (K varies per websearch result)
- 1 `Source` → 0..1 `SearchEvaluation` (1 in real impl, 1 in stub per Q1)
- 1 `PlanStep` → 1 Outline section (1:1 in real impl)
- 1 `Outline` → 1 `Draft` → N `Critique` (N iterations)
- 1 `Draft` + N `Critique` → 1 `FinalReport` (after polish loop terminates)

---

## 4. State transitions (per graph topology)

```
[user_query="..."] 
       │
       ▼ plan_node
[plan=[PlanStep, ...]]
       │
       ▼ confirm_plan_node
       │ (interrupt when auto_confirm_plan=False; pass through when True)
       ▼
[research_queries=[ResearchQuery, ...]]  ◀── research subgraph ──┐
[sources=[Source, ...]]                                           │
[search_evaluations=[SearchEvaluation, ...]]                      │
[iteration_counts.research += 1]                                  │
       │ (when len(sources) >= 8 OR iters >= 3)                   │
       ▼ write subgraph ─────────────────────────────────────────┘
[outline="..."]
[draft="..."]
[critique_history=[Critique, ...]]
[iteration_counts.write += 1]
       │ (when critic.score >= 0.8 OR iters >= 2)
       ▼ emit_final_report
[final_report="..."] ──▶ END
```

**Stub behavior** (foundation spec):
- plan_node writes `plan = [PlanStep(title="<stub plan step 1>")]` once.
- Research subgraph writes `sources = [Source × 2-3]` + `search_evaluations = [SearchEvaluation × matching]` once, then exits immediately (no iteration loop, since `iteration_counts.research` is 0 and stub doesn't increment it — actually it WILL stay 0 in stub because stub doesn't touch it; spec 003 replaces this with real loop).
- Write subgraph writes `outline`, `draft`, `critique_history = [Critique × 1]`, `final_report` once, then exits.

**Foundation invariant**: state schema fields never shrink; they only grow or get overwritten. No field is ever removed.
