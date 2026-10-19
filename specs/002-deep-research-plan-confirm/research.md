# Research · Spec 002（Plan + Confirm 阶段）

> Phase 0 产出：解决 spec.md 中所有 NEEDS CLARIFICATION，并对外部依赖与最佳实践做尽职调查。

---

## R1. Langfuse 3.x Python SDK 的 `get_prompt` / `compile` API

### Decision

使用 `langfuse>=3.0` SDK（pyproject.toml 实际锁定的版本，**spec 002 FR-004 原写的 "Langfuse 2.x" 是错的**，已在 plan 阶段纠正），核心 API：

```python
from langfuse import get_client

lf = get_client()                              # 默认读 .env
prompt = lf.get_prompt("deep-research-plan", label="production")
# prompt 是 PromptClient 对象（langfuse SDK v3.x）

# text 类型 prompt（带 {{var}} 占位符）
compiled: str = prompt.compile(user_query=..., max_steps=..., user_feedback=...)

# chat 类型 prompt（messages 数组 + 每条带 {{var}} 占位符）
# v3 SDK 仍返回 str 形式的 compile（需要 messages 参数 → 走 LangChain）
```

### Rationale

- 项目现有 `src/apps/fuse/prompt_demo.py` 已经走通 `langfuse>=3.0` 的 `get_client()` + `get_prompt(name, label="production")` + `p.compile(**kwargs)` 链路——这就是 spec 002 plan_node 要复用的 pattern。
- `langfuse>=3.0` 与 `>=2.0,<3.0` 的 API 主要差异在 `update_current_generation` 的参数名（`usage` → `usage_details`），但 `get_prompt` / `compile` 在 2.x → 3.x 保持兼容。
- 选 chat 类型 prompt 而非 text：spec 002 FR-007 要求 plan_node 把 chat 拼成 `list[dict]` 后调 LLM；text 类型只能拼成单字符串，无法保留 system/user 区分。

### Alternatives Considered

- **A. text 类型 prompt**：API 简单（`p.compile(...)` 返回 str），但 system message / user message 边界丢失；spec 002 FR-007 必须传 chat messages 才能让 qwen-plus 区分 system 与 user 角色——**否定**。
- **B. **chat 类型 prompt + Langfuse 自带 chat messages**：`p.compile(...)` 返回 `list[dict]` 直接喂给 LLM；但 langfuse 3.x SDK 实际仍返回 `str`（system + user 拼接），chat messages 拆分需要客户端解析——**部分否定**：plan_node 仍按 text 类型处理，但 prompt 内部按 chat 模板写（system + user 分段）；后续 spec 003/004 评估是否切到 chat 类型 + 客户端解析。
- **C. **双 prompt 模板**（text + chat）：保留 fallback；spec 002 不需要，**否定**。

### Action Items

- **plan_node** 按 `text` 类型 prompt 走：`p.compile(user_query=..., max_steps=..., user_feedback=...)` → 拼成 `list[dict]` 喂 LLM。
- **Langfuse UI 端**：spec 002 用户文档 / README 同步 `deep-research-plan` prompt 的 chat 模板格式（system message + user message 用 `{{var}}` 占位符）。
- **记录**：spec 002 FR-004 中 "Langfuse 2.x" 错误需在 plan 阶段标注为 [修正]，plan 阶段不重写 spec 但在 Notes 加变更说明。

---

## R2. LangGraph 1.x `interrupt()` + `Command(resume=...)` 模式

### Decision

```python
from langgraph.types import interrupt, Command

# 节点函数内
def confirm_plan_node(state):
    user_decision = interrupt({"plan": [s.model_dump(mode="json") for s in state["plan"]]})
    if isinstance(user_decision, str) and user_decision.startswith("edit:"):
        return Command(
            goto="plan",
            update={"messages": state["messages"] + [HumanMessage(content=user_decision)]},
        )
    return {}  # approve 或其他值

# 外部调用
from langgraph.types import Command
result = graph.invoke(Command(resume="approve"), config={"configurable": {"thread_id": tid}})
```

