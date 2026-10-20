# Contracts: Deep Research Streamlit UI (spec 005)

**Purpose**: spec 005 的 5 个对外契约 — UI 层 / CLI 入口 / Streamlit session_state / 错误消息字符串。每一契约锁定**形状**与**前后置条件**，implementer 与单测均以此为准。

**Created**: 2026-10-19

---

## C1. `app.py::_run_research_with_stages(input_payload, thread_id) -> dict`

**用途**：spec 005 唯一的 invoke 包装器。所有"开始研究" / "Approve" / "Submit Edit" button 的 rerun 都调此函数。

**签名**：

```python
def _run_research_with_stages(
    input_payload: dict | None,
    thread_id: str,
) -> dict:
```

**前置条件**：

- `input_payload is None` 时，函数自动读 `st.session_state.user_query` 构造 `{"user_query": st.session_state.user_query}`。
- `input_payload is not None` 时（resume 分支），传 `Command(resume=...)` 构造的 dict 或 `{"user_query": ..., "auto_confirm_plan": False}` 直接给后续 `run_research`。
- `thread_id` 由 caller 传入（来自 `st.session_state.thread_id`）。

**后置条件 / 返回 shape**：

| 返回 dict 形状 | 含义 | UI 渲染 |
|---|---|---|
| `{"__interrupt__": [{"plan": [PlanStep, ...], ...}]}` | interrupt 触发，需要 plan review | 渲染 `format_plan_md(plan)` + Approve / Submit Edit button |
| `{"final_report": "..."}` | run 跑通 emit_final_report | 渲染 `st.markdown(final_report)` + download button |
| `{"error": "no_pending_interrupt", "thread_id": tid}` | 重复 Approve / Submit 但无 pending interrupt | 渲染 `st.warning(...)` |
| `{"error": "thread_id_busy", "thread_id": tid}` | Postgres advisory lock 已被另一进程占 | 渲染 `st.error(...)` + 提示稍后再试 |
| `{"error": "not_yet_implemented", "thread_id": tid}` | 节点未就绪（兜底） | 渲染 `st.error(...)` |

**前置 / 后置契约（单调性）**：

- 单一函数签名（**不**接受 `command` 参数；`input_payload` 已经是 `Command(resume=...)` dict）
- 不抛异常（吞掉所有 exception/error，统一映射到 `{"error": ...}` dict）
- 不持有 Streamlit state — 全部状态来自 caller
- 不调 LangGraph 内部 API（仅调 `run_research` 一个 public 函数）

**依赖**：`from src.apps.deep_research.graph import run_research`

---

## C2. `app.py::format_plan_md(plan_steps: list[PlanStep]) -> str`

**用途**：把 spec 001 的 `PlanStep` 列表渲染为 Markdown，UI 把 Markdown 渲染进 `st.markdown(plan_md, unsafe_allow_html=False)`。

**签名**：

```python
def format_plan_md(plan_steps: list[PlanStep]) -> str:
```

**渲染契约**：

```text
# 拟议研究计划

1. **{title_1}**
   {description_1}

2. **{title_2}**
   {description_2}

...
```

**前置条件**：

- `plan_steps` 是非空 `list[PlanStep]`（每项有 `title: str`，`description: str | None` 可选）。
- 1-based 序号（per spec 001 `Source.index: int` 约定）。

**后置条件**：

- 返回非空 `str`，可直接喂 `st.markdown`。
- `description is None` 时省略 `description` 行（不渲染空段落）。
- 不抛异常（**不**做 Pydantic 校验失败处理 — 假定 `plan_steps` 来自 LangGraph interrupt payload，已 Pydantic 校验）。

**依赖**：`from src.apps.deep_research.schemas import PlanStep`

---

## C3. `cli.py::main(argv: list[str] | None = None) -> int`

**用途**：CLI 烟雾测试入口。`python -m src.apps.deep_research.cli "<query>" [--thread-id=<tid>]`。

**签名**：

```python
import sys
def main(argv: list[str] | None = None) -> int:
```

**argparse 合约**：

| 参数 | 必填 | 含义 |
|---|---|---|
| `query`（位置参数） | ✅ | User 的研究问题；空字符串视为缺参 → exit 1 + stderr usage |
| `--thread-id=<tid>` | ❌ | 显式指定 LangGraph thread_id；缺省时 CLI 生成新 uuid |

**返回 / 退出码约定**（per Q5 澄清，2026-10-19）：

| 场景 | exit code | stdout | stderr |
|---|---|---|---|
| 跑通 emit_final_report | **0** | `result["final_report"]` Markdown | `[cli] thread_id=<tid>` + `[cli] query=<query[:80]>` |
| `run_research` 抛 exception / 返回其他 error | **1** | _ | `[ERROR] <type>: <msg>` |
| `result["error"] == "thread_id_busy"` | **2** | _ | `[ERROR] thread_id_busy: thread_id=<tid> busy; retry later` |
| `--thread-id=<existing_tid>` | **0**（含 warning） | _ | `[cli] WARNING: thread_id=<tid> already has <N> checkpoint(s); new run will create a fresh checkpoint and not inherit prior state` |
| 缺位置参数 `query` | **1** | _ | argparse usage（自动 stderr）+ `[ERROR] missing query argument` |
| Postgres 不可达 | **1** | _ | `[ERROR] <psycopg.OperationalError>: <msg>` |

