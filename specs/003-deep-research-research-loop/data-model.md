# Data Model · Spec 003（Research 阶段）

> Phase 1 产出：spec 003 涉及的实体、字段、生命周期、约束。spec 001 已定义的实体（Source / ResearchQuery / SearchEvaluation）不重定义，仅引用与细化。spec 002 写过的字段（plan / messages / auto_confirm_plan）继承。

---

## 1. 实体清单

spec 003 直接涉及的实体（在 `src/apps/deep_research/schemas.py` 中已定义）：

| 实体 | spec 003 角色 | spec 001 引入 | spec 003 改动 |
|---|---|---|---|
| `ResearchQuery` | `_gen_query_node` LLM 输出，写入 `state["research_queries"]` | ✅ | 不改 schema |
| `Source` | `_websearch_node` 与 `_eval_search_node` 写入 `state["sources"]` | ✅ | 不改 schema（`score` 与 `index` 字段的写入时机由 spec 003 定义） |
| `SearchEvaluation` | `_eval_search_node` 写入 `state["search_evaluations"]` | ✅ | 不改 schema |
| `Source` 的 `url` 字段 | 跨轮去重的 key（per [Edge Cases · 同一 Source URL 跨多轮重复]） | ✅ | 不改 schema |

`Source` / `ResearchQuery` / `SearchEvaluation` 在 spec 003 **不改 schema**——所有字段定义在 spec 001 已 frozen。spec 003 仅改变这些实体的**写入者**与**写入时机**。

---

## 2. Source 字段（spec 001 + spec 003 细化）

继承 spec 001 schemas.py 的定义：

```python
class Source(BaseModel):
    index: int                                # 1..N 全局唯一（spec 003 eval_search_node 重编号）
    title: str                                # 必填
    url: HttpUrl                              # 必填；跨轮去重的唯一 key
    content: str                              # spec 003 截断到 1500 字符（per R7）
    score: float | None                       # ReRank 后由 eval_search_node 写值；USE_RERANK=false 时保持 None
    query_index: int                          # Send payload 传入；eval_search_node 用于按 query 分组 ReRank
```

### spec 003 约束（从 FR-002 / FR-013 / R4 衍生）

| 字段 | 约束 | 来源 |
|---|---|---|
| `index`（websearch_node 写入时） | 临时为 `0`（占位） | R4 决策 |
| `index`（eval_search_node 重编号后） | `1..N` 全局唯一，按 score 降序排序后赋值 | FR-013 + [Q6] |
| `title` | 必填；非空字符串 | FR-002 |
| `url` | 必填 HttpUrl；**跨轮去重的 key**（相同 url 保留 score 最高版本） | [Edge Cases · 同一 Source URL 跨多轮重复] |
| `content` | spec 003 截断到 1500 字符（per R7） | R7 |
| `score` | ReRank 后由 eval_search_node 写；USE_RERANK=false 时保持 None | FR-006 + FR-017 |
| `score < 0.3` | 该 Source 被 eval_search_node 丢弃，不入 `state["sources"]` | FR-013 + ADR-0004 |
| `query_index` | Send payload 传入；eval_search_node 按 `query_index` 分组后 ReRank | FR-013 |

### websearch_node 返回的 Source（中间状态）

`_websearch_node(payload)` 返回的每个 Source 的 `index=0`——这是 **故意**的占位值，避免 5 个 Send worker 都用 `index=1..N`（每 worker 独立编号）产生冲突；真正的全局唯一编号在 fan-in 后由 `_eval_search_node` 重新赋值。

### eval_search_node 重编号算法

```python
def _renumber_sources(sources: list[Source]) -> list[Source]:
    """Sort by score desc, then renumber index = 1..N globally unique."""
    # sort by (-score, query_index, original_order) for stability
    sorted_sources = sorted(
        enumerate(sources),
        key=lambda x: (-(x[1].score or 0.0), x[1].query_index, x[0]),
    )
    return [
        s.model_copy(update={"index": i + 1})
        for i, (_, s) in enumerate(sorted_sources)
    ]
```

**为什么按 score 降序排**：FinalReport References 章节（spec 004 生成）按 `Source.index` 编号；最相关的 Source 优先呈现，可读性更好。

---

## 3. SearchEvaluation 字段（spec 001 + spec 003 细化）

