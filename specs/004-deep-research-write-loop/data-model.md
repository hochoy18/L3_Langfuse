# Data Model · Spec 004（Write 阶段）

> Phase 1 产出：spec 004 涉及的实体、字段、生命周期、约束。spec 001/002/003 已定义的实体（Source / ResearchQuery / SearchEvaluation / Plan / Critique 等）不重定义，仅引用与细化。spec 004 唯一 schema 升级：`Critique` 加 `suggestions: list[str]`（per [Q4]）。

---

## 1. 实体清单

spec 004 直接涉及的实体（在 `src/apps/deep_research/schemas.py` 中已定义）：

| 实体 | spec 004 角色 | spec 001 引入 | spec 004 改动 |
|---|---|---|---|
| `Outline` | `_outline_node` LLM 输出，写入 `state["outline"]` | ✅ | 不改 schema |
| `Draft` | `_draft_node` 一次性写入 + `_polish_node` 覆盖写入 `state["draft"]` | ✅ | 不改 schema |
| `Critique` | `_critic_review_node` 写入 `state["critique_history"]`（append-reducer） | ✅ | **MINOR 升级：加 `suggestions: list[str]`** |
| `PolishedDraft` | 类型标签（polish 节点覆盖 `draft` 字段，不单独存 PolishedDraft） | ✅ | 不改 schema |
| `FinalReport` | `_emit_final_report_node` 写入 `state["final_report"]`（外层图第 5 节点） | ✅ | 不改 schema |
| `Reference` | emit 时动态渲染的中间变量（按 Source 派生，不入 state） | ✅ | 不改 schema |
| `Source` | spec 003 已重排为 1..N 全局唯一；spec 004 直接复用 | ✅ | 不改 schema（继承 spec 003） |

---

## 2. Outline 字段（spec 001 + spec 004 细化）

继承 spec 001 schemas.py 的定义：

```python
class Outline(BaseModel):
    # Markdown 字符串（含 N 个 h2 章节对应 PlanStep）
    markdown: str
```

**实装注意**：spec 001 时 `Outline` 实体仅作类型标签（state 字段是 `outline: str`）；spec 004 节点产出的 Markdown 直接写 `state["outline"] = markdown_str`，**不** 实例化 `Outline` Pydantic 实体（state 字段是 str 而非 Pydantic 实例，与 spec 001 FR-003 "集合用 Pydantic + 文本用 str" 一致）。

### spec 004 约束

| 字段 | 约束 | 来源 |
|---|---|---|
| `markdown` (state["outline"]) | 含 N 个 `## ` 章节（N == `len(state["plan"])`） | US1 SC-002 + FR-001 |
| 章节标题 | 包含对应 `PlanStep.title` 关键词（substring match 算通过） | US1 SC-002 |
| 章节数不一致 | `_outline_node` 重试 1 次（同 prompt 重 invoke） | [Edge Cases · outline 章节数] |

---

## 3. Draft 字段（spec 001 + spec 004 细化）

```python
class Draft(BaseModel):
    # Markdown 字符串（含 {{cite_N}} 占位符）
    markdown: str
```

**实装注意**：state 字段 `draft: str` 是文本字段（per spec 001 FR-003）；`Draft` Pydantic 实体保留作为类型标签但不强制实例化。

### spec 004 约束

| 字段 | 约束 | 来源 |
|---|---|---|
| `markdown` (state["draft"]) | 含 ≥ 5 处 `{{cite_N}}` 占位符 | US2 SC-003 + FR-002 |
| `{{cite_N}}` 中的 N | 合法 `Source.index`（`1 ≤ N ≤ len(state["sources"])`） | US2 SC-003 + [Q3] + spec 003 Q6 |
| LLM 直接写 `[ref_N]` | `_draft_node` log 警告但不抛错，emit 把 `[ref_N]` 视为合法引用 | US2 SC + [Q3] |
| polish 覆盖写入 | `_polish_node` 返回 `{"draft": new_draft}`，**覆盖**而非追加 | FR-004 + [Q5] |
| polish 大幅缩减 | `new_draft < 0.5 * old_draft` 时 log 警告但不抛错 | [Edge Cases · polish 后 draft 缩减过大] |

### State field 写入者矩阵

| 节点 | state["draft"] 行为 | 备注 |
|---|---|---|
| foundation stub | 一次性写入 `<stub draft>` | spec 001 完成态 |
| draft_node | 一次性写入真实 LLM 生成 Markdown | spec 004 FR-002 |
| critic_review_node | **不写** draft | spec 004 仅追加 critique_history |
| polish_node | **覆盖** draft（`{"draft": new_draft}`） | spec 004 FR-004 |
| emit_final_report_node | **不写** draft（仅读 draft + sources 加工 final_report）| spec 004 FR-007 |