**前置 / 后置契约**：

- `argv=None` 时读 `sys.argv[1:]`（标准 CLI 习惯）。
- 函数**不**抛异常 — 任何 exception 都捕获后 `[ERROR] <type>: <msg>` + exit 1。
- `result["final_report"]` 是 `str`，**不**二次格式化（避免破坏 Markdown）。
- 启动时 stdout **不**打印任何 progress（per FR-013 "stdout 单一 final_report.md result"；progress 走 stderr）。

**依赖**：`from src.apps.deep_research.graph import run_research, DEFAULT_DB_URI` + `psycopg`（per R4）+ `uuid` + `argparse`。

---

## C4. Streamlit `session_state` 字典（per `data-model.md` §1.1）

**用途**：spec 005 唯一的 in-process state 载体。

**schema（strict）**：

| key | type | 默认 | 写入者 | 读取者 | 含义 |
|---|---|---|---|---|---|
| `thread_id` | `str` | `str(uuid.uuid4())`（首次访问） | 顶部初始化 / Load / Reset | 全部 invoke | 当前 LangGraph thread_id |
| `user_query` | `str`（由 `st.text_input(key="user_query")` 绑定） | `""` | User 输入 | 首次 invoke 时读 | User 的 query |
| `plan_feedback` | `str`（由 `st.text_area(key="plan_feedback")` 绑定） | `""` | User 输入 plan review 反馈 | edit submit 时拼 | plan review 反馈 |
| `last_action` | `Literal["start", "approve", "edit"] \| None` | `None` | 各 button click 后设置 | rerun 顶部根据值分派 | 上一次 action |
| `user_input` | `str`（仅临时） | _ | Approve 按钮 / `"Submit Edit"` 按钮 | rerun 时构造 resume | 上一次 invoke 的 resume payload |

**约束**（per `data-model.md` §1.1 末尾 + FR-002 + FR-005 / NFR-004）：

- 字典**不**持久化到 Postgres / Redis / 文件
- 字典**不**与 `checkpoints` state 重叠 — 两者是独立两层
- 字典**不**引入 LangGraph reducer / 不修改 `DeepResearchState`

---

## C5. 错误消息字符串格式

**用途**：spec 005 内部所有 error / warning / log 消息的字符串格式。便于 CI grep + i18n 后续扩展。

**统一前缀规则**：

| 前缀 | 场景 | 来源 |
|---|---|---|
| `[cli] ` | CLI stderr 日志（启动 / warning） | FR-014 |
| `[ERROR] ` | CLI 错误（exit != 0 前） | FR-013 + Q5 |
| _（无前缀）_ | Streamlit `st.warning()` / `st.error()` 的 user-facing 字符串 | FR-009 |

**关键错误消息字符串**（per FR-009 / FR-013 / Q5 / Q6）：

| 字符串 | 触发场景 | UI / CLI 出口 |
|---|---|---|
| `"无待处理的 plan review 请求"` | `result["error"] == "no_pending_interrupt"` | UI → `st.warning(...)` |
| `"线程 {tid} 正在被其他请求使用，请稍后重试"` | `result["error"] == "thread_id_busy"` | UI → `st.error(...)` |
| `"Deep research 后端尚未实装: {type}: {msg}"` | `NotYetImplementedError` 兜底 | UI → `st.error(...)` + CLI → exit 1 |
| `"[ERROR] {type}: {msg}"` | CLI exception | CLI stderr + exit 1 |
| `"[ERROR] thread_id_busy: thread_id={tid} busy; retry later"` | CLI `result["error"] == "thread_id_busy"` | CLI stderr + exit 2 |
| `"[cli] WARNING: thread_id={tid} already has {n} checkpoint(s); new run will create a fresh checkpoint and not inherit prior state"` | CLI `--thread-id=<existing_tid>` | CLI stderr + exit 0 |
| `"[cli] thread_id={tid}"` | CLI 启动 | CLI stderr |
| `"[cli] query={query[:80]}"` | CLI 启动 | CLI stderr |

**i18n 注意**：当前 spec 005 仅支持中文 UI 文案 + 英文 CLI 日志。Streamlit 中文 UI 字符串**不**做 chevron 模板 — 直接 hardcoded 在 `app.py`（per Constraint #6 "prompt text never in source" 不适用此；仅 spec 001-004 的 prompt 模板受约束）。CLI stderr 日志用英文（便于 CI + 跨平台 Dev 工具输出）。

---

## 契约总览（5 合同）

| # | 名字 | 位置 | 用途 |
|---|---|---|---|
| C1 | `_run_research_with_stages` | `src/apps/deep_research/app.py` | UI 唯一 invoke 包装器 |
| C2 | `format_plan_md` | `src/apps/deep_research/app.py` | plan → Markdown 渲染 |
| C3 | `main(argv)` | `src/apps/deep_research/cli.py` | CLI smoke 入口 |
| C4 | session_state 字典 | Streamlit in-process | UI 唯一 state |
| C5 | 错误消息字符串 | `app.py` / `cli.py` | grep-able error format |

实施校验（per `data-model.md` §5）：spec 005 PR diff 应只新增 `app.py` + `cli.py` + `tests/apps/deep_research/test_app_cli.py` + 修改 `pyproject.toml` + `README.md`；**不**触碰 `src/apps/deep_research/{schemas.py, state.py, graph.py, prompts.py, tools.py, nodes/`。