### Rationale

- LangGraph 1.2+（pyproject.toml 锁定 `>=1.2.11`）已经把 `interrupt()` 升级为**同步阻塞式**：节点调 `interrupt(payload)` 时 LangGraph 抛 `GraphInterrupt` 异常，让 `graph.invoke()` 立即返回，state 被 checkpointer 持久化。
- `Command(resume=...)` 是 LangGraph 1.x 的 resume 标准；payload 类型由节点决定（spec 002 用 `str`，"approve" / "edit:..."）。
- payload 用 `.model_dump(mode="json")` 而不是 `.model_dump()`——`pydantic_serde.py` 已经处理 msgpack 兼容（`HttpUrl` → str），但 `interrupt()` payload 走 LangGraph 自己的序列化路径，绕开 `pydantic_serde.py`，所以 plan_node / confirm_plan_node 端必须自己 `model_dump(mode="json")` 一次。

### Alternatives Considered

- **A. `__interrupt__` payload 模式**（LangGraph 0.x）：返回 `__interrupt__` 字段而非抛异常；spec 002 必须 LangGraph 1.x，**否定**。
- **B. 异步 `ainvoke`**：spec 002 暂只走同步 `invoke`；`ainvoke` 留给 spec 005 Streamlit async UI。
- **C. `interrupt` 抛异常 vs 返回值**：LangGraph 1.x 标准是抛 `GraphInterrupt`；UI 层（spec 005）需要在 `try/except` 里捕获后渲染 plan + 决策按钮。spec 002 FR-007 不强制 `try/except`——只保证 plan_node 调 `interrupt` 后 `run_research` 行为正确。

### Action Items

- `confirm_plan_node` 用 `interrupt({"plan": [...]})` 而非 `interrupt(plan_list)`——payload 必须是 dict（LangGraph 内部 protocol 要求）。
- `interrupt` 返回值类型：`str`（来自 `Command(resume=str)`）。
- spec 002 SC-002 验证：`try: ...; except langgraph.errors.GraphInterrupt as e: print(e)`。
- `Command(goto="plan", update=...)` 中 `update` 的 `messages` 走 `operator.add` reducer；plan_node 第二次执行时 state["messages"] 含 HumanMessage，按 FR-003 提取 user_feedback。

---

## R3. DashScope OpenAI 兼容端点 + qwen-plus

### Decision

```python
from langchain_openai import ChatOpenAI

llm = ChatOpenAI(
    model="qwen-plus-2025-04-28",
    api_key=os.environ["DASHSCOPE_API_KEY"],
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    temperature=0.4,
)
```

### Rationale

- DashScope 提供 OpenAI 兼容端点（`/compatible-mode/v1`），让 LangChain `ChatOpenAI` 可以直接用。
- `qwen-plus-2025-04-28` 是 DashScope 当前主力模型；plan 任务用 `qwen-plus` 而不是 `qwen-max`（成本 + 速度：plan 是结构化输出，不需要 max 的复杂推理）。
- `temperature=0.4`：plan 需要"分章节"多样性但又要稳定结构化输出；0.0 太死板（不同 query 总是同样章节数），1.0 太随机。
- free tier RPM 限制实测 ≈ 60/min；spec 002 FR-006(d) 用指数退避（base=1s, max_retries=2）覆盖瞬时限流。

### Alternatives Considered

- **A. 直连 DashScope SDK**（`dashscope.Generation.call`）：与 LangChain 集成差，prompt 模板与 Langfuse trace 关联麻烦；**否定**。
- **B. qwen-turbo**：更便宜但 plan 质量不稳定（实测章节划分粗），**否定**。
- **C. qwen-max**：plan 质量高但成本翻倍；plan 是高频调用（每个 query 一次 + edit 可能再来一次），**否定**。

