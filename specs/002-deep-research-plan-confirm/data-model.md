# Data Model · Spec 002（Plan + Confirm 阶段）

> Phase 1 产出：spec 002 涉及的实体、字段、生命周期、约束。spec 001 已定义的实体（PlanStep, Plan, Source, SearchEvaluation, ...）不重定义，仅引用。

---

## 1. 实体清单

spec 002 直接涉及的实体（在 `src/apps/deep_research/schemas.py` 中已定义）：

| 实体 | spec 002 角色 | spec 001 引入 | spec 002 改动 |
|---|---|---|---|
| `UserQuery` | 输入（`state["user_query"]: str`） | ✅ | 不动 |
| `PlanStep` | `state["plan"]` 的元素 | ✅ | 不动（已是 Pydantic BaseModel） |
| `Plan` | plan_node LLM 输出中间容器 | ✅ | 不动（仅 LLM 解析时用，不入 state） |
| `HumanMessage` | edit 透传 user feedback | ❌（LangChain） | spec 002 新写入 `state["messages"]` |

`Plan` 在 spec 002 中**不直接写入 state**——plan_node 把 LLM 返回的 JSON 解析为 `list[PlanStep]` 后直接写 `state["plan"]`；`Plan` 仅作为 LLM 输出 schema 的中间校验对象。

---

## 2. PlanStep 字段（spec 001 + spec 002 共同约束）

继承 spec 001 schemas.py 的定义：

```python
class PlanStep(BaseModel):
    title: str                              # 必填，≤ 50 字符（spec 002 隐式约束）
    description: str | None = None           # 可选，≤ 200 字符
    depends_on: list[int] = []              # 默认空 list
```

### spec 002 约束（从 FR-002 / FR-006 衍生）

| 字段 | 约束 | 来源 |
|---|---|---|
| `title` | 必填；非空字符串；与 Plan 内其他 PlanStep 不重复 | FR-002 + Edge Case "全重复" |
| `description` | 可选；与 plan 阶段相关性不强，LLM 经常为 None | FR-002 |
| `depends_on` | 默认 `[]`；spec 002 不解析 DAG，由 spec 003 复用 | FR-002 |
| `Plan.steps` 总数 | 3 ≤ len ≤ 7 | FR-002（Q1 澄清） |
| `Plan.steps` 总数 = 0 | Pydantic validation 失败；plan_node 重试 1 次（per FR-006(a) 路径） | Edge Case "LLM 返回空 Plan" |
| 重复 `title` | plan_node 检测后走重试路径 | Edge Case "全重复" |

### Validation 行为

```python
# schemas.py 不改；plan_node 端做 list 长度校验：
try:
    Plan(steps=[PlanStep(**s) for s in llm_json])
except ValidationError as e:
    if "too_long" in str(e) or "exceed" in str(e):
        raise NotYetImplementedError("plan steps exceed max_steps=7")
    if "too_short" in str(e) or "min_length" in str(e):
        raise NotYetImplementedError("plan steps below min_steps=3")
    raise  # 其他 ValidationError 透传
```

---

## 3. State 字段（spec 002 改动的部分）

继承 spec 001 state.py 的 11 字段定义；spec 002 仅细化以下字段：

### 3.1 `state["plan"]: list[PlanStep]`

- **写入者**：`plan_node`（仅此一处）。
- **写入时机**：plan_node 第一次执行（initial invoke），或 edit 重生成后（per FR-008(b)）。
- **reducer**：无（plain `list`，overwrite 语义）——继承 spec 001 Q2 决策（避免 sub-graph 双重 reducer）。
- **生命周期**：
  - plan_node 第一次执行：写入 `list[PlanStep]`（3-7 条）。
  - confirm_plan_node approve：plan 保持不变，图继续推进。
  - confirm_plan_node edit：plan_node 第二次执行，覆盖写入新 plan。
- **下游消费**：research 子图入口（spec 003）读 `plan[i].title` 生成 ResearchQuery。

### 3.2 `state["messages"]: list[AnyMessage]`