---

## 4. Critique 字段（spec 001 + spec 004 MINOR 升级）

**spec 004 升级**：`Critique` 加 `suggestions: list[str]` 字段（per [Q4] + R3）。

```python
class Critique(BaseModel):
    score: float = Field(ge=0.0, le=1.0)
    comments: str
    suggestions: list[str] = Field(default_factory=list, max_length=10)  # spec 004 MINOR 升级
```

### 升级细节

- **`suggestions` 默认空 list**：旧 Critique 实例（spec 002/003 持久化的 Postgres checkpoint 数据）反序列化时 `suggestions=[]`，向后兼容（per R3）
- **`max_length=10`**：防御 LLM 输出过长 list（polish 节点读 11+ suggestions 时只取前 10 条）
- **`polish_node` fallback**：读 `critique.suggestions` 为空时，从 `critique.comments` 派生（LLM 解析 comments 字符串为多 bullet）——避免 spec 001/002/004 schema 兼容性问题

### spec 004 约束

| 字段 | 约束 | 来源 |
|---|---|---|
| `score` | `0.0 <= score <= 1.0`（Pydantic Field 校验）| spec 001 schema + US3 SC-004 |
| `score < 0` or `score > 1` | Pydantic ValidationError → critic_review_node 重试 1 次 | [Edge Cases · critic score 越界] + FR-016 |
| `comments` | 非空字符串 | US3 SC-004 + FR-003 |
| `suggestions` | list of str（可空 list）；max_length=10 | [Q4] + R3 + FR-014 |
| JSON fence（`` ```json ... ``` ``） | `_critic_node` 在 model_validate_json 之前先 strip fence | [Edge Cases · JSON fence] + FR-003 |
| JSON 解析失败 | 重试 1 次（同 prompt 重 invoke），仍失败抛 `NotYetImplementedError("critic review failed after retry: <reason>")` | FR-016 + US3 SC |

### State field 写入者矩阵

| 节点 | state["critique_history"] 行为 |
|---|---|
| foundation stub | 一次性写入 `[Critique(score=0.0, comments="<stub>")]` |
| draft_node | 不写 |
| critic_review_node | **追加** 1 条 Critique（`{"critique_history": [critique]}` append-reducer）|
| polish_node | 不写（polish 不评审自己） |
| emit_final_report_node | 不写 |

---

## 5. PolishedDraft 字段（spec 001 + spec 004 实体保留但不存 state 字段）

```python
class PolishedDraft(BaseModel):
    markdown: str
```

**实装注意**：`PolishedDraft` Pydantic 实体保留在 `schemas.py`（per spec 001 schema 设计），但 spec 004 不在 `DeepResearchState` 引入 `polished_draft` 字段——polish 节点覆盖 `state["draft"]` 字段而非单独存 PolishedDraft（per Assumption 章节）。

**为什么**：polish 节点**覆盖** draft 而不是单独存 PolishedDraft，避免 schema 字段膨胀；后续 polish 操作在 draft 上做，无需追溯历史 polished drafts（history 由 critique_history 记录）。

---

## 6. FinalReport 字段（spec 001 + spec 004 细化）

```python
class FinalReport(BaseModel):
    markdown: str
```

**实装注意**：state 字段 `final_report: str` 是文本字段；spec 004 emit_final_report 节点产出的 Markdown 直接写 `state["final_report"] = markdown_str`，**不** 实例化 `FinalReport` Pydantic 实体。

### spec 004 约束（emit_final_report_node 加工）

| 输出要求 | 验证方式 |
|---|---|
| 行内 `[ref_N]` 替换 `{{cite_N}}` 占位符 | `re.sub(r'\{\{cite_(\d+)\}\}', lambda m: f'[ref_{m.group(1)}]', draft_text)`（per FR-007 + R2） |
| 行内 `[ref_N]` 总数 ≥ `len(state["sources"])` | US5 SC-006 |
| `## References` 章节存在 | US5 SC-006 + US5 SC |
| References 章节按 Source.index 升序列出 title / url / score | US5 SC + ReferenceListPolicy（CONTEXT.md §6） |
| Source.index duplicate 防御 | `len(set(s.index for s in sources)) != len(sources)` 时 log + enumerate reindex（per FR-008 + R8） |
| 长度字段 | `len(state["final_report"]) > 100` 字符（防御空 final_report） |

---

## 7. Reference 字段（spec 001 + spec 004 emit 时动态生成）