```python
class SearchEvaluation(BaseModel):
    source_index: int          # Position in state["sources"] this evaluation rates
    relevance: float           # 0.0-1.0 (ReRank score OR LLM Judge score)
    rationale: str             # ReRank: "ReRank score: 0.92"; LLM Judge: qualitative reason
    evaluator: Literal["rerank", "llm_judge"]  # 标记评分策略
```

### spec 003 约束

| 字段 | 约束 | 来源 |
|---|---|---|
| `source_index` | 对应 `state["sources"][i].index`（重编号后的 1..N）；eval_search_node 写 | FR-013 |
| `relevance`（ReRank 路径） | 直接等于 `Source.score`（0.0-1.0） | FR-013 |
| `relevance`（LLM Judge 路径） | LLM Judge JSON 中的 `relevance` 字段；范围 0.0-1.0 校验 | FR-017 |
| `rationale`（ReRank 路径） | `f"ReRank score: {score:.2f}"`（不是 LLM Judge 的 qualitative reason） | FR-013 |
| `rationale`（LLM Judge 路径） | LLM Judge JSON 中的 `rationale` 字符串 | FR-017 |
| `evaluator` | `"rerank"`（USE_RERANK=true 路径）；`"llm_judge"`（USE_RERANK=false 或 ReRank fallback） | FR-013 / FR-017 |

### 为什么 SearchEvaluation.rationale 在 ReRank 路径不是空字符串

`Source.score` 已经存了 numeric score，但 `SearchEvaluation.rationale` 字段是 **统一 schema**（不破坏 spec 001 字段）。ReRank 路径填 `"ReRank score: 0.92"` 占位——便于 Langfuse UI 在 spec 006 接入后看 SearchEvaluation span 时不显示空白。

---

## 4. ResearchQuery 字段（spec 001 + spec 003 细化）

```python
class ResearchQuery(BaseModel):
    text: str                 # 搜索查询字符串
    target_step: int          # PlanStep 索引（0..len(plan)-1）
    iteration: int            # Research 轮次（1..3）
```

### spec 003 约束

| 字段 | 约束 | 来源 |
|---|---|---|
| `text` | LLM 自然语言输出；3-8 词；非空 | FR-009 + R9 prompt |
| `target_step` | 取值范围 `[0, len(plan))`；spec 003 LLM 仅取**单 step**（当前 iteration 对应的 PlanStep），不跨 step | R9 prompt 模板 |
| `iteration` | 在 `_gen_query_node` 入口 `state["iteration_counts"]["research"]` +1 后传给 LLM，让 LLM 知道"这是第几轮" | FR-009 + R6 |
| 总数 | `1 <= len(produced) <= 5`（per [Q4] 决策：变长 fan-out） | [Q4] |

### 写入时机

`_gen_query_node` 入口：
1. `state["iteration_counts"]["research"] += 1`（per R6）
2. 调 LLM 产 `list[ResearchQuery]`（每个含 `iteration = state["iteration_counts"]["research"]`）
3. 返回 `list[Send("websearch", {"query": q.text, "target_step": q.target_step, "iteration": q.iteration, "query_index": i})]`

`_websearch_node`（Send worker）：
- 写 `state["research_queries"]` 时把每条 query 的 `iteration` 字段设为 payload 的 `iteration`

---

## 5. State 字段（spec 003 改动的部分）

继承 spec 001 state.py 的 11 字段定义；spec 003 细化以下字段：

### 5.1 `state["sources"]: list[Source]`

- **写入者**：`_websearch_node`（partial，新增 Source）+ `_eval_search_node`（final，重编号 + 过滤）。
- **reducer**：无（plain `list`，overwrite 语义）——继承 spec 001 Q2 决策。
- **生命周期**：
  - `_websearch_node` 第 i 个返回 `{"sources": [Source_i_1, Source_i_2, ...]}`；LangGraph 1.2+ 的 Send fan-out 把所有 worker 的 partial sources **拼接**（per R4 + LangGraph 1.2.11+ `Send` docs）。
  - `_eval_search_node` 读 `state["sources"]`（已经包含所有 worker 的 Source），做：URL 去重 → 按 query_index 分组 ReRank → score < 0.3 过滤 → 重编号 → 返回 `{"sources": renumbered}`。
