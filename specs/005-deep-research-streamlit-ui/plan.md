# Implementation Plan: Deep Research Streamlit UI

**Branch**: `[005-deep-research-streamlit-ui]` | **Date**: 2026-10-19 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/005-deep-research-streamlit-ui/spec.md`

## Summary

spec 005 在不改动 spec 001-004 已冻结的 `state.py` / `schemas.py` / `graph.py` / `prompts.py` / `tools.py` / `nodes/`（per FR-017 + Constitution Principle I）的前提下，提供 deep research agent 的两个**入口层**：

1. **`src/apps/deep_research/app.py`** — Streamlit 单页 UI（per ADR-0006），调用 `run_research()` 已暴露的接口 + `Command(resume=...)` resume 模式。引入 `_run_research_with_stages` 包装器 + `format_plan_md` 渲染辅助 + Streamlit `session_state` 模式。
2. **`src/apps/deep_research/cli.py`** — CI 烟雾测试入口（per ADR-0006 + spec 002 FR-011），用 `auto_confirm_plan=True` 旁路 interrupt，单次 invoke 打印 `final_report` Markdown 到 stdout。

技术路径：
- **不**引入新 LangGraph 节点 / 状态字段 / prompt / tool（per FR-017 + FR-020 + NFR-004）——UI 层是 `run_research()` 的 client
- **不**引入新 env var（per FR-018 + Constitution Constraint #7）
- **引入 4 个新依赖**：`streamlit>=1.30.0`（runtime，per FR-015）+ `pytest>=8.0` / `pytest-cov>=5.0` / `pytest-asyncio>=0.23`（dev，per FR-016 + Constitution Principle III "pytest lands in spec 005"）
- **测试路径**：项目**首次**引入 pytest + coverage gate，6 个 TC 覆盖 4 类分支（per FR-021 + Constitution Principle III ≥80% line+branch）

## Technical Context

**Language/Version**: Python 3.11+（per `pyproject.toml` `requires-python = ">=3.11"`，实装环境为 3.13.13）
**Primary Dependencies**: streamlit>=1.30.0（runtime，new）+ pytest>=8.0 + pytest-cov>=5.0 + pytest-asyncio>=0.23（dev，new）+ 继承 spec 001-004 的 langgraph>=1.2.11 / langchain-openai>=1.6.2 / langfuse>=3.0 / chevron>=0.14.0 / psycopg[binary]>=3.3.5 / pydantic>=2.7
**Storage**: PostgreSQL（继承 spec 001 / ADR-0005，PostgresSaver 跨进程 resume 载体）
**Testing**: pytest 8.0+（项目**首次**）+ pytest-cov 5.0+（coverage gate）+ pytest-asyncio 0.23+（per FR-016 + FR-021 + FR-022 + Principle III）
**Target Platform**: Local development（macOS / Linux，per ADR-0006 "本地 streamlit run" + "single-user 本地用"）
**Project Type**: Library entry layer（Streamlit single-page web UI + CLI smoke test）——主包是 `src/apps/deep_research/` 模块；spec 005 是模块的 client 入口
**Performance Goals**: P50 wall-clock latency `app.py` 单次 rerun < 200ms（per NFR-001）；CLI 单次 invoke < research + write 全流程 P50 + 5s overhead（per NFR-002）
**Constraints**: 单用户本地用（per ADR-0006 + Assumption "User 是单用户本地用 cai_he 自己"）；无 auth / 多用户（per ADR-0006 "未来触发条件：若需要多用户协作 / 远程访问"）；streamlit 部署模式本地 `streamlit run`（per ADR-0006 + Assumption "Streamlit 部署模式是本地"）
**Scale/Scope**: 1 个 Streamlit 单页 + 1 个 CLI 入口 + 6 个 pytest TC + 22 个 FR + 9 个 SC + 8 条 Edge Cases；不引入 SSE / websocket / Langfuse trace 写入 / 多用户 / 实时协作

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle / Constraint | Status | Notes |
|---|---|---|
| I. Module-First & Spec Compatibility | ✓ | `app.py` / `cli.py` 仅作为 `run_research()` client，**不**改 `state.py` / `schemas.py` / `graph.py`（per FR-017）。新依赖 `streamlit` + `pytest` 在 `pyproject.toml` 内，不修改 module 内部 schema。 |
| II. CLI-Grade Smoke Tests | ✓ | `cli.py` 即为 CLI smoke test entry（per FR-011 ~ FR-014 + ADR-0006）；exit code 约定（0/1/2）经 Q5 澄清显式化。 |
| III. Test-First & Coverage Gate | ✓ | spec 005 引入 pytest + pytest-cov（per FR-016 + FR-021 + FR-022）；`pytest --cov=app.py --cov=cli.py --cov-fail-under=80` 必须 exit 0；6 个 TC 覆盖 4 类分支。 |
| IV. Integration Testing at Module Boundaries | ✓ | `app.py` 调 `run_research()` + `build_graph()` 是跨模块边界；`test_app_cli.py` TC-1 / TC-3 用 monkeypatch mock `run_research` + assert result dict shape（per spec 001-004 nodes contracts §C3 模式）。 |
| V. Observability, Versioning, Simplicity | ✓ | **不**引入 Langfuse hook（spec 006 范畴）；**不**引入 SSE / websocket；**不**预埋 un-specced feature（per FR-020 + Principle V Simplicity）。CLI / UI stderr / print 走 spec 002/003/004 一致的"单行带数字计数"模式。 |
| Project Constraint #1 (State schema invariants) | ✓ | spec 005 **不**改 state 字段；UI 只读 `plan` / `final_report` / `messages`（per FR-017 + NFR-004）。 |
| Project Constraint #2 (Prompt text never in source) | ✓ | spec 005 **不**触碰 prompt；UI 仅调 `run_research()`，prompt 仍由 spec 002/003/004 实装的节点从 Langfuse 拉。 |
| Project Constraint #3 (`iteration_counts` increment discipline) | ✓ | spec 005 **不**触碰 `iteration_counts`；counter 仍由 spec 003 / 004 的节点入口 `+1`。 |
| Project Constraint #4 (Error propagation contract) | ✓ | UI / CLI 透传 `NotYetImplementedError`（UI → `st.error`；CLI → `[ERROR] <type>: <msg>` + exit 1）；UI 把 `{"error": ...}` 转为 `st.warning` / `st.error`，**不**触发新错误路径。 |
| Project Constraint #5 (Postgres is the only state source) | ✓ | CLI 不绕开 PostgresSaver（per Assumption "CLI 与 Streamlit 共享同一 run_research() 入口"）；UI 跨 tab resume 依赖 Postgres（per US2 acceptance scenarios）。 |
| Project Constraint #6 (Mustache rendered client-side via chevron) | ✓ | spec 005 **不**触碰 prompt 渲染。 |
| Project Constraint #7 (Env-var hygiene) | ✓ | spec 005 **不**新增 / 重命名 env var（per FR-018）；所有现有 env var 不变。 |
| Project Constraint #8 (Library versions) | ✓ | spec 005 新增 4 个依赖（streamlit 1.30+ runtime + pytest 3 个 dev），均在本 spec 显式记录 + ADR 由 spec 充当（per FR-015 + FR-016 + Assumption）。 |

**Gate 结果**：✓ 全部通过。Complexity Tracking 表为空（无 violation 需要 justify）。

## Project Structure

### Documentation (this feature)

```text
specs/005-deep-research-streamlit-ui/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
│   └── README.md        # app.py + cli.py contracts
├── checklists/
│   └── requirements.md  # Spec quality checklist
└── spec.md              # Feature specification
```

### Source Code (repository root)

```text
src/apps/deep_research/
├── README.md               # +spec 005 增量章节 (per FR-019)
├── graph.py                # 不变（继承 spec 001-004）
├── state.py                # 不变
├── schemas.py              # 不变
├── prompts.py              # 不变
├── tools.py                # 不变
├── nodes/                  # 不变
├── app.py                  # 🆕 spec 005 — Streamlit 单页 UI
└── cli.py                  # 🆕 spec 005 — CLI 烟雾测试