```python
class Reference(BaseModel):
    index: int
    title: str
    url: HttpUrl
    score: float | None
```

**实装注意**：`Reference` 实体在 emit_final_report 节点**不存 state 字段**——它是 Source 在 final_report References 章节的视图。emit 时按 `Source` 派生 `list[Reference]`，按 index 升序拼接为 Markdown 字符串：

```python
references_md = "\n".join(
    f"{i}. [{ref.title}]({ref.url}) — score: {ref.score:.2f}"
    for ref in sorted(references, key=lambda r: r.index)
)
```

---

## 8. Source 字段（spec 003 复用 + spec 004 不改）

spec 004 write 节点**不读** Source.score 做内容筛选（仅在 References 章节渲染时用）；spec 004 直接复用 spec 003 已重排的 1..N 全局唯一 index（per R6 + Q7）。Source 字段约束继承 [data-model.md of spec 003](./../003-deep-research-research-loop/data-model.md) §2 Source 字段。

---

## 9. DeepResearchState（spec 001 frozen + spec 004 不变）

```python
class DeepResearchState(TypedDict):
    user_query: str
    plan: list[PlanStep]
    research_queries: list[ResearchQuery]                # Annotated, _merge_queries_by_identity reducer
    sources: list[Source]                                # Annotated, operator.add reducer
    search_evaluations: list[SearchEvaluation]           # Annotated, operator.add reducer
    outline: str                                         # ← spec 004 写入
    draft: str                                           # ← spec 004 写入
    critique_history: list[Critique]                     # ← spec 004 写入，Annotated, operator.add reducer
    final_report: str                                    # ← spec 004 写入
    iteration_counts: dict[str, int]                     # spec 004 写 "write" key
    messages: list[AnyMessage]                           # Annotated, operator.add reducer
    auto_confirm_plan: bool
```

**spec 004 不引入新 state 字段**（per FR + Constitution Constraint #1）；仅改变以下字段的写入者：

| 字段 | spec 004 写入者 |
|---|---|
| `outline` | `_outline_node` 一次性写入 |
| `draft` | `_draft_node` 一次写入 + `_polish_node` 覆盖写入 |
| `critique_history` | `_critic_review_node` 每轮追加 1 条（append-reducer） |
| `final_report` | `_emit_final_report_node`（外层图第 5 节点）一次性写入 |
| `iteration_counts["write"]` | `_critic_review_node` 入口 `+1`（per R4 + [Q5]） |

---

## 10. State Transitions（spec 004 write 子图）

```
graph topology (外层图 5 节点 + 嵌套 2 子图):
                                                          
plan → confirm_plan → research → write → emit_final_report → END
                              │        │
                              │        └─ outline → draft → critic_review ⇄ polish
                              │                (END 是子图 boundary marker)
                              └─ research 是嵌套子图 (per spec 003, 不展开)
```

### write 子图内部转换

```text
START → outline → draft → critic_review → should_continue_writing
                                                    │
                                            ┌───────┴───────┐
                                            │               │
                                       score<0.8      score≥0.8
                                       AND iters<2     OR iters≥2
                                            │               │
                                       polish          END (退出子图)
                                            │
                                       (覆盖 state["draft"])
                                            │
                                       critic_review  (回边, iters+=1)
```

### state["iteration_counts"]["write"] 转换

| 时间点 | counter 值 | 来源 |
|---|---|---|
| graph.py 入口初始化 | 0 | spec 001 FR-004 + foundation Q4 |
| 第 1 轮 critic_review_node 入口 +1 | 1 | per R4 + FR-003 |
| 第 2 轮 critic_review_node 入口 +1（仅当 should_continue → POLISH）| 2 | per R4 |
| should_continue_writing 决策点 | 读 counter | 不写 |
| write 子图退出（END marker）| counter ∈ {1, 2} | 不变 |

### state["critique_history"] 转换

| 时间点 | critique_history 长度 | 来源 |
|---|---|---|
| foundation stub 完成 | 1（[Critique(score=0.0, comments="<stub>")]）| spec 001 |
| spec 004 完成态 | 1-2 条真实 Critique（含 score + comments + suggestions）| spec 004 |
| polish_node | 不追加（polish 不评审自己）| per FR-004 |
| emit_final_report_node | 不追加 | per FR-007 |

---

## 11. spec 001/002/003 vs spec 004 接口边界

spec 001 frozen / spec 002 写过的字段 / spec 003 写过的字段 / spec 004 写过的字段**互不重叠**：

