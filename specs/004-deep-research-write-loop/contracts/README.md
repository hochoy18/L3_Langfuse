# Contracts · Spec 004

> Phase 1 产出：spec 004 引入 / 修改的对外契约（函数签名 + 行为约束 + 错误类型 + Langfuse prompt 模板）。

spec 004 不暴露新 HTTP / gRPC / CLI 接口；它是 LangGraph 图的内部节点实现 + 一个外层图新节点，所以"contract"是指 **Python 函数签名 + 调用契约 + 异常类型 + Langfuse prompt 占位符**。

---

## C1. `src.apps.deep_research.prompts.get_llm`（spec 004 增量）

### Signature（spec 004 FR-012 实装）

```python
def get_llm(
    role: Literal["plan", "research", "write", "draft", "critic", "polish"]
) -> ChatOpenAI:
    """Return a ChatOpenAI instance for the given role.

    spec 002 实装 role="plan"（temperature=0.4）。
    spec 003 实装 role="research"（temperature=0.2）。
    spec 004 新增 role="write"（0.4）/ "draft"（0.7）/ "critic"（0.2）/ "polish"（0.5）。
    """
```

### Contract（spec 004 4 个新 role）

| role | temperature | 与 spec 002/003 role 的差异 |
|---|---|---|
| `write` | 0.4 | 与 plan 相同（结构化章节生成）|
| `draft` | 0.7 | spec 004 新增（长文生成）|
| `critic` | 0.2 | 与 research 相同（评审要严格稳定）|
| `polish` | 0.5 | 折中 |

### Test Cases

```python
# TC-1: 4 个 role 都返回 ChatOpenAI
for role, expected_temp in [("write", 0.4), ("draft", 0.7), ("critic", 0.2), ("polish", 0.5)]:
    llm = get_llm(role)
    assert isinstance(llm, ChatOpenAI)
    assert llm.temperature == expected_temp

# TC-2: DASHSCOPE_API_KEY missing
import os
saved = os.environ.pop("DASHSCOPE_API_KEY", None)
try:
    with pytest.raises(RuntimeError, match="DASHSCOPE_API_KEY not set"):
        get_llm("write")
finally:
    if saved:
        os.environ["DASHSCOPE_API_KEY"] = saved
```

---

## C2. `src.apps.deep_research.nodes.write._outline_node`

### Signature

```python
def _outline_node(state: DeepResearchState) -> dict:
    """Generate outline (Markdown with N h2 sections for plan steps)."""
```

### Contract

| 条件 | 行为 |
|---|---|
| `prompts.get_prompt("deep-research-outline", label="production")` 缺失 | 抛 `NotYetImplementedError("prompt not found in langfuse: deep-research-outline")`（per US1 SC-009 + FR-018）|
| `os.environ.get("DASHSCOPE_API_KEY")` 为空 | 抛 `RuntimeError("DASHSCOPE_API_KEY not set; required for write subgraph")`（per FR-018）|
| outline LLM 输出 h2 章节数 ≠ `len(state["plan"])` | 重试 1 次（同 prompt 重 invoke）；仍失败 log 警告但不抛错（per [Edge Cases · outline 章节数]）|
| 成功 | 写 `state["outline"] = markdown_str`；返回 `{"outline": markdown_str}` |
| retry 计数 | log `print(f"[outline] retry attempt={N} h2_count={M}")` |

### Langfuse Prompt（[C9 §1](#deep-research-outline)）

---

## C3. `src.apps.deep_research.nodes.write._draft_node`

### Signature

```python
def _draft_node(state: DeepResearchState) -> dict:
    """Generate draft Markdown with {{cite_N}} placeholders."""
```

### Contract

| 条件 | 行为 |
|---|---|
| `prompts.get_prompt("deep-research-draft", label="production")` 缺失 | 抛 `NotYetImplementedError("prompt not found in langfuse: deep-research-draft")` |
| `DASHSCOPE_API_KEY` 缺失 | 抛 `RuntimeError`（per FR-018）|
| draft LLM 输出 `{{cite_N}}` 占位符数 = 0 | 重试 1 次（同 prompt 重 invoke，加强"必须用占位符"指令）；仍失败 log 警告但不抛错（per [Edge Cases · draft LLM 跳过占位符]）|
| draft LLM 直接写 `[ref_N]` | log `[draft] LLM wrote raw [ref_N] literals; will normalize in emit_final_report`，**不抛错**（per US2 SC + [Q3]）|
| 成功 | 写 `state["draft"] = markdown_str`；返回 `{"draft": markdown_str}` |
| 占位符日志 | `[write] node=draft iteration=1 placeholder_count=N`（per FR-016）|