tests/apps/deep_research/
├── __init__.py
├── conftest.py
├── test_foundation.py
├── test_schemas.py
└── test_app_cli.py         # 🆕 spec 005 — 6 个 TC（per FR-021）

pyproject.toml              # +streamlit +pytest +pytest-cov +pytest-asyncio
                            # +[tool.pytest.ini_options] testpaths 配置
```

**Structure Decision**: 单 project layout（沿用 spec 001-004 模块结构）。`app.py` + `cli.py` 作为 `src/apps/deep_research/` 模块的 client 入口（per ADR-0006）；`tests/apps/deep_research/test_app_cli.py` 作为模块测试扩展（per FR-021 + Principle III + test_foundation.py / test_schemas.py 模式）。

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| _（无）_ | — | — |

---

## Phase 0: Research（详见 [research.md](./research.md)）

Phase 0 解决 spec 005 的 4 个技术 unknowns：
- **R1**: Streamlit `app.py` 单测模式选型（`streamlit.testing.v1.AppTest` vs monkeypatch `st.*`）
- **R2**: pytest 8 + pytest-cov 5 + pytest-asyncio 0.23 + Streamlit 1.30 版本兼容性
- **R3**: `_run_research_with_stages` 包装器在 invoke 阻塞期间的诚实 UX 边界（per Q4 澄清）
- **R4**: CLI thread_id 复用 warning 的检测方式（Postgres `SELECT count(*)` 性能 vs sync pg）

输出：[research.md](./research.md) 已交付。

## Phase 1: Design & Contracts（详见 [data-model.md](./data-model.md) + [contracts/README.md](./contracts/README.md) + [quickstart.md](./quickstart.md)）

Phase 1 落地 spec 005 的 3 类交付物：
- **D1** data-model.md：spec 005 不新增实体，引用已冻结的 Pydantic 实体 + state 字段 + session_state 字典契约
- **D2** contracts/README.md：5 个对外契约（C1 `app.py::run_research_with_stages` / C2 `app.py::format_plan_md` / C3 `cli.py` argparse / C4 `app.py` session_state / C5 错误消息字符串格式）
- **D3** quickstart.md：SC-001 ~ SC-009 的可运行验证步骤

输出：data-model.md + contracts/README.md + quickstart.md 已交付。

---

## 实现路径概要（供 Phase 2 /speckit-tasks 拆任务时参考）

### T1. 引入依赖（pyproject.toml）

- `pyproject.toml::dependencies` 新增 `"streamlit>=1.30.0,<2.0"`（per FR-015 + Assumption）
- `pyproject.toml::[dependency-groups] dev` 新增 `"pytest>=8.0,<9.0"` + `"pytest-cov>=5.0"` + `"pytest-asyncio>=0.23"`（per FR-016 + Principle III）
- `pyproject.toml` 新增 `[tool.pytest.ini_options]` section：`testpaths = ["tests"]` + `addopts = "-ra --strict-markers"`（pytest 标准配置）

### T2. `src/apps/deep_research/cli.py`（per FR-011 ~ FR-014 + ADR-0006）

- `argparse` 解析位置参数 `query`（必填）+ 可选 `--thread-id=<tid>`
- 调用 `run_research({"user_query": query, "auto_confirm_plan": True}, thread_id=tid)`（per Q7 决策 hardcoded）
- 成功 → `print(result["final_report"])` 到 stdout；error / busy → `[ERROR] ...` 到 stderr + exit 1 / 2（per Q5）
- `--thread-id` 已存在 checkpoint → stderr `[cli] WARNING: thread_id=<tid> already has N checkpoint(s); ...`（per Q6）
- 启动时 stderr 打印 `[cli] thread_id=<tid>` + `[cli] query=<query[:80]>`（per FR-014）

### T3. `src/apps/deep_research/app.py`（per FR-001 ~ FR-010 + ADR-0006）

- script body 顶部初始化 `st.session_state.thread_id = str(uuid.uuid4())`（per FR-002）
- 侧栏：`st.text_input("thread_id", disabled=True)` + Load thread 文本框 + Load thread 按钮 + Reset thread 按钮（per FR-003）
- 主区：`st.text_input("研究问题")` + "开始研究" 按钮（per FR-004）
- 按钮 click → `st.session_state.last_action = "start" / "approve" / "edit"` + `st.session_state.user_input = ...` + `st.rerun()`（per FR-005 + FR-007）
- rerun 顶部：根据 `last_action` 调 `_run_research_with_stages(payload, thread_id)`（per FR-005）
- 检测 `result["__interrupt__"]` → 渲染 plan review 页（per FR-006）
- 检测 `result.get("error")` → 渲染 warning / error（per FR-009）
- 完成 → `st.markdown(result["final_report"])` + `st.success("✅ 研究完成")` + `st.download_button("下载 Markdown", ...)`（per FR-008）
- `_run_research_with_stages` 包装器：`st.spinner("研究进行中...")` 包裹 invoke + 完成后 single-shot `st.empty().markdown("✅ 完成")`（per FR-010 + Q4 澄清）
- `format_plan_md(plan)` 辅助：把 `list[PlanStep]` 渲染为 `### {i}. {title}\n{description}\n`（per FR-006 (3) + US1 acceptance scenario 2）