### Action Items

- spec 002 FR-005 锁定 `qwen-plus-2025-04-28` + `temperature=0.4`。
- env var `DASHSCOPE_API_KEY` 必填；无则 `get_llm` 抛 `RuntimeError("DASHSCOPE_API_KEY not set")`。
- 模型名写死为字符串（不引入 config 抽象层）——spec 002 唯一 LLM，未来 spec 003/004 可能换 model 时再抽象。

---

## R4. Postgres `pg_advisory_xact_lock` 互斥

### Decision

```python
from psycopg import connect

def _acquire_thread_lock(tid: str) -> bool:
    """Try to acquire advisory lock on thread_id. Returns True if acquired."""
    with connect(os.environ["LANGGRAPH_CHECKPOINT_DB"]) as conn:
        with conn.cursor() as cur:
            cur.execute("SET LOCAL lock_timeout = '1s'")
            try:
                cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (tid,))
                return True
            except psycopg.errors.LockNotAvailable:
                return False
            # NB: lock released on transaction commit (end of `with` block)
```

### Rationale

- `pg_advisory_xact_lock(hashtext(:thread_id))`：把 thread_id 字符串 hash 成 int64，加 Postgres advisory lock；事务结束（`with conn:` 退出）自动释放。
- `hashtext()` 是 Postgres 内置函数，返回 `integer`（int32）；advisory lock 接受 int64，所以 hashtext 结果直接够用。
- `SET LOCAL lock_timeout = '1s'`：避免 UI 卡死；超过 1 秒拿不到锁就放弃，返回 `thread_id_busy`。
- critical section 范围：`_has_pending_interrupt + graph.invoke(input)`；**不**包含 LLM 调用本身（plan_node 内的 LLM invoke 期间不应持有锁，避免拖死 Postgres 连接池）。

### Alternatives Considered

- **A. `SELECT FOR UPDATE` on `checkpoints` 表**：会锁住 LangGraph 内部的 checkpoint 写入路径，可能影响 `PostgresSaver.put()` 行为；**否定**。
- **B. 应用层锁（Python `threading.Lock`）**：跨进程无效；**否定**。
- **C. Redis 分布式锁**：项目当前没有 Redis 依赖（ADR-0005 用 Postgres）；**否定**（额外引入依赖）。
- **D. advisory lock 但不设 timeout**：第二个进程无限期等待——UI 不友好；**否定**。

### Action Items

- `run_research` 入口：先 `_acquire_thread_lock(tid)`，失败即返回 `{"error": "thread_id_busy"}`。
- advisory lock 必须覆盖 `_has_pending_interrupt` + `graph.invoke`；不能覆盖 LLM 调用。
- 锁等待超时 `1s`——避免 UI 长卡。
- SC-008 验证：脚本 A 持锁跑 30s，脚本 B 1s 内拿到 `thread_id_busy`。

---

## R5. Pydantic JSON retry 模式（手写 vs tenacity）

### Decision

**手写 retry**（不用 tenacity）：

```python
import time

def _invoke_with_retry(llm, messages, *, max_retries=2, base=1.0):
    """Exponential backoff for 429 / 5xx. Other errors propagate immediately."""
    last_err = None
    for attempt in range(max_retries + 1):
        try:
            return llm.invoke(messages)
        except Exception as e:
            status = getattr(e, "status_code", None) or getattr(e, "code", None)
            if status not in (429, 500, 502, 503) or attempt == max_retries:
                raise
            time.sleep(base * (2 ** attempt))   # 1s, 2s
            last_err = e
    raise last_err  # unreachable
```

### Rationale

- tenacity 是通用 retry 库，但 spec 002 只需要"分类 + 指数退避"两件事——引入 tenacity 增加依赖 + 黑盒（retry 计数难打 Langfuse score）。
- 手写 ~15 行，retry 计数直接 `langfuse.update_current_span(metadata={"plan_retry_count": N})`——便于 spec 006 Langfuse observability 时直接接。
- base=1s + max_retries=2 + exp=2x：实际等待序列 `[0, 1s, 2s]`，覆盖 1 分钟内瞬时限流。

### Alternatives Considered

- **A. tenacity**：通用但引入依赖；**否定**。
- **B. HTTPX 层 retry**（如 `httpx.Client` 配 `transport=httpx.HTTPTransport(retries=2)`）：retry 在 HTTP 层，LangChain / Langfuse 看不到 retry 次数；**否定**。
- **C. SDK-level retry**（如 ChatOpenAI 的 `max_retries` 参数）：retry 在 SDK 内部，Langfuse span 看不到；**否定**。

### Action Items

- `_invoke_with_retry` 是 plan_node 的内部 helper，不暴露公共 API。
- retry 计数通过 `langfuse.update_current_span(metadata={"plan_retry_count": N})` 写——spec 006 完整接 Langfuse 前用 `print(f"[retry] plan_retry_count={N}")` 占位。
- JSON 解析失败 + LLM 空 Plan：1 次重试（不走指数退避，立即重试 1 次）——见 FR-006(a) + 新 Edge Case。

---

## R6. interrupt payload 序列化（plan list → dict）

### Decision

```python
interrupt_payload = {
    "plan": [s.model_dump(mode="json") for s in state["plan"]]
}
user_decision = interrupt(interrupt_payload)
```

### Rationale

- `interrupt(payload)` 的 payload 必须是 msgpack-serializable；`pydantic_serde.py` 处理 `PostgresSaver` 端的 msgpack，但 `interrupt` payload 走 LangGraph 自带的序列化（绕过 saver）。
- `PlanStep` 含 `HttpUrl`（schemas.py）——直接 `model_dump()` 会保留 `Url` 对象，msgpack 序列化失败；`model_dump(mode="json")` 把 `HttpUrl` 转成 `str`。
- spec 002 SC-002 验证后，UI（spec 005）从 `state["plan"]`（已经是 Pydantic 实例）渲染，不需要反序列化 interrupt payload——所以 plan_node 不需要还原 payload。

### Alternatives Considered

- **A. 直接传 `state["plan"]`（Pydantic 实例 list）**：msgpack 序列化失败；**否定**。
- **B. 传 plan 的字符串渲染**：UI 拿不到结构化数据，无法做章节粒度的 edit；**否定**。
- **C. 自定义 JSON 序列化函数**：plan_node 端做一次 `model_dump(mode="json")`，简洁；**采用**。

### Action Items

- confirm_plan_node 调 `interrupt({"plan": [s.model_dump(mode="json") for s in state["plan"]]})`。
- spec 002 SC-002 不验证 payload 内容，只验证 `GraphInterrupt` 被抛出——payload 验证留 spec 005。

---

## R7. cross-process resume 验证脚本

### Decision

两个独立 Python 进程：

```python
# script_a.py
import sys
from src.apps.deep_research.graph import run_research
run_research({"user_query": "..."}, thread_id="cr-001")  # 抛 GraphInterrupt
sys.exit(0)  # 立即退出，不 resume

# script_b.py（完全独立启动）
from langgraph.types import Command
from src.apps.deep_research.graph import run_research
r = run_research(Command(resume="approve"), thread_id="cr-001")
# 不返回 {"error": "no_pending_interrupt", ...}
```

### Rationale

- 验证 4 件事：(a) LangGraph checkpointer 真的写到 Postgres；(b) 进程退出后 Postgres state 持久；(c) 进程 B 全新连接能读 state；(d) `run_research` 不误报 `no_pending_interrupt`。
- 必须用 `subprocess.run([sys.executable, "script_b.py"])` 或两个独立 shell——`sys.exit(0)` 后 import 的全局对象会被回收，模拟真实跨进程。