- **下游消费**：spec 004 write 子图入口读 `state["sources"]` 生成 Outline。

### 5.2 `state["search_evaluations"]: list[SearchEvaluation]`

- **写入者**：`_eval_search_node`（仅此一处）。
- **reducer**：无（plain `list`，overwrite 语义）。
- **生命周期**：每个保留的 Source 一条 SearchEvaluation；eval_search_node 写一次 `{"search_evaluations": [...]}`。
- **下游消费**：spec 004 不直接读 SearchEvaluation（spec 006 Langfuse UI 用作 search quality metric）。

### 5.3 `state["research_queries"]: list[ResearchQuery]`

- **写入者**：`_gen_query_node`（间接通过 `_websearch_node`，每轮每 query 一条）。
- **reducer**：无（plain `list`，overwrite 语义）。
- **生命周期**：每轮 gen_query 产 N 条 ResearchQuery；websearch_node 把每条 query 写入 state 的 `research_queries` 字段（追加，但因 Send 拼接特性，final state 含所有轮次的 queries）。
- **下游消费**：spec 004 不读 research_queries；spec 006 Langfuse UI 用作 research activity log。

### 5.4 `state["iteration_counts"]: dict[str, int]`

- **写入者**：`_gen_query_node` 入口 `state["iteration_counts"]["research"] += 1`（per R6 + FR-014）。
- **reducer**：无（plain `dict`，overwrite 语义）——继承 foundation FR-004 + spec 002 NFR-002。
- **生命周期**：research 子图入口 +1；research 子图退出时 counter 等于实际跑的轮数（1-3）。
- **下游消费**：spec 004 write 子图也读/写 `iteration_counts["write"]`；spec 003 不改 `["write"]`。

### 5.5 spec 003 不改动的字段

`user_query` / `plan` / `messages` / `auto_confirm_plan`（spec 002 已写）/ `outline` / `draft` / `critique_history` / `final_report`（spec 004 才写）—— spec 003 不读不写。

---

## 6. 状态转换（State Transitions）

spec 003 涉及的 state 转换（research 子图内部）：

```
┌─────────────────────────────────────────────────────────────────┐
│ research 子图入口 (由 confirm_plan_node approve 后触发)            │
│   state 继承自 spec 002: plan 已写, sources=[]                    │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│ _gen_query_node (iteration 1)                                     │
│   ├─ reads state["plan"], state["user_query"]                    │
│   ├─ state["iteration_counts"]["research"] += 1   (=1)            │
│   ├─ calls prompts.get_prompt("deep-research-gen-query",           │
│   │   label="production")                                          │
│   ├─ chevron 渲染: {user_query, plan_section_title,                │
│   │   plan_section_description, iteration=1, target_step=i}        │
│   ├─ calls prompts.get_llm("research").invoke(...)                │
│   ├─ parses JSON → list[ResearchQuery]                            │
│   ├─ return list[Send("websearch", payload_i) for q in queries]   │
│   └─ LangGraph fan-out 5 个 websearch worker (并发)               │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│ _websearch_node (× N Send workers, 并发)                          │
│   ├─ reads payload["query"], payload["query_index"],              │
│   │   payload["target_step"], payload["iteration"]                │
│   ├─ calls DashScopeWebSearch().run(query) → list[Source]         │
│   │   (每条 Source.index=0 占位; query_index=payload["query_index"])│
│   ├─ if NotYetImplementedError (known error): print + return       │
│   │   {"sources": []} (per FR-013 + R8)                         │
│   │   if other Exception: raise (Send 失败 → 整图崩)             │
│   ├─ returns {"sources": [Source, ...], "research_queries":       │
│   │   [ResearchQuery(text=q, target_step=ts, iteration=iter)]}    │
│   └─ LangGraph fan-in: 所有 worker 的 sources 字段拼接            │
│      (per R4; LangGraph 1.2.11+ Send + plain list 拼接语义)        │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│ _eval_search_node (fan-in)                                        │
│   ├─ reads state["sources"] (merged from all workers)             │
│   ├─ 按 url 去重 (保留首条 score 最高的; per [Edge Case])          │
│   ├─ 按 query_index 分组: {q: [s for s in sources if              │
│   │   s.query_index == q]}                                        │
│   ├─ per group:                                                   │
│   │   ├─ if USE_RERANK=true (默认):                              │
│   │   │   ├─ try DashScopeReRank().run(query, docs)               │
│   │   │   │   成功 → 把 relevance_score 写回 Source.score         │
│   │   │   │   失败 → fallback LLM Judge (per R5 + FR-017)         │
│   │   │   └─ 过滤 score < 0.3 的 Source                           │
│   │   └─ if USE_RERANK=false:                                    │
│   │       ├─ prompts.get_llm("research").invoke(...)              │
│   │       ├─ parses JSON → [{source_index, relevance, rationale}] │
│   │       └─ 写 state["search_evaluations"] (evaluator="llm_judge")│
│   │           (Source.score 保持 None)                              │
│   ├─ 按 score 降序 + query_index + 原始顺序 排序稳定                │
│   ├─ 重编号 index = 1..N                                          │
│   ├─ writes {"sources": renumbered, "search_evaluations": evals}  │
│   └─ if len(sources) == 0 AND iters == 3: raise                  │
│       NotYetImplementedError("research: 3 iterations yielded 0   │
│       sources after ReRank filter") (per FR-015)                    │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│ _should_continue_research (条件边)                                 │
│     ├─ if len(state["sources"]) >= 8 OR                            │
│     │   state["iteration_counts"]["research"] >= 3:              │
│     │   return "__end__" → 子图退出 → 外层图继续到 write 子图     │
│     └─ else: return "gen_query" → 回到 _gen_query_node 下一轮    │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
                  (循环 1-3 轮; 写子图入口由 spec 004 实现)
```

