# Contracts · Spec 002

> Phase 1 产出：spec 002 引入 / 修改的对外契约（函数签名 + 行为约束 + 错误类型）。

spec 002 不暴露新 HTTP / gRPC / CLI 接口；它是 LangGraph 图的内部节点实现，所以"contract"是指 **Python 函数签名 + 调用契约 + 异常类型**。

---

## C1. `src.apps.deep_research.prompts.get_prompt`

### Signature（spec 002 FR-004 实装）

```python
def get_prompt(name: str, label: str | None = None) -> "langfuse.api.text_prompt.TextPrompt":
    """Fetch a Langfuse prompt by name and optional label.

    Parameters
    ----------
    name : str
        Prompt name as registered in Langfuse UI (e.g. "deep-research-plan").
    label : str | None
        Label filter (e.g. "production", "staging", None for latest).

    Returns
    -------
    langfuse.api.text_prompt.TextPrompt
        Prompt object with `.compile(**kwargs)` method (Mustache template).

    Raises
    ------
    langfuse.api.commons.errors.Error (subclass)
        Prompt not found / network error / auth failure — propagated as-is,
        NOT wrapped in NotYetImplementedError.

    RuntimeError
        LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY not set in environment.
    """
```

### Contract

| 条件 | 行为 |
|---|---|
| Prompt 存在 + label 匹配 | 返回 TextPrompt 对象；`.compile(**kwargs)` 可用 |
| Prompt 存在 + label 不匹配 | 返回默认版本（latest） |
| Prompt 不存在 | 抛 `langfuse.api.commons.errors.Error` 透传 |
| Langfuse 服务不可达 | 抛 `langfuse.api.commons.errors.Error`（含网络错误） |
| env var 缺失 | 抛 `RuntimeError` |

### Test Cases

```python
# TC-1: prompt exists
p = get_prompt("deep-research-plan", label="production")
assert hasattr(p, "compile")
assert callable(p.compile)

# TC-2: prompt missing
with pytest.raises(Exception):  # LangfuseError subclass
    get_prompt("non-existent-prompt", label="production")
```

---

## C2. `src.apps.deep_research.prompts.get_llm`

### Signature（spec 002 FR-005 实装）

```python
from typing import Literal

def get_llm(role: Literal["plan", "research", "write", "critic", "polish"]) -> ChatOpenAI:
    """Return a ChatOpenAI instance for the given role.

    spec 002 实装 role="plan"（DashScope OpenAI-compatible qwen-plus）;
    其他 role（research/write/critic/polish）仍抛 NotYetImplementedError
    （spec 003/004 才实装）。

    Parameters
    ----------
    role : Literal["plan", "research", "write", "critic", "polish"]
        Logical LLM role.

    Returns
    -------
    langchain_openai.ChatOpenAI
        For role="plan": configured with DashScope base_url, qwen-plus-2025-04-28,
        temperature=0.4.

    Raises
    ------
    NotYetImplementedError
        For role in {"research", "write", "critic", "polish"}.

    RuntimeError
        DASHSCOPE_API_KEY not set in environment.
    """
```

### Contract

| role | 返回 | 备注 |
|---|---|---|
| `"plan"` | `ChatOpenAI(model="qwen-plus-2025-04-28", temperature=0.4)` | spec 002 唯一实装 |
| `"research"` | 抛 `NotYetImplementedError("get_llm not yet implemented: research")` | spec 003 实装 |
| `"write"` | 抛 `NotYetImplementedError` | spec 004 实装 |
| `"critic"` | 抛 `NotYetImplementedError` | spec 004 实装 |
| `"polish"` | 抛 `NotYetImplementedError` | spec 004 实装 |

### Test Cases

```python
# TC-1: role="plan" returns valid LLM
llm = get_llm("plan")
assert isinstance(llm, ChatOpenAI)
assert llm.model_name == "qwen-plus-2025-04-28"
assert llm.temperature == 0.4

# TC-2: role="research" raises NotYetImplementedError
with pytest.raises(NotYetImplementedError):
    get_llm("research")

# TC-3: DASHSCOPE_API_KEY missing
import os
saved = os.environ.pop("DASHSCOPE_API_KEY", None)
try:
    with pytest.raises(RuntimeError):
        get_llm("plan")
finally:
    if saved:
        os.environ["DASHSCOPE_API_KEY"] = saved
```

---

## C3. `src.apps.deep_research.nodes.plan.plan_node`

### Signature