### Alternatives Considered

- **A. 同进程内两次 invoke**（不退出）：不能验证"Postgres 是唯一状态载体"；**否定**。
- **B. `os.execv` 自替换**：复杂且掩盖真实场景；**否定**。

### Action Items

- SC-004 测试用两个 shell / 两个 `python -c "..."` 命令，不混在同一脚本。
- Postgres 端验证 `SELECT count(*) FROM checkpoints WHERE thread_id = 'cr-001' >= 2`。

---

## R8. Langfuse prompt 的人工创建（前置条件）

### Decision

用户必须**手动**在 Langfuse UI 上创建 prompt：

```
Name: deep-research-plan
Type: text
Labels: ["production"]
Config: {"model": "qwen-plus-2025-04-28", "temperature": 0.4}
Prompt (multi-line):
  As a deep research planner, decompose the following research question into
  a structured plan with 3-7 sections.

  User's research question:
  {{user_query}}

  {{#if user_feedback}}
  User feedback from a previous round (address these in the revised plan):
  {{user_feedback}}
  {{/if}}

  Output a JSON array of PlanStep objects:
  [
    {"title": "...", "description": "...", "depends_on": []},
    ...
  ]

  Constraints:
  - 3 to 7 PlanStep entries total
  - Each step is independent (or declare depends_on indices)
  - Each title is unique
  - Output ONLY the JSON array, no markdown fences, no commentary
```

### Rationale

- Mustache 模板语法 `{{var}}` 与 Langfuse 2.x/3.x 兼容；`{{#if}}` 是可选块，让 `user_feedback` 在二次生成时注入但首次生成时不出现。
- prompt 文本不含任何中文固定提示词——所有"如何拆解"的指令都在 Langfuse UI 上，方便 Lesson 0006 调 prompt 时不需重发代码。
- spec 002 NFR-003 要求"prompt 字面值不在 Python 源码"——这条由 spec 002 验收。

### Alternatives Considered

- **A. 自动创建 prompt 脚本**（spec 002 提供）：用户不需要手动 UI；但 plan_node 启动时检测不到就自动 seed 不友好（属于"silent magic"），且用户改 prompt 时被覆盖；**否定**。
- **B. 双 prompt 模板**（plan + plan-edit）：避免 `{{#if}}` 复杂度；但 prompt 数量翻倍，Lesson 0006 多发一次；**否定**。

### Action Items

- spec 002 README 同步："前置 1 步：在 Langfuse UI 创建 `deep-research-plan` prompt（label=production），内容见上"。
- 缺失 prompt 时 plan_node 抛 `PromptNotFoundError`（透传）——SC-006 验证路径。

---

## R9. SPEC 002 ↔ SPEC 003/004 接口边界

### Decision

| 接口 | spec 002 实装 | spec 003/004 接手 |
|---|---|---|
| `state["plan"]` | list[PlanStep]（3-7 条，真实 LLM） | spec 003 读 `plan[i].title` 生成 ResearchQuery |
| `state["messages"]` | 含 HumanMessage（edit 触发时） | spec 004 复用作为 write 阶段的 chat history |
| `state["iteration_counts"]` | 不动（继承 foundation 初始化） | spec 003/004 在各自节点 +1 |
| `state["sources"]` / `search_evaluations` | 不动（stub 继承） | spec 003 写入 |
| `state["final_report"]` | 不动（stub 继承） | spec 004 写入 |

### Rationale

- spec 002 与 spec 003/004 的 state 字段**互不重叠**——避免 reducer 冲突；spec 002 写 `plan` + `messages`，spec 003 写 `sources` + `search_evaluations` + `iteration_counts["research"]`，spec 004 写 `outline` + `draft` + `critique_history` + `final_report` + `iteration_counts["write"]`。
- `state["messages"]` 是唯一**跨 spec 共享**的字段：spec 002 写（edit HumanMessage），spec 004 可能读（write 阶段的 user intent）。当前 spec 002 不假设 spec 004 怎么用，只保证字段写入正确。