### Langfuse Prompt（[C9 §2](#deep-research-draft)）

---

## C4. `src.apps.deep_research.nodes.write._critic_review_node`

### Signature

```python
def _critic_review_node(state: DeepResearchState) -> dict:
    """Review draft, return Critique. Increment iteration_counts["write"] at entry."""
```

### Contract

| 条件 | 行为 |
|---|---|
| 节点入口 | `state["iteration_counts"]["write"] += 1`（per FR-003 + R4 + Constitution Constraint #3）|
| `prompts.get_prompt("deep-research-critic")` 缺失 | 抛 `NotYetImplementedError("prompt not found in langfuse: deep-research-critic")` |
| `DASHSCOPE_API_KEY` 缺失 | 抛 `RuntimeError` |
| critic LLM 输出 JSON 解析失败（ValidationError）| 重试 1 次（同 prompt 重 invoke），仍失败抛 `NotYetImplementedError("critic review failed after retry: <reason>")`（per FR-016）|
| critic LLM 输出 JSON 含 `` ```json ... ``` `` fence | strip fence 后再 `model_validate_json`（per [Edge Cases · JSON fence]）|
| critic score 越界（< 0 或 > 1）| Pydantic ValidationError → 重试 1 次，仍失败抛 NotYetImplementedError |
| 成功 | 解析为 `Critique(score=X, comments=str, suggestions=list[str])`；返回 `{"critique_history": [critique]}`（append-reducer 模式）|
| log | `[write] node=critic_review iteration=N drafts_chars=3` |

### Langfuse Prompt（[C9 §3](#deep-research-critic)）

---

## C5. `src.apps.deep_research.nodes.write._polish_node`

### Signature

```python
def _polish_node(state: DeepResearchState) -> dict:
    """Polish draft based on latest critique. Override state["draft"]."""
```

### Contract

| 条件 | 行为 |
|---|---|
| `prompts.get_prompt("deep-research-polish")` 缺失 | 抛 `NotYetImplementedError("prompt not found in langfuse: deep-research-polish")` |
| `DASHSCOPE_API_KEY` 缺失 | 抛 `RuntimeError` |
| LLM 输出空字符串 | log `[polish] LLM returned empty draft; accepting per polish philosophy`，**不抛错**（per FR-017 + R7）|
| LLM 输出 `new_draft < 0.5 * old_draft` | log `[polish] warning: new_draft substantially shorter than old; not throwing per polish philosophy`，**不抛错**（per [Edge Cases · polish 后 draft 缩减过大]）|
| LLM 直接写 `[ref_N]` 而非 `{{cite_N}}` | log 警告；**不抛错**（emit 把 `[ref_N]` 视为合法）|
| critique.suggestions 为空 list | 从 `critique.comments` 派生（LLM 解析 comments 为多 bullet）——向后兼容（per R3）|
| 成功 | **覆盖**写 `state["draft"] = new_draft`；返回 `{"draft": new_draft}` |
| log | `[write] node=polish iteration=N drafts_chars=N` |

### Langfuse Prompt（[C9 §4](#deep-research-polish)）

---

## C6. `src.apps.deep_research.nodes.write._should_continue_writing`

### Signature

```python
def _should_continue_writing(state: DeepResearchState) -> Literal["polish", "__end__"]:
    """Conditional edge: continue polish loop or exit write subgraph."""
```

### Contract

```python
score = state["critique_history"][-1].score
iters = state["iteration_counts"]["write"]
if score < 0.8 and iters < 2:
    return "polish"
else:
    return END  # LangGraph subgraph boundary marker
```

| 条件 | 行为 |
|---|---|
| `critique_history` 为空 | 不应发生（`_critic_review_node` 总是先追加再返回）；fallback 到 `END`（safety）|
| `critique_history[-1].score >= 0.8` | 返回 `"__end__"`（critic 满意）|
| `iteration_counts["write"] >= 2` | 返回 `"__end__"`（强制退出）|
| 其他 | 返回 `"polish"`（继续迭代）|

---

## C7. `src.apps.deep_research.nodes.write.build_write_subgraph()`

### Signature

```python
def build_write_subgraph() -> StateGraph:
    """Build the write subgraph (outline → draft → critic_review ⇄ polish)."""
```

### Contract

```python
builder = StateGraph(DeepResearchState)
builder.add_node("outline", _outline_node)
builder.add_node("draft", _draft_node)
builder.add_node("critic_review", _critic_review_node)
builder.add_node("polish", _polish_node)
builder.add_edge(START, "outline")
builder.add_edge("outline", "draft")
builder.add_edge("draft", "critic_review")
builder.add_conditional_edges(
    "critic_review",
    _should_continue_writing,
    {"polish": "polish", END: END}
)
builder.add_edge("polish", "critic_review")
return builder.compile()
```