- **写入者**：spec 002 中由 `confirm_plan_node`（edit 路径）+ 间接由 `plan_node`（通过 LangChain LLM invoke 自动追加 AIMessage）。
- **reducer**：`Annotated[list[AnyMessage], operator.add]`（spec 001 FR-003）。
- **spec 002 写入细节**：
  - `confirm_plan_node` 检测到 `str.startswith("edit:")` 后，写入 `HumanMessage(content=str)`。
  - `plan_node` 第二次执行时从最近一条 HumanMessage 提取 `user_feedback`（per FR-003）。
- **下游消费**：spec 004 write 阶段可能作为 chat history 复用（边界外，spec 002 不假设）。

### 3.3 `state["auto_confirm_plan"]: bool`

- **写入者**：`run_research` 入口（per FR-010 + foundation FR-008）。
- **reducer**：无（plain `bool`）。
- **消费**：`confirm_plan_node` 读取（per FR-009），True 时跳过 interrupt。
- **生命周期**：仅在 initial invoke 时由 run_research 初始化；不修改。

### 3.4 spec 002 不改动的字段

`user_query` / `research_queries` / `sources` / `search_evaluations` / `outline` / `draft` / `critique_history` / `final_report` / `iteration_counts` —— spec 002 不读不写，由 spec 003/004 接手。

---

## 4. 状态转换（State Transitions）

spec 002 涉及的 state 转换（plan + confirm 阶段）：

```
┌─────────────────────────────────────────────────────────────┐
│ initial invoke                                                │
│   run_research(input, thread_id=tid)                          │
│   ├─ run_research writes auto_confirm_plan=False              │
│   ├─ run_research acquires pg_advisory_xact_lock(tid)         │
│   └─ graph.invoke(initial_state)                              │
└────────────────────────┬─────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│ plan_node (first execution)                                   │
│   ├─ reads state["user_query"], state["messages"] (latter may │
│   │  be empty first time, or have prior edit HumanMessage)    │
│   ├─ calls prompts.get_prompt("deep-research-plan",           │
│   │   label="production")                                     │
│   ├─ extracts user_feedback from last HumanMessage (if any)   │
│   ├─ calls prompts.get_llm("plan").invoke(...) with retry     │
│   ├─ parses JSON → list[PlanStep]                             │
│   ├─ writes state["plan"]                                     │
│   └─ returns {"plan": [...]}                                  │
└────────────────────────┬─────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│ confirm_plan_node                                             │
│   ├─ reads state["plan"] (just written by plan_node)          │
│   ├─ if auto_confirm_plan=True: return {} (skip interrupt)    │
│   ├─ else: interrupt({"plan": [s.model_dump(mode="json")     │
│   │   for s in state["plan"]]})                               │
│   │   → LangGraph throws GraphInterrupt                       │
│   │   → graph.invoke returns to caller                        │
│   │   → PostgresSaver persists checkpoint                     │
│   │   → Postgres advisory_lock auto-released (txn commit)    │
│   └─ caller resumes with Command(resume=...)                  │
│      ├─ if "approve": return {} → graph continues to research │
│      ├─ if "edit: <text>": HumanMessage(content=text) →       │
│      │   Command(goto="plan", update={"messages": [...]})     │
│      └─ other values: treated as approve                      │
└────────────────────────┬─────────────────────────────────────┘
                         │ (after resume)
                         ▼
┌─────────────────────────────────────────────────────────────┐
│ research subgraph (stub in spec 002, real in spec 003)        │
│   ... continues per spec 003 ...                              │
└─────────────────────────────────────────────────────────────┘
```

**Edge case：edit 路径**

```
confirm_plan_node (edit) 
  → Command(goto="plan", update={"messages": [...]})
  → plan_node (second execution)
  ├─ reads state["messages"] (now has HumanMessage)
  ├─ extracts user_feedback = last HumanMessage content (minus "edit:" prefix)
  ├─ LLM invoke with user_feedback populated
  └─ writes state["plan"] (overwrites)
  → confirm_plan_node again
```

---

## 5. Postgres Schema 增量

spec 002 不引入新表——所有 Postgres 状态由 LangGraph `PostgresSaver` 管理（`checkpoints` / `checkpoint_blobs` / `checkpoint_writes` 三表，per ADR-0005）。

但 spec 002 引入了 **advisory lock**——这是 Postgres session-level 锁，**不占表**，仅在 `pg_locks` 系统视图里有记录，事务结束自动清理。