---

## 7. Langfuse Prompt 占位符 Schema

### 7.1 `deep-research-gen-query`

| 占位符 | 类型 | 来源 | 必填 |
|---|---|---|---|
| `{{user_query}}` | str | `state["user_query"]` | ✅ |
| `{{plan_section_title}}` | str | 当前 iteration 对应的 `plan[i].title` | ✅ |
| `{{plan_section_description}}` | str | `plan[i].description`（可为 None → 空字符串） | ❌ |
| `{{iteration}}` | int | `state["iteration_counts"]["research"]`（+1 后） | ✅ |
| `{{target_step}}` | int | 当前 iteration 对应的 `plan` index | ✅ |

完整模板见 `research.md §R9` 与 `contracts/README.md §C8`。

### 7.2 `deep-research-judge`

| 占位符 | 类型 | 来源 | 必填 |
|---|---|---|---|
| `{{user_query}}` | str | `state["user_query"]` | ✅ |
| `{{sources_json}}` | str | `[s.model_dump(mode="json") for s in sources]` 序列化为 JSON 字符串 | ✅ |

---

## 8. Send Payload Schema

`_gen_query_node` 返回的每个 `Send` 的 payload：

```python
Send(
    "websearch",
    {
        "query": str,            # 搜索查询字符串（from ResearchQuery.text）
        "target_step": int,      # 0..len(plan)-1（from ResearchQuery.target_step）
        "iteration": int,        # 1..3（from ResearchQuery.iteration）
        "query_index": int,      # 0..len(queries)-1（本 worker 在本轮的索引）
    },
)
```

**payload 必须是 msgpack-serializable**——这是 LangGraph 1.x 的要求；`int` / `str` 都是原生类型，无 Pydantic 实例。

---

## 9. Postgres Schema 增量

spec 003 不引入新表——所有 Postgres 状态由 LangGraph `PostgresSaver` 管理（继承 spec 001 / 002）。

可观测的 Postgres 状态：

| 系统视图 | 用途 | spec 003 验证路径 |
|---|---|---|
| `checkpoints` 表 | LangGraph state 持久化 + Send fan-in/out checkpoint | SC-002（websearch 时间戳差）/ SC-004（sources 数量）/ SC-006（research 子图退出） |
| `checkpoint_writes` 表 | pending writes | 不直接验证 |

---

## 10. 错误 Schema

spec 003 引入的 `NotYetImplementedError` 子消息（per FR-022 + Edge Cases）：