| 边界 | 行为 |
|---|---|
| 子图入口 | 接收外层 state["outline"] / "draft" / "critique_history" / "sources" / "plan" 子集（sub-state）|
| 子图退出 | END marker；外层图接续到 `emit_final_report` 节点 |
| Send fan-out | **不**用（write 是串行循环，与 research 子图不同）|
| state schema | 完全继承 `DeepResearchState`（无新字段）|

---

## C8. `src.apps.deep_research.graph._emit_final_report_node`

### Signature

```python
def _emit_final_report_node(state: DeepResearchState) -> dict:
    """Deterministic transformation: draft + sources → final_report Markdown."""
```

### Contract

| 条件 | 行为 |
|---|---|
| `state["sources"]` 中 `Source.index` 不唯一 | log `log [emit] Source.index duplicates detected; falling back to enumerate reindex`，用 `enumerate(sources, start=1)` 重新编号后 emit（per FR-008 + R6）|
| `state["draft"]` 中 `{{cite_N}}` 占位符 | `re.sub(r'\{\{cite_(\d+)\}\}', lambda m: f'[ref_{m.group(1)}]', draft_text)` 替换为 `[ref_N]`（per FR-007 + R2）|
| `{{cite_N}}` 中 N > len(state["sources"]) | log `[emit] dropped {{cite_N}} (no source with index=N)`，**不抛错**（per R2 Action Items）|
| `state["sources"]` 为空 | log 警告但仍 emit（References 章节为 `_No sources found._`）（per [Edge Cases · emit sources 为空]）|
| 成功 | 拼接 draft + `## References\n\n` + 每条 source 的 `1. [title](url) — score: X.XX\n`（按 index 升序）；写 `state["final_report"] = md_str`；返回 `{"final_report": md_str}` |
| log | `[write] node=emit_final_report final_report_chars=N refs_count=N` |

### 输出格式示例

```markdown
## 第一章：LangGraph 1.2 Send API

Send API 是 LangGraph 1.2 引入的 fan-out 原语 [ref_1]...

## 第二章：...

## References

1. [LangGraph Send API 文档](https://langchain-ai.github.io/langgraph/concepts/low_level/#send) — score: 0.92
2. [LangGraph 实战教学](https://example.com/blog) — score: 0.85
3. ...
```

---

## C9. Langfuse Prompt（spec 004 新增 4 个）

> 用户**手动创建**于 Langfuse UI（chat 类型，label="production"）。spec 004 不写创建脚本。chevron 客户端渲染。

### `deep-research-outline`

| 字段 | 值 |
|---|---|
| 用途 | outline 节点 LLM 输入 |
| 类型 | chat template |
| 占位符 | `{{user_query}}` / `{{plan_steps}}` / `{{sources_summary}}` |
| 渲染 | chevron 客户端（per spec 002 FR-001）|
| 占位符内容示例 | `user_query = "分析 LangGraph 1.2 Send API"` / `plan_steps = "1. Send API 基础\n2. Fan-out 实现\n3. 应用案例"` / `sources_summary = "- LangGraph 文档\n- 实战博客"` |

### `deep-research-draft`

| 字段 | 值 |
|---|---|
| 用途 | draft 节点 LLM 输入 |
| 类型 | chat template |
| 占位符 | `{{outline}}` / `{{sources_with_index}}` |
| 渲染 | chevron 客户端 |
| 占位符内容示例 | `outline = "## 第一章：...\n## 第二章：..."` / `sources_with_index = "[1] LangGraph 文档 - https://...\n[2] 实战博客 - https://..."` |

### `deep-research-critic`

| 字段 | 值 |
|---|---|
| 用途 | critic 节点 LLM 输入 |
| 类型 | chat template |
| 占位符 | `{{outline}}` / `{{draft}}` / `{{sources_summary}}` |
| 渲染 | chevron 客户端 |
| 输出 schema | `{score: float (0-1), comments: str (≤200 字), suggestions: list[str] (≤3 条, 每条 ≤50 字)}` |

### `deep-research-polish`

| 字段 | 值 |
|---|---|
| 用途 | polish 节点 LLM 输入 |
| 类型 | chat template |
| 占位符 | `{{outline}}` / `{{draft}}` / `{{critique_comments}}` / `{{critique_suggestions}}` |
| 渲染 | chevron 客户端 |
| 输出规范 | 仍含 `{{cite_N}}` 占位符（**不**替换为 `[ref_N]`，emit 统一替换）|

---