可观测的 Postgres 状态：

| 系统视图 | 用途 | spec 002 验证路径 |
|---|---|---|
| `pg_locks` | 查看 advisory lock | SC-008：脚本 A 持锁期间可见 `locktype='advisory'` |
| `checkpoints` 表 | LangGraph state 持久化 | SC-004：cross-process resume 验证 |
| `checkpoint_writes` 表 | pending writes | 不直接验证 |

---

## 6. Interrupt Payload Schema

`confirm_plan_node` 调 `interrupt(payload)` 时 payload 的 schema：

```python
interrupt_payload: dict = {
    "plan": list[dict]   # list of PlanStep.model_dump(mode="json") output
}
# 每个 element:
{
    "title": str,
    "description": str | None,
    "depends_on": list[int]
}
```

**为什么用 dict 而非 Pydantic 实例**：LangGraph 1.x 的 `interrupt()` payload 走 LangGraph 自己的序列化（绕开 `pydantic_serde.py`），必须 msgpack-serializable；`PlanStep.model_dump(mode="json")` 把 `HttpUrl` 等转 str。

**payload 不被任何下游节点消费**——下游消费的是 `state["plan"]`（已经是 Pydantic 实例）；payload 仅作为 UI（spec 005）渲染 plan + 决策按钮的 source。

---

## 7. Resume Payload Schema

`Command(resume=payload)` 的 payload：

```python
resume_payload: str = <one of>:
  - "approve"                            # 通过
  - "edit: <user feedback text>"         # 修改 + 重生成（spec 002 Q2 决策）
  - <other string>                       # 视为 approve（FR-008(c) 宽松策略）
```

**type check**：`isinstance(user_decision, str)`——LangGraph 1.x 透传 payload 类型；若传 `dict` / `Command` 等复杂类型，spec 002 视为 approve（不报错）。

**size check**：`< 10 KB`（隐式约束，超大 payload 拒绝——FR-008 没有显式 size limit，但 plan edit 文本通常 < 1 KB）。

---

## 8. 错误 Schema

spec 002 引入 3 类结构化错误（per FR-007 + FR-012 + FR-017）：

```python
# 1. Command(resume) 但 thread 无 pending interrupt
{"error": "no_pending_interrupt", "thread_id": str}

# 2. 同 thread_id 被另一个进程持有
{"error": "thread_id_busy", "thread_id": str}

# 3. plan_node LLM 调用失败（FR-006 各类）
NotYetImplementedError(
    "plan steps exceed max_steps=7"
    | "plan generation failed: <reason>"
    | "dashscope rate limited after 2 retries"
)
```

返回路径：

| 错误 | 抛出位置 | 捕获位置 | 渲染 |
|---|---|---|---|
| `no_pending_interrupt` | `run_research` 返回 dict | UI（spec 005） | "No pending interrupt for this thread" |
| `thread_id_busy` | `run_research` 返回 dict | UI（spec 005） | "Another session is running; retry in a moment" |
| `NotYetImplementedError` | `plan_node` 抛出 | `run_research` 不捕获，向上抛 | UI 显示错误消息 + 触发 "retry" 按钮 |

---

## 9. 与 schemas.py / state.py 的兼容性

spec 002 不修改 `schemas.py` / `state.py`——所有实体定义在 spec 001 已就位。

spec 002 仅在 **行为层** 改变：
- `plan_node`：stub → 真实 LLM 调用
- `confirm_plan_node`：保留 interrupt 行为，新增 edit 分支
- `run_research`：新增 advisory lock + structured error 返回

**这意味着 spec 002 可以合并到 master 而不需要任何 spec 001 文件变更**——purely additive on top of foundation。

---

## 10. 边界外（spec 002 不涉及）

| 项 | 由哪个 spec 处理 |
|---|---|
| `Source` / `SearchEvaluation` 字段细节 | spec 003 |
| `Outline` / `Draft` / `Critique` / `FinalReport` / `Reference` 字段细节 | spec 004 |
| `iteration_counts["research"]` / `["write"]` 写入 | spec 003 / spec 004 |
| Streamlit session_state 字段 | spec 005 |
| Langfuse `@observe` 装饰器 | spec 006 |