```python
from src.apps.deep_research.state import DeepResearchState

def plan_node(state: DeepResearchState) -> dict:
    """Generate or re-generate the research Plan via LLM.

    Parameters
    ----------
    state : DeepResearchState
        Must contain:
        - user_query: str
        - messages: list[AnyMessage]  (last HumanMessage used as user_feedback)

    Returns
    -------
    dict
        {"plan": [PlanStep, ...]} — overwrites state["plan"].

    Raises
    ------
    NotYetImplementedError
        - LLM call failed after retry (FR-006(b)(d))
        - Plan steps outside [3, 7] (FR-002 + FR-006(c))
        - Plan is empty after retry (Edge Case)

    langfuse.api.commons.errors.Error
        Prompt not found / network error — propagated as-is (FR-004).

    Other ValidationError / ValueError
        Malformed LLM response — propagated as-is.
    """
```

### Contract

| 条件 | 行为 |
|---|---|
| 首次执行（`state["messages"]` 无 HumanMessage） | `user_feedback=""`；`prompt = get_prompt("deep-research-plan", label="production")` → `template = prompt.prompt` → `compiled = _render_mustache(template, user_query=..., max_steps=7, user_feedback="")` → `messages = _build_chat_messages(compiled, user_query)` → `llm.invoke(messages)`。**注意**：chevron 客户端渲染 Mustache；Langfuse 4.x `TextPromptClient.compile()` 不解析 `{{#if}}` 段 |
| 二次执行（`state["messages"]` 有 HumanMessage 以 "edit:" 开头） | `user_feedback=<HumanMessage content minus "edit:">`（同 chevron 路径） |
| LLM 返回 0 条 plan | 重试 1 次（同 prompt 再 invoke）；仍 0 条 → `NotYetImplementedError` |
| LLM 返回 1-2 条 plan | Pydantic validation 失败（min=3）→ `NotYetImplementedError` |
| LLM 返回 8+ 条 plan | Pydantic validation 失败（max=7）→ `NotYetImplementedError` |
| LLM HTTP 429 / 5xx | 指数退避重试 2 次（base=1s, exp=2x）；仍失败 → `NotYetImplementedError` |
| LLM HTTP 400 / 401 | 不重试，立即抛 `NotYetImplementedError` |
| LLM JSON 解析失败 | 重试 1 次（不带 backoff，立即）；仍失败 → `NotYetImplementedError` |
| `state["iteration_counts"]` | **不修改**（per NFR-002） |

### Side Effects

- 写 `state["plan"]: list[PlanStep]`
- 间接写 `state["messages"]: list[AnyMessage]`（LangChain ChatOpenAI 自动追加 AIMessage）
- 写 Langfuse span attribute `plan_retry_count`（spec 006 完整接 hook 前用 `print` 占位）
- 写 Postgres `checkpoints` 表（LangGraph 自动）

---

## C4. `src.apps.deep_research.nodes.plan.confirm_plan_node`

### Signature

```python
from langgraph.types import interrupt

def confirm_plan_node(state: DeepResearchState) -> dict:
    """Pause for user plan confirmation, then either resume or re-plan.

    Parameters
    ----------
    state : DeepResearchState
        Must contain:
        - plan: list[PlanStep]  (just written by plan_node)
        - auto_confirm_plan: bool  (default False)
        - messages: list[AnyMessage]  (used to append HumanMessage on edit)

    Returns
    -------
    dict
        - Empty ``{}`` for approve / unknown / non-string resume payloads
          (graph continues to research subgraph via the conditional edge).
        - ``{"messages": [HumanMessage(content=user_decision)]}`` for
          ``"edit: <text>"`` resume — the ``messages`` field's
          ``operator.add`` reducer appends; the conditional edge then routes
          back to ``plan``.

    Note
    ----
    LangGraph 1.2.x does not support ``Command(goto=..., update={...})``
    returning a LastValue channel key combined with the target node's own
    write to the same channel in the same tick
    (``InvalidUpdateError: Can receive only one value per step``). spec 002
    therefore uses a conditional edge instead. The function returns ``dict``
    only — no ``Command`` import.
    """
```

### Contract

| 条件 | 行为 |
|---|---|
| `auto_confirm_plan=True` | 返回 `{}`；不调 `interrupt()` |
| `auto_confirm_plan=False` + 首次执行 | 调 `interrupt({"plan": [...]})`；LangGraph 1.2+ 把图冻结在 ``__interrupt__`` 状态，``run_research`` 把 state（含 ``__interrupt__`` 键）作为正常 dict 返回（**不**抛 ``GraphInterrupt`` 异常） |
| resume payload = `"approve"` | 返回 `{}`；条件边路由到 `research` |
| resume payload = `"edit: <text>"`（len > 5） | 返回 `{"messages": [HumanMessage(content=user_decision)]}`；条件边路由回 `plan` 重新生成 |
| resume payload = 其他 string | 返回 `{}`（宽松策略 per FR-008(c)） |
| resume payload = 非 string | 返回 `{}`（不报错） |

### Side Effects

