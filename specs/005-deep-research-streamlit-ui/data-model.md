# Data Model: Deep Research Streamlit UI (spec 005)

**Purpose**: spec 005 不新增 Pydantic 实体 / state 字段 / Redis 键——它纯 UI 层 / 入口层。本文件记录 spec 005 **只读**的 Pydantic 实体 + state 字段 + Streamlit session_state 字典契约，确保 implementer 不误改 spec 001-004 已冻结 schema。

**Created**: 2026-10-19

---

## 1. spec 005 新增的运行时数据结构

spec 005 **不**新增 Pydantic 实体 / `DeepResearchState` 字段 / LangGraph reducer。

spec 005 新增的是 **Streamlit session_state 字典**（in-process，不是 Postgres / Redis）：

### 1.1 Streamlit `session_state` 契约（C4 contract）

| key | type | 默认 | 写入者 | 读取者 | 含义 |
|---|---|---|---|---|---|
| `thread_id` | `str` | `str(uuid.uuid4())`（首次访问生成） | `app.py` 顶部初始化 / Load thread 按钮 / Reset thread 按钮 | 全部 invoke 调用 | 当前 UI session 的 LangGraph thread_id |
| `user_query` | `str`（受 `st.text_input("研究问题", key="user_query")` 控制） | `""` | User 输入 | 首次 invoke 时读作 `{"user_query": user_query}` | User 当前的研究问题 |
| `plan_feedback` | `str`（受 `st.text_area(...)` 控制） | `""` | User 输入 plan review 反馈 | edit submit 时拼为 `Command(resume="edit: " + text)` | plan review 阶段 User 的修改反馈 |
| `last_action` | `Literal["start", "approve", "edit"] \| None` | `None` | 各 button click 后设置 | rerun 顶部根据值分派 `_run_research_with_stages` | 上一次按钮触发的 action，决定 rerun 时调什么 invoke |
| `user_input` | `str`（仅临时） | _ | "Approve" 按钮设 `"approve"`；"Submit Edit" 按钮设 `"edit: " + text` | rerun 时构造 `Command(resume=user_input)` | 上一次 invoke 的 resume payload |

**约束**：
- session_state 字典**不**持久化到 Postgres（per Assumption "Streamlit tab 重启后 session_state 丢失"）
- session_state 字典**不**与 Postgres `checkpoints` 表的 state 重叠——它们是两层独立状态（UI in-process + LangGraph persisted）
- session_state 不引入新的 reducer / 不修改 `DeepResearchState`（per FR-017 + NFR-004 + Constitution Principle I "spec-compatible"）

---

## 2. spec 005 只读的 Pydantic 实体（来自 spec 001）

UI 层**只读**以下 Pydantic 实体，**不**修改 / 不扩展：

### 2.1 `PlanStep`（schemas.py）

| 字段 | 类型 | UI 用途 |
|---|---|---|
| `title` | `str`（必填） | `format_plan_md` 渲染 `### {i}. {title}` 标题 |
| `description` | `str \| None` | `format_plan_md` 渲染章节描述 |
| `depends_on` | `list[int]` | UI **不**展示（per Q1 决策 "整段反馈文本模式"，per-step 编辑不需要 DAG 信息） |

### 2.2 `Plan`（schemas.py）

| 字段 | 类型 | UI 用途 |
|---|---|---|
| `steps` | `list[PlanStep]` | interrupt payload `payload["plan"]` 整体读入 → 传给 `format_plan_md` |

### 2.3 `FinalReport`（schemas.py）

| 字段 | 类型 | UI 用途 |
|---|---|---|
| `markdown` | `str` | `result["final_report"]`（state 字段）→ `st.markdown(final_report)` + `st.download_button("下载 Markdown", data=final_report, file_name=f"{thread_id}.md")` |

### 2.4 其他 9 个实体

UserQuery / ResearchQuery / Source / SearchEvaluation / Outline / Draft / Critique / PolishedDraft / Reference — UI 层**不直接读**，仅通过 `state["final_report"]` Markdown 字符串间接消费。

---

## 3. spec 005 只读的 DeepResearchState 字段（来自 spec 001-004）

`DeepResearchState` 是 spec 001 frozen TypedDict（per spec 001 FR-003 + spec 003 + spec 004 增量）。spec 005 只读以下字段：

| 字段 | 类型 | UI 用途 |
|---|---|---|
| `plan` | `list[PlanStep]` | `confirm_plan_node` interrupt payload `{"plan": [...]}` 直接读取，**不**经过 reducer |
| `messages` | `list[AnyMessage]` | `_after_confirm_plan` 条件边读最新 HumanMessage（spec 002 FR-008），UI **不**直接读；spec 002 / graph.py 处理 |
| `final_report` | `str` | `result["final_report"]` 读取 → `st.markdown` + `st.download_button` |

UI **不读**的字段：`user_query`（input 时传入，不读）、`raw_sources` / `sources` / `research_queries` / `search_evaluations` / `outline` / `draft` / `critique_history`（debug 不在 spec 005 scope）、`iteration_counts`（debug 不在 spec 005 scope，per Q2 决策）、`auto_confirm_plan`（spec 002/003/004 / graph.py 处理，CLI hardcoded `True`，UI 默认 `False`）。

---

## 4. spec 005 只读的 CLI argv 契约

CLI argparse 契约（C3 contract）：

```
python -m src.apps.deep_research.cli <query> [--thread-id=<tid>]
```

| 参数 | 必填 | 类型 | 含义 |
|---|---|---|---|
| `query`（位置参数） | ✅ | `str` | User 的研究问题；空字符串视为缺参（per US4 acceptance scenario） |
| `--thread-id=<tid>` | ❌ | `str` | 显式指定 LangGraph thread_id；缺省时 CLI 生成新 uuid（per FR-012） |

---

## 5. spec 005 与 spec 001-004 的边界

- **不**改 `schemas.py`（12 个 Pydantic 实体 frozen，per spec 001 FR-002）
- **不**改 `state.py`（12 字段 TypedDict frozen，per spec 001 FR-003 + spec 003 US2/S3 + spec 004 FR-014 Critique suggestions 增量）
- **不**改 `graph.py`（`build_graph` / `run_research` / `_acquire_thread_lock` / `_emit_final_report_node` 全部继承，per spec 001 FR-004/FR-007 + spec 002 FR-012-017 + spec 004 FR-007-009）
- **不**改 `prompts.py` / `tools.py` / `nodes/`（per FR-017 + NFR-004）

**实施校验**：spec 005 PR 在 diff 上**不应**触及 `src/apps/deep_research/{schemas.py, state.py, graph.py, prompts.py, tools.py, nodes/`**；只允许新增 `app.py` / `cli.py` + 修改 `README.md` + `pyproject.toml`（per FR-015/FR-016/FR-019 + Principle I）。