### T4. `tests/apps/deep_research/test_app_cli.py`（per FR-021 + Principle III）

- 6 个 TC + 1 个 TC-7 + 1 个 TC-8 = 8 个测试（per Q5/Q6 澄清增量）
- TC-1: `_run_research_with_stages` 检测 `__interrupt__` 不渲染 final_report（per FR-021 + US1）
- TC-2: `_run_research_with_stages` 正常 final_report 分支渲染（per FR-021 + US1 + US4）
- TC-3: error 分支（`no_pending_interrupt` / `thread_id_busy`）→ `st.warning`（per FR-009 + FR-021 + US1）
- TC-4: `cli.py` 调 `run_research` 单次 + print final_report 到 stdout（per FR-013 + FR-021 + US4）
- TC-5: `cli.py` invoke raise → `sys.exit(1)` + stderr `[ERROR]`（per FR-013 + FR-021 + US4）
- TC-6: `cli.py` 无位置参数 → `sys.exit(1)` + stderr usage（per FR-021 + US4 Edge Case）
- TC-7: `cli.py` `thread_id_busy` → `sys.exit(2)` + stderr `[ERROR] thread_id_busy`（per Q5 + FR-021）
- TC-8: `cli.py --thread-id=<existing_tid>` → stderr `[cli] WARNING: ...` + exit 0（per Q6 + FR-021）
- 全程 monkeypatch 替换 `streamlit.*` / `print` / `sys.exit`（per FR-022 + Assumption "Streamlit 没有 headless 单测 fixture"）