## C10. Env Var Schema（spec 004 不新增）

| Env Var | spec 004 用途 | 来源 |
|---|---|---|
| `DASHSCOPE_API_KEY` | 4 个 write LLM 调用必需 | spec 001/002 已有 |
| `DASHSCOPE_MODEL` | 4 个 write LLM 默认模型 | spec 002 已有 |
| `DASHSCOPE_API_BASE_URL` | DashScope OpenAI 兼容 base URL | spec 002 已有 |
| `LANGFUSE_*` | 拉 4 个 write prompt | spec 002 已有 |
| `LANGGRAPH_CHECKPOINT_DB` | Postgres advisory lock | spec 001 已有 |

**spec 004 不新增 env var**（per FR-019 + Constitution Constraint #7）。

---

## C11. State Schema 兼容（spec 004 不改 `DeepResearchState`，仅 Critique 加字段）

| 字段 | spec 004 状态 |
|---|---|
| `DeepResearchState` TypedDict | 不变（spec 001 frozen）|
| `PlanStep` Pydantic | 不变 |
| `ResearchQuery` Pydantic | 不变 |
| `Source` Pydantic | 不变 |
| `SearchEvaluation` Pydantic | 不变 |
| `Outline` Pydantic | 不变 |
| `Draft` Pydantic | 不变 |
| **`Critique` Pydantic** | **MINOR 升级：加 `suggestions: list[str] = Field(default_factory=list, max_length=10)`** |
| `PolishedDraft` Pydantic | 不变（保留作为类型标签）|
| `FinalReport` Pydantic | 不变 |
| `Reference` Pydantic | 不变 |

**Critique 升级细节**：
- 默认 `default_factory=list` —— 旧 Critique 实例反序列化时 `suggestions=[]`
- `max_length=10` —— 防御 LLM 输出过长 list
- 不破坏 spec 001 foundation 兼容（旧 Critique 实例 score + comments 仍可用）

---

## C12. 错误类型契约（spec 004 抛出节点）

| 异常类 | 抛出节点 | 触发条件 |
|---|---|---|
| `NotYetImplementedError` | `_outline_node` / `_draft_node` / `_critic_review_node` / `_polish_node` | Langfuse prompt 缺失 |
| `NotYetImplementedError` | `_critic_review_node` | JSON 解析失败（重试 1 次后仍失败）|
| `NotYetImplementedError` | `_critic_review_node` | critique score 越界（重试 1 次后仍失败）|
| `RuntimeError` | (所有 write 节点) | `DASHSCOPE_API_KEY` 缺失 |
| **不抛错** | `_polish_node` | LLM 输出空字符串 / 大幅缩减 draft（polish 哲学）|
| **不抛错** | `_emit_final_report_node` | Source.index duplicate / `{{cite_N}}` 中 N > sources 长度（emit 哲学）|
| **透传** | 任何节点 | KeyError / AttributeError 等未知异常（per FR-017 + spec 003 FR-022 一致性）|

---

## 测试矩阵（spec 004 验收）

| TC | 主题 | 覆盖 |
|---|---|---|
| TC-1 | `get_llm("write"/"draft"/"critic"/"polish")` 返回 ChatOpenAI + temperature 正确 | 4 个单测 |
| TC-2 | `DASHSCOPE_API_KEY` 缺失时 `get_llm` 抛 RuntimeError | 4 个单测（每个 role 一次）|
| TC-3 | `_outline_node` 重试机制（章节数不一致时重试 1 次）| mock LLM 单元测试 |
| TC-4 | `_critic_review_node` JSON fence stripping + 重试 | mock LLM 单元测试 |
| TC-5 | `_polish_node` 空字符串 / 大幅缩减 不抛错 | mock LLM 单元测试 |
| TC-6 | `_emit_final_report_node` 占位符替换 + References 章节 + duplicate 兜底 | mock state 单元测试 |
| TC-7 | `build_write_subgraph().get_graph().draw_mermaid()` 渲染 4 节点拓扑 | graph viz 可视化 |
| TC-8 | 端到端 `run_research(...)` → `state["final_report"]` 合法 Markdown | quickstart.md SC-001 |

详见 [quickstart.md](./quickstart.md) 10 个 SC 验证脚本。

---

## 总结

spec 004 引入 11 个契约（C1-C11）+ 1 个错误契约（C12）+ 8 个测试矩阵（TC-1-TC8）。无新 HTTP / gRPC / CLI 接口；新增 Langfuse prompt 接口（C9 4 个）；新增 Python 函数签名（C2-C8 7 个节点函数 + C1 4 个 LLM role）。**Purely additive** 在 spec 001/002/003 之上，per Constitution Principle I。