| spec | state 字段写入 | state 字段读 |
|---|---|---|
| spec 001 | `iteration_counts: {research:0, write:0}` / `auto_confirm_plan=False`（graph.py 入口初始化） | — |
| spec 002 | `plan` / `messages`（追加）/ `iteration_counts["research"]` +1（plan 子图 stub） | `user_query` |
| spec 003 | `sources` / `search_evaluations` / `research_queries` / `iteration_counts["research"]` | `user_query` / `plan` |
| spec 004 | `outline` / `draft` / `critique_history` / `final_report` / `iteration_counts["write"]` | `user_query` / `plan` / `sources` |

**互不重叠**保证：(a) 后续 spec 改某字段不影响前置 spec；(b) 实装时按 spec 单独跑通，单元 / 集成测试可隔离；(c) Lesson 0007 实验可独立打开某个 spec 的功能（如"只用 spec 003 research 不跑 spec 004 write"）。

### 11.5 spec 阶段最终状态 vs 实装状态对照表

| 维度 | spec 阶段承诺 | research.md 校正 | 实装阶段（tasks） |
|---|---|---|---|
| LLM role 数 | 6 个（plan/research/write/draft/critic/polish）| 不变 | prompts.py::get_llm 6 个分支 |
| Critique schema | `score + comments + suggestions` | 不变 | schemas.py Critique 加 suggestions: list[str] |
| write 子图节点 | outline / draft / critic_review / polish / should_continue_writing | 不变 | nodes/write.py 5 个函数 |
| 外层图节点数 | 5（+ emit_final_report）| 不变 | graph.py add_node + 2 add_edge |
| counter +1 时机 | critic_review_node 入口 | 不变 | 函数体第一行 |
| `{{cite_N}}` 占位符 | draft 用，emit re.sub | 不变 | _draft_node prompt + emit_final_report_node re.sub |
| polish 哲学 | 尽力改进；空字符串不抛错 | 不变 | _polish_node 不写 try/except |
| Source.index 防御 | duplicate 时 enumerate reindex | 不变 | _emit_final_report_node 第一段 |

---

## 12. 错误 schema（spec 004）

| 错误类 | 抛出节点 | 触发条件 | 消息模板 |
|---|---|---|---|
| `NotYetImplementedError` | `_outline_node` | Langfuse prompt 缺失 | `"prompt not found in langfuse: deep-research-outline"` |
| `NotYetImplementedError` | `_outline_node` | 章节数与 plan 不一致（重试 1 次后仍失败） | log 警告，不抛错 |
| `NotYetImplementedError` | `_critic_review_node` | JSON 解析失败（重试 1 次后仍失败） | `"critic review failed after retry: <reason>"` |
| `NotYetImplementedError` | `_critic_review_node` | Critique score 越界（< 0 或 > 1）| ValidationError → 重试 1 次后仍失败 → 抛 |
| `RuntimeError` | `_outline_node` / `_draft_node` / `_critic_review_node` / `_polish_node` | `DASHSCOPE_API_KEY` 缺失 | `"DASHSCOPE_API_KEY not set; required for write subgraph"` |
| **不抛错**（polish 哲学）| `_polish_node` | LLM 输出空字符串 / 大幅缩减 draft | log 警告但不抛错（per FR-017 + R7）|
| **不抛错**（emit 哲学）| `_emit_final_report_node` | Source.index duplicate | log 警告 + enumerate reindex（per FR-008 + R6）|

---

## 13. Langfuse Prompt 契约（spec 004 新增 4 个 prompt）

详见 [contracts/README.md §C9](./contracts/README.md)。

---

## 14. 与 spec 005 Streamlit UI 的接口契约

spec 005 接力需要 `state["final_report"]` 字段渲染 Markdown 显示给用户。spec 004 输出契约保证：

- `state["final_report"]` 是合法 Markdown（per US5 SC-006 / SC-007）
- 包含 ≥ 5 处 `[ref_N]` 行内引用（per US5 SC）
- 包含 `## References` 章节列出所有 Source（per US5 SC + ReferenceListPolicy）

spec 005 UI 不需要额外字段——`final_report` 是 agent 端到端的最终输出。

---

## 总结

spec 004 数据模型 = spec 001 frozen schema + Critique MINOR 升级（suggestions）+ state 字段写入者矩阵（outline/draft/critique_history/final_report/iteration_counts["write"]）+ 外层图第 5 节点 emit_final_report + write 子图 4 节点循环。

无新 Pydantic 实体（Outline/Draft/PolishedDraft/FinalReport/Reference 5 个实体已在 spec 001 定义）；无新 state 字段（spec 001 11 字段不变）；无新 env var；无新 Python 依赖。**Purely additive** 在 spec 001/002/003 之上，per Constitution Principle I。