### T5. `src/apps/deep_research/README.md`（per FR-019 + Constraint #6）

- 新增 `## spec 005 增量（Streamlit UI + CLI 烟雾测试）` 章节
- 列出 `app.py` / `cli.py` 入口命令 + 新增 `streamlit` 依赖 + pytest 引入 + 测试运行命令

### 关键 path（scope boundary 复述）

- **不**改 `graph.py` / `state.py` / `schemas.py` / `prompts.py` / `tools.py` / `nodes/`（per FR-017 + NFR-004）
- **不**新增 state 字段 / Pydantic 实体 / env var / prompt / Langfuse hook（per FR-017 + FR-018 + FR-020 + NFR-005）
- **不**做 plan 不端 / Langfuse trace 写入 / SSE / websocket / multi-user / 实时协作（per FR-020 + Out of Scope）

---

## Constitution Check（Re-check post Phase 1）

| Principle / Constraint | Status | Notes |
|---|---|---|
| I. Module-First & Spec Compatibility | ✓ | Phase 1 design 不引入新 state / schema / graph 内部修改；UI / CLI 仅为 module entry layer。 |
| II. CLI-Grade Smoke Tests | ✓ | `cli.py` 即 CLI smoke test；exit code 约定显式化；stderr 提示便于 CI grep。 |
| III. Test-First & Coverage Gate | ✓ | 8 个 TC（FR-021 + Q5/Q6 澄清增量）+ `pytest --cov-fail-under=80` gate。 |
| IV. Integration Testing at Module Boundaries | ✓ | TC-1/TC-3 monkeypatch `run_research` + assert result dict shape；TC-4/TC-5/TC-6/TC-7/TC-8 monkeypatch `print` / `sys.exit` 验证 CLI 边界。 |
| V. Observability, Versioning, Simplicity | ✓ | 不引入 Langfuse hook / SSE / websocket / 多用户 / 实时协作；stderr / print 走单行带数字计数模式（与 spec 002/003/004 一致）。 |

Phase 1 后 gate 仍 ✓ 全部通过。无 Complexity Tracking 条目。