- 调 `interrupt({"plan": [s.model_dump(mode="json") for s in state["plan"]]})`（写 Postgres checkpoint；`s` 可能是 `PlanStep` 实例或经 `PydanticJsonPlusSerializer` 反序列化得到的 `dict`，用 `hasattr(s, "model_dump")` 守卫）
- edit 路径下追加 `HumanMessage` 到 `state["messages"]`（reducer `operator.add` 自动合并）
- 路由决策由 `graph.py::add_conditional_edges("confirm_plan", _after_confirm_plan, {"plan": "plan", "research": "research"})` 接管

---

## C5. `src.apps.deep_research.graph.run_research`

### Signature（spec 002 修改 + 已有）

```python
from langgraph.types import Command

def run_research(
    input: dict | Command,
    *,
    thread_id: str | None = None,
    config: dict | None = None,
) -> dict:
    """Run or resume the deep research graph.

    spec 002 additions:
      1. acquire pg_advisory_xact_lock(hashtext(thread_id)) before invoke/resume
      2. on lock contention → return {"error": "thread_id_busy", "thread_id": tid}
      3. on resume without pending interrupt → return {"error": "no_pending_interrupt", ...}
         (inherited from spec 001 FR-007)

    Parameters
    ----------
    input : dict | Command
        dict: initial state (user_query, optional auto_confirm_plan, ...).
        Command: resume command (Command(resume="approve" | "edit: ...")).

    Returns
    -------
    dict
        - On initial invoke success: final graph state (plan, sources, final_report, ...).
        - On interrupt (auto_confirm_plan=False): raises langgraph.errors.GraphInterrupt.
        - On no_pending_interrupt: {"error": "no_pending_interrupt", "thread_id": tid}.
        - On thread_id_busy: {"error": "thread_id_busy", "thread_id": tid}.

    Raises
    ------
    langgraph.errors.GraphInterrupt
        Auto-confirm=False + first invoke → interrupt triggered.
    """
```

### Contract

| input type | condition | behavior |
|---|---|---|
| `dict` | `auto_confirm_plan=False` | Acquire lock → invoke graph → interrupt throws GraphInterrupt → lock released |
| `dict` | `auto_confirm_plan=True` | Acquire lock → invoke graph → reaches research subgraph (stub throws NotYetImplementedError for spec 002) |
| `Command(resume=str)` | pending interrupt exists | Acquire lock → resume graph → return final state |
| `Command(resume=str)` | no pending interrupt | Return `{"error": "no_pending_interrupt", "thread_id": tid}` |
| any | lock contention (timeout 1s) | Return `{"error": "thread_id_busy", "thread_id": tid}` |

### Side Effects

- Acquire / release Postgres advisory lock per call
- Write to Postgres `checkpoints` table (via LangGraph)
- Read env vars: `LANGGRAPH_CHECKPOINT_DB`, `LANGFUSE_HOST`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `DASHSCOPE_API_KEY`

---

## C6. Langfuse Prompt Template Contract

### Name

`deep-research-plan`

### Type

`text`（spec 002 R1 决策——plan_node 按 text 拼 chat messages；chat 类型 prompt 留给未来 spec 评估）

### Labels

`["production"]`（默认 label；spec 002 不实现 label fallback chain）

### Placeholders

| 占位符 | 类型 | 来源 | 必填 |
|---|---|---|---|
| `{{user_query}}` | str | `state["user_query"]` | ✅ |
| `{{max_steps}}` | int | hardcoded 7 | ✅ |
| `{{user_feedback}}` | str | `state["messages"]` 最近 HumanMessage（去 "edit:" 前缀），若无则为空 | ❌ |

### Mustache 模板片段

```mustache
As a deep research planner, decompose the research question into 3-7 sections.

Research question:
{{user_query}}

{{#if user_feedback}}
User feedback from previous round (revise the plan accordingly):
{{user_feedback}}
{{/if}}

Constraints:
- 3-7 PlanStep entries
- Each step has a unique title (≤50 chars)
- Output ONLY a JSON array, no markdown fences

Output format:
[{"title": "...", "description": "...", "depends_on": []}, ...]
```

### Config

```json
{
  "model": "qwen-plus-2025-04-28",
  "temperature": 0.4
}
```

---

## C7. State Schema Contract（与 spec 001 兼容）

spec 002 不修改 `DeepResearchState` 字段——继承 spec 001 FR-003 定义。

spec 002 仅改变以下字段的**写入者**：

| 字段 | spec 001 写入者 | spec 002 写入者 |
|---|---|---|
| `plan` | stub plan_node | real plan_node（LLM） |
| `messages` | （无） | real plan_node（间接 LangChain）+ confirm_plan_node（HumanMessage） |
| `auto_confirm_plan` | run_research 入口 | 同（保留） |
| 其他 8 字段 | stub 节点 | spec 003/004 接手 |

下游消费（spec 003/004）无需知道 plan 是 stub 还是 LLM 产出——Pydantic schema 不变。