```python
NotYetImplementedError(
    # websearch 节点
    "dashscope web search rate limited after 2 retries: query=<q>"
    "dashscope web search returned 0 results for query=<q>"
    "DASHSCOPE_WEB_SEARCH_APP_ID not set"            # 实际是 RuntimeError, 但 spec 003 文档统一此处
    "DASHSCOPE_WEB_SEARCH_APP_TOKEN not set"

    # ReRank 节点
    "dashscope rerank failed: <code>"

    # eval_search 节点
    "llm judge failed after retry: <reason>"
    "gen_query produced 0 queries after retry"

    # research 子图退出
    "research: 3 iterations yielded 0 sources after ReRank filter"
)
```

返回路径：

| 错误 | 抛出位置 | 捕获位置 | UI 渲染（spec 005） |
|---|---|---|---|
| `dashscope web search rate limited after 2 retries` | `_websearch_node` 包成 + return `{"sources": []}` | gen_query 下一轮决定 | 不打印 progress |
| `dashscope rerank failed: <code>` | `DashScopeReRank._run` | `_eval_search_node` try/except | fallback LLM Judge 静默生效 |
| `llm judge failed after retry` | `_eval_search_node` | run_research 不 catch，向上抛 | UI 显示"评分失败"提示 |
| `research: 3 iterations yielded 0 sources` | `_eval_search_node` | run_research 不 catch，向上抛 | UI 显示"研究失败"提示 |
| 其他（未知错误） | 任一节点透传 | run_research 不 catch，向上抛 | UI 显示"内部错误"提示 |

---

## 11. 与 schemas.py / state.py 的兼容性

spec 003 不修改 `schemas.py` / `state.py`——所有实体定义在 spec 001 已就位。

spec 003 仅在 **行为层** 改变：
- `tools.py::DashScopeWebSearch._run`：占位 raise → 真实 Bailian App 调用
- `tools.py::DashScopeReRank._run`：占位 raise → 真实 DashScope ReRank 调用
- `nodes/research.py`：stub → 真实 gen_query / Send websearch / eval_search / should_continue_research
- `prompts.py::get_llm("research")`：stub raise → 真实 ChatOpenAI 实例

**这意味着 spec 003 可以合并到 master 而不需要任何 spec 001/002 文件变更**——purely additive on top of foundation + plan-confirm。

---

## 12. 边界外（spec 003 不涉及）

| 项 | 由哪个 spec 处理 |
|---|---|
| Outline / Draft / Critique / FinalReport / Reference 写入 | spec 004 |
| Write 循环 + `critic_score >= 0.8` 终止条件 | spec 004 |
| Streamlit session_state + interrupt 接线 | spec 005 |
| Langfuse `@observe` 装饰器 + TraceMetadata | spec 006 |
| Plan step DAG 校验 / depends_on 解析（spec 002 NFR + FR-002 提到但未实现） | 未来 spec（暂未规划） |
| Source summary / chunk 切分（写阶段长 context 处理） | spec 004 处理（如果需要） |
| 跨 research 子图 + write 子图的 iteration_counts 总分 | spec 004 write 子图 |

---

## 13. 与 spec 002 的接口边界

spec 003 与 spec 002 在 state 字段的接口：

| 字段 | spec 002 写入者 | spec 003 读取者 | spec 003 写入者 |
|---|---|---|---|
| `plan` | plan_node | gen_query_node（plan_section_title / plan_section_description） | 不写 |
| `user_query` | run_research 入口 | gen_query_node（传入 LLM prompt） | 不写 |
| `messages` | confirm_plan_node（HumanMessage） | 不读 | 不写 |
| `auto_confirm_plan` | run_research 入口 | 不读 | 不写 |
| `sources` | stub（spec 002 继承） | eval_search_node（读 + 重编号） | websearch_node（partial） + eval_search_node（final） |
| `search_evaluations` | stub | 不读 | eval_search_node |
| `research_queries` | stub | 不读 | websearch_node（通过 Send fan-in 拼接） |
| `iteration_counts` | run_research 入口初始化 | gen_query_node（读 + 写） | gen_query_node |
| 其他（outline / draft / critique_history / final_report） | stub | 不读 | 不写 |

**互不重叠**——spec 002 写 `plan` / `messages` / `auto_confirm_plan`；spec 003 写 `sources` / `search_evaluations` / `research_queries` / `iteration_counts["research"]`。无 reducer 冲突风险（plain list / plain dict 字段）。