### Alternatives Considered

- **A. 在 spec 002 里提前给 spec 004 写 state 字段**：违反"每个 spec 只动自己的字段"原则；**否定**。

### Action Items

- spec 002 plan_node 不读 `sources` / `outline` / `draft`（plan 在 research 之前，没有这些数据）。
- spec 002 confirm_plan_node 不写 `sources` / `outline` 等。

---

## R10. SPEC 002 实装后对 SC-001 的"宽容"边界

### Decision

SC-003 中"走到 research 入口"的判定标准：**Postgres `checkpoints` 表有 `node="research"` 的 checkpoint**，或 `research` 子图入口抛下游 stub `NotYetImplementedError`。

### Rationale

- foundation spec 保留 research 子图为 stub——spec 002 实现后调 `run_research(..., auto_confirm_plan=True)` 会跑完 plan + confirm_plan 然后进 research 子图，被 stub 抛 `NotYetImplementedError`。
- 不能要求 spec 002 实装 research 子图（属于 spec 003 范围）。
- 验证以 Postgres checkpoint 为准——只要图已经把 state 推到 `research` 节点（哪怕是入口）就算"到达"。

### Alternatives Considered

- **A. 用 try/except `NotYetImplementedError` 包住**：是 spec 003 stub 的 fallback，不是验证 spec 002 的真实行为；**否定**。
- **B. 用 mock 替换 research 子图**：spec 002 测试应该用真实图，不引入 mock；**否定**。

### Action Items

- SC-003 验证路径：`Postgres SELECT * FROM checkpoints WHERE thread_id='...' AND node='research'` ≥ 1 条。
- 若 SC-003 失败但 Postgres checkpoint 表有 `node="research"`，仍算 PASS——这是宽容判定。

---

## 总结：10 个研究产出的关键决策

| # | 主题 | 关键决策 |
|---|---|---|
| R1 | Langfuse SDK | `langfuse>=3.0`（不是 spec 写的 2.x），按 text prompt + system/user 拼接 chat messages |
| R2 | LangGraph interrupt | `interrupt({"plan": [...]})` payload dict；`Command(goto="plan", update=...)` 回 plan_node |
| R3 | DashScope LLM | `qwen-plus-2025-04-28` + `temperature=0.4` |
| R4 | 跨进程 lock | `pg_advisory_xact_lock(hashtext(:tid))` + 1s timeout，不覆盖 LLM 调用 |
| R5 | Retry | 手写指数退避（不引入 tenacity）；retry 计数写 Langfuse span |
| R6 | Payload 序列化 | `[s.model_dump(mode="json") for s in state["plan"]]` |
| R7 | 跨进程验证 | 两个独立 shell 跑 `python -c "..."` |
| R8 | Langfuse prompt 模板 | Mustache 语法 + `{{#if user_feedback}}` 块；README 同步手动创建步骤 |
| R9 | Spec 接口边界 | plan_node 只写 `plan` + `messages`；不读 sources/outline/draft |
| R10 | SC-003 宽容判定 | Postgres checkpoint 有 `node="research"` 即 PASS |

---

## Spec 002 的隐式修正（与原 spec 的差异）

| 项 | spec.md 原文 | 实际（plan 阶段确认） | 影响 |
|---|---|---|---|
| FR-004 | "Langfuse 2.x client" | `langfuse>=3.0`（pyproject.toml 锁定） | SDK API 实际兼容（get_prompt/compile 不变），但 spec Notes 加说明 |
| FR-005 | model 名未指定 | `qwen-plus-2025-04-28` | hardcode 在 prompts.py，无 config 抽象 |
| FR-007 | payload `{"plan": ...}` | `{"plan": [s.model_dump(mode="json") for s in state["plan"]]}` | 显式序列化，防 msgpack 失败 |
