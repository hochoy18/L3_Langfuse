# Tasks: Deep Research Streamlit UI

**Input**: Design documents from `/specs/005-deep-research-streamlit-ui/`
**Prerequisites**: plan.md (required), spec.md (required for user stories), research.md, data-model.md, contracts/, quickstart.md

**Branch**: `[005-deep-research-streamlit-ui]`
**Created**: 2026-10-19

**Tests**: spec 005 是项目**首次**引入 pytest 的 spec（per FR-016 + Constitution Principle III "pytest 在 spec 005 落地"）。8 个 TC（TC-1 ~ TC-8 per FR-021）落在 `tests/apps/deep_research/test_app_cli.py`；coverage gate `--cov-fail-under=80` per FR-022 + SC-007。

**Organization**: Tasks grouped by user story (US1-US4 from spec.md §"User Scenarios & Testing"），按 P1 → P2 顺序交付，每 story 独立可测。

---

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 可并行（不同文件 / 无依赖）
- **[Story]**: 对应 spec.md 的 US1 / US2 / US3 / US4 优先级
- 任务描述包含绝对文件路径

## Path Conventions

- **Single project**: `src/` + `tests/` 在 repo 根目录
- **Streamlit 入口**: `src/apps/deep_research/app.py`（script）
- **CLI 入口**: `src/apps/deep_research/cli.py`（module）
- **Tests**: `tests/apps/deep_research/test_app_cli.py`

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: 项目**首次**引入 streamlit + pytest + pytest-cov-cov + pytest-asyncio 依赖 + pytest 首次配置。

- [X] T001 Add `streamlit>=1.30.0,<2.0` to `pyproject.toml::dependencies` per FR-015 + ADR-0006 "Streamlit 1.30+ 锁定"（runtime dep，不进 dev group）
- [X] T002 Add `pytest>=8.0,<9.0` + `pytest-cov>=5.0,<6.0` + `pytest-asyncio>=0.23,<1.0` to `pyproject.toml::[dependency-groups]` dev section per FR-016 + Constitution Principle III "pytest 在 spec 005 首次落地"
- [X] T003 Add `[tool.pytest.ini_options]` section to `pyproject.toml` per FR-022 + R2: `testpaths = ["tests"]` + `addopts = "-ra --strict-markers"` + `asyncio_mode = "auto"`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: spec 005 不引入新数据库 schema / state 字段 / auth 框架 / API routing / env var（per FR-017 + FR-018 + NFR-004）。所有 schema / state 继承自 spec 001-004 已冻结内容。

**⚠️ NOTE**: Phase 2 故意为空——spec 005 仅增量 UI 层 / CLI 入口层（per FR-017 + Constitution Principle I "spec-compatible"）。

**Checkpoint**: Setup 完成，user story 可开始。

---

## Phase 3: User Story 1 - 单页 UI 跑通 plan → research → write → final_report（Priority: P1）🎯 MVP

**Goal**: User 在 `http://localhost:8501` 输入 query + 点 "开始研究" → 看 plan markdown → 点 Approve → 看 final_report。Streamlit 端到端跑通 spec 001-004 已 frozen 的 deep research pipeline。

**Independent Test**: SC-001 + SC-002 per quickstart.md SC-001 / SC-002 步骤。

### Implementation for User Story 1

- [X] T004 [US1] Add script body top-level imports in `src/apps/deep_research/app.py` per FR-001: `streamlit as st` + `from langgraph.types import Command` + `from src.apps.deep_research.graph import run_research, build_graph` + `from src.apps.deep_research.state import DeepResearchState` + `from src.apps.deep_research.schemas import PlanStep` + `import uuid`
- [X] T005 [US1] Initialize `st.session_state.thread_id = str(uuid.uuid4())` at script body top (after imports, before any widget) in `src/apps/deep_research/app.py` per FR-002（idempotent: only set if `"thread_id" not in st.session_state`，避免 rerun 覆盖）
- [X] T006 [US1] Add sidebar thread_id display in `src/apps/deep_research/app.py` per FR-003(a): `st.sidebar.text_input("thread_id", value=st.session_state.thread_id, disabled=True)` 只读显示
- [X] T007 [US1] Add main area "请输入研究问题" text_input + "开始研究" button in `src/apps/deep_research/app.py` per FR-004: 按钮 click 后校验 `user_query.strip()` 非空 → 置 `last_action = "start"` → `st.rerun()`；空 query 时 `st.warning("请输入研究问题")` 不 invoke
- [X] T008 [US1] Add rerun dispatch on `last_action` at script body top (after session_state init) in `src/apps/deep_research/app.py` per FR-005: `None` → 仅渲染空 state 首页；`"start"` → 调 `_run_research_with_stages(None, thread_id)` 然后 `last_action = None`；`"approve"` / `"edit"` → 调 `_run_research_with_stages(Command(resume=user_input), thread_id)` 然后 `last_action = None`
- [X] T009 [US1] Implement `format_plan_md(plan_steps: list[PlanStep]) -> str` helper in `src/apps/deep_research/app.py` per FR-006 + C2 contract: 渲染 `# 拟议议研究计划\n\n1. **{title}**\n   {description}\n\n...`（1-based 序号 + title + description; `description is None` 省略）
- [X] T010 [US1] Add `__interrupt__` plan review rendering in `src/apps/deep_research/app.py` per FR-006: 检测 `result["__interrupt__"]` → 取 `payload = result["__interrupt__"][0].value` + `plan = payload["plan"]` → `st.markdown("## Plan 审阅")` + `st.caption(...)` + `st.markdown(format_plan_md(plan))` + `st.text_area("反馈（留空 = approve；非空 = edit 重生成）", key="plan_feedback")` + `st.button("Approve", key="approve_btn")` + `st.button("Submit Edit", key="edit_btn")`
- [X] T011 [US1] Wire Approve / Submit Edit buttons → `last_action` + `user_input` + `st.rerun()` in `src/apps/deep_research/app.py` per FR-007: Approve 按钮 → `last_action = "approve"` + `user_input = "approve"` + `st.rerun()`；Submit Edit 按钮 → 校验 `plan_feedback.strip()` 非空 → `last_action = "edit"` + `user_input = "edit: " + plan_feedback.strip()` + `st.rerun()`
- [X] T012 [US1] Add normal `final_report` rendering in `src/apps/deep_research/app.py` per FR-008: 走完 invoke 且无 `__interrupt__` 无 error → `st.markdown(result["final_report"])` + `st.success("✅ 研究完成")` + `st.download_button("下载 Markdown", data=result["final_report"], file_name=f"{thread_id}.md", mime="text/markdown")`
- [X] T013 [US1] Add error branch handling in `src/apps/deep_research/app.py` per FR-009: `result.get("error") == "no_pending_interrupt"` → `st.warning("⚠️ 该 thread 无 pending interrupt，可能已完成或不存在")`；`"thread_id_busy"` → `st.warning("⚠️ Thread 正在被另一个 session 占用，请稍后重试")`；其他 → `st.error(f"❌ {result['error']}")`；**不**调 `st.markdown(result["final_report"])`
- [X] T014 [US1] Implement `_run_research_with_stages(input_payload, thread_id) -> dict` wrapper in `src/apps/deep_research/app.py` per FR-010 + R3: `with st.spinner("研究进行中..."):` 包裹 invoke；`input_payload is None` 时自动从 `st.session_state.user_query` 构造 `{"user_query": ...}`；invoke 完成后 single-shot 在 spinner 上下文退出后写 `st.markdown("✅ 完成")`（仅一次，**不**做 Plan/Research/Write 阶段切）

**Checkpoint**: US1 完整可独立运行——User 可在单 tab 端到端跑 plan → research → write → final_report。

---

## Phase 4: User Story 2 - 跨进程 / 跨浏览器 tab 续跑同一 thread（Priority: P2）

**Goal**: User 从 Langfuse UI（或前一会话）复制 `thread_id` 粘贴到 Streamlit 侧栏 Load thread 文本框 + 点按钮 → 接管历史 thread 继续跑；Reset thread 按钮生成新 uuid 用于异常恢复。

**Independent Test**: SC-003 per quickstart.md SC-003 步骤。

### Implementation for User Story 2

- [X] T015 [US2] Add sidebar Load thread button in `src/apps/deep_research/app.py` per FR-003(b): `st.sidebar.text_input("Load thread_id (paste from another session)", key="load_thread_input")` + `st.sidebar.button("Load thread")` → 校验非空 → `st.session_state.thread_id = load_thread_input.strip()` + `st.rerun()`（**不**调 `run_research`，仅覆盖 session_state）
- [X] T016 [US2] Add sidebar Reset thread button in `src/apps/deep_research/app.py` per FR-003(c): `st.sidebar.button("Reset thread (新 uuid)")` → `st.session_state.thread_id = str(uuid.uuid4())` + `st.rerun()`

**Checkpoint**: US1 + US2 都独立可运行——单 tab 端到端 + 跨 tab resume。

---

## Phase 5: User Story 3 - 进度显示（单段 spinner + 完成收尾）（Priority: P2）

**Goal**: invoke 期间显示单段 "研究进行中..." spinner 旋转；invoke 完成后 spinner 区切到 "✅ 完成"；阻塞 invoke 模型下无虚假阶段切换（per Q4 澄清，2026-10-19）。Lesson 0007 跑 6+ 分钟 research loop 时 User 看到 spinner 旋转 + 完成收尾，不会以为浏览器坏。

**Independent Test**: SC-002 + FR-010 per quickstart.md SC-003 步骤（spinner 不切 + "✅ 完成" 一次性收尾）。

### Tests for User Story 3（REQUIRED per FR-021 TC-1 / TC-2 / TC-3）

> **NOTE**: 这些测试在 `tests/apps/deep_research/test_app_cli.py` 验证 T014 实装的 `_run_research_with_stages` 包装器。TDD 顺序：先写测试（应 fail 因 T014 未完成），再 T014 完成 T014 后 pass。

- [X] T017 [P] [US3] Add TC-1: test `__interrupt__` branch of `_run_research_with_stages` in `tests/apps/deep_research/test_app_cli.py` per FR-021 TC-1: monkeypatch `streamlit.spinner` (返回 context manager mock) + monkeypatch `streamlit.markdown` + mock `run_research` 返回 `{"__interrupt__": [(type("X", (), {"value": {"plan": [PlanStep(title="step1", description=None, depends_on=[])]}}),)]}` → assert 调用 `run_research` + assert `st.markdown(final_report)` 未调用 + assert 函数返回 `__interrupt__` dict
- [X] T018 [P] [US3] Add TC-2: test normal `final_report` branch of `_run_research_with_stages` in `tests/apps/deep_research/test_app_cli.py` per FR-021 TC-2: mock `run_research` 返回 `{"final_report": "## References\n[ref_1] ..."}` → assert 调用 `run_research` + assert 函数返回 final_report dict + assert "✅ 完成" 被 `st.markdown` 写一次
- [X] T019 [P] [US3] Add TC-3: test error branch of `_run_research_with_stages` in `tests/apps/deep_research/test_app_cli.py` per FR-021 TC-3: 两个 case — (a) mock `run_research` 返回 `{"error": "no_pending_interrupt", "thread_id": tid}` + assert 函数返回该 dict；(b) mock `run_research` 返回 `{"error": "thread_id_busy", "thread_id": tid}` + assert 函数返回该 dict；不 "✅ 完成"

**Checkpoint**: US3 验证完成——3 个 wrapper 测试 pass + spinner/completion 行为覆盖。

---

## Phase 6: User Story 4 - CLI 烟雾测试 auto_confirm_plan=True 单次跑通（Priority: P2）

**Goal**: Lesson 0007 实验脚本能调 `python -m src.apps.deep_research.cli "..."` 单次 invoke 拿到 `final_report` 打印到 stdout，**不**卡在 stdin + 不需要 Streamlit。同时 CLI 是 spec 005 上线后 CI 第一个真正"全链路 pass/fail 信号"的 hook 点。

**Independent Test**: SC-004 + SC-005 + SC-006 per quickstart.md SC-004 ~ SC-006 步骤。

### Tests for User Story 4（REQUIRED per FR-021 TC-4 / TC-5 / TC-6 / TC-7 / TC-8）

> **NOTE**: TDD 顺序：先写测试（应 fail 因 CLI 未实装），再 T024-T026 实装 CLI 后 pass。

- [X] T020 [P] [US4] Add TC-4: test `cli.py` prints `final_report` to stdout in `tests/apps/deep_research/test_app_cli.py` per FR-021 TC-4: monkeypatch `sys.argv = ["cli", "为什么 LangGraph 1.2 用 interrupt()?"]` + monkeypatch `src.apps.deep_research.cli.run_research` 返回 `{"final_report": "## References\n[ref_1] ..."}` + monkeypatch `psycopg.connect` (避免真实 DB) → assert `capsys.readouterr().out` 含 `## References` + exit code 0
- [X] T021 [P] [US4] Add TC-5: test `cli.py` raise → `sys.exit(1)` + stderr `[ERROR]` in `tests/apps/deep_research/test_app_cli.py` per FR-021 TC-5: monkeypatch `sys.argv = ["cli", "..."]` + monkeypatch `run_research` 抛 `RuntimeError("test error")` → assert stderr 含 `[ERROR] RuntimeError: test error` + exit code 1
- [X] T022 [P] [US4] Add TC-6: test `cli.py` no position arg → `sys.exit(1)` + stderr usage in `tests/apps/deep_research/test_app_cli.py` per FR-021 TC-6: monkeypatch `sys.argv = ["cli"]` (无位置参数) → assert stderr 含 argparse usage + `[ERROR] missing query argument` + exit code 1
- [X] T023 [P] [US4] Add TC-7: test `cli.py` `thread_id_busy` → `sys.exit(2)` in `tests/apps/deep_research/test_app_cli.py` per FR-021 TC-7: monkeypatch `sys.argv = ["cli", "..."]` + monkeypatch `run_research` 返回 `{"error": "thread_id_busy", "thread_id": tid}` → assert stderr 含 `[ERROR] thread_id_busy: thread_id=<tid> busy; retry later` + exit code 2
- [X] T024 [P] [US4] Add TC-8: test `cli.py --thread-id=<existing>` → warning + exit 0 in `tests/apps/deep_research/test_app_cli.py` per FR-021 TC-8: monkeypatch `sys.argv = ["cli", "...", "--thread-id=existing-tid"]` + monkeypatch `psycopg.connect` 返回 mock connection（cursor.execute 返回 `[(1,)]`） → assert stderr 含 `[cli] WARNING: thread_id=existing-tid already has 1 checkpoint(s); new run will create a fresh checkpoint and not inherit prior state` + exit code 0

### Implementation for User Story 4

- [X] T025 [US4] Implement `cli.py` argparse + run_research call in `src/apps/deep_research/cli.py` per FR-011 + FR-012 + Q7 (hardcoded): `argparse.ArgumentParser` + 位置参数 `query` (必填) + 可选 `--thread-id=<tid>` (缺省 `str(uuid.uuid4())`)；模块顶层 try/except 捕获所有 exception + `sys.exit(<non-zero>)`；调 `run_research({"user_query": query, "auto_confirm_plan": True}, thread_id=tid)`
- [X] T026 [US4] Implement `final_report` print + error handling in `src/apps/deep_research/cli.py` per FR-013 + Q5 (exit code 0/1/2 约定): invoke 成功 → `print(result["final_report"])` 到 stdout + exit 0；`result["error"] == "thread_id_busy"` → stderr 打印 `[ERROR] thread_id_busy: thread_id=<tid> busy; retry later` + `sys.exit(2)`；其他 error → stderr 打印 `[ERROR] <error_key>: <msg>` + `sys.exit(1)`
- [X] T027 [US4] Implement thread_id warning + stderr startup logs in `src/apps/deep_research/cli.py` per FR-014 + R4 (psycopg SELECT count): invoke 前调 `_check_thread_id_reuse(tid) -> int`（通过 `psycopg.connect(LANGGRAPH_CHECKPOINT_DB or DEFAULT_DB_URI)` + `SELECT count(*) FROM checkpoints WHERE thread_id = %s`）；`n > 0` 时 stderr 打印 `[cli] WARNING: thread_id=<tid> already has <n> checkpoint(s); new run will create a fresh checkpoint and not inherit prior state`；启动时 stderr 打印 `[cli] thread_id=<tid>` + `[cli] query=<query[:80]>`（Postgres 不可达时让 `_check_thread_wid_reuse` 返回 0，依赖后续 `run_research` 触发 connection error 走 exit 1 路径）

**Checkpoint**: US1-US4 都独立可运行——Streamlit UI 端到端 + 跨 tab + 进度 + CLI smoke test 都交付。

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: 文档同步 + coverage gate 验证 + 依赖完整性验证。

- [X] T028 [P] Add `## spec 005 增量（Streamlit UI + CLI 烟雾测试）` section to `src/apps/deep_research/README.md` per FR-019 + SC-009：列出 (a) `streamlit run src/apps/deep_research/app.py` 命令；(b) `python -m src.apps.deep_research.cli "..." [--thread-id=<tid>]` 命令；(c) 新依赖 `streamlit>=1.30.0` + pytest 3 个 dev dep；(d) `pytest --cov=src/apps/deep_research/app.py --cov=src/apps/deep_research/cli.py --cov-fail-under=80 tests/apps/deep_research/test_app_cli.py` 测试命令；(f) spec 005 scope 边界（per FR-017 + NFR-004，**不**改 graph.py / state.py / schemas.py）
- [X] T029 [P] Run `pytest --cov=src/apps/deep_research/app.py --cov=src/apps/deep_research/cli.py --cov-branch --cov-fail-under=80 tests/apps/deep_research/test_app_cli.py` per FR-022 + SC-007: 验证 8 个 TC (T017-T019 + T020-T024) 全 pass + `app.py` 行覆盖 ≥ 80% + `cli.py` 行覆盖 ≥ 90% + exit code 0

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — 立即可启动
- **Foundational (Phase 2)**: SKIPPED（per FR-017 + FR-018，无新 schema/state/auth/env）
- **User Stories (Phase 3-6)**:
  - **US1 (Phase 3)**: Depends on Setup — 无依赖其他 story
  - **US2 (Phase 4)**: Depends on Setup — 扩展 US1 的 sidebar (T006) 但独立可测
  - **US3 (Phase 5)**: Depends on US1 T014（wrapper 实装）以测试 wrapper 行为
  - **US4 (Phase 6)**: Depends on Setup only — 与 US1/US2/US3 完全独立
- **Polish (Phase 7)**: Depends on US1 + US2 + US3 + US4 全部完成

### User Story Dependencies

- **User Story 1 (P1)**: Setup 后可启动 — 无 story dependency
- **User Story 2 (P2)**: Setup 后可启动 — 扩展 US1 sidebar 但独立可测（侧栏 Load/Reset 是增量）
- **User Story 3 (P2)**: 依赖 US1 T014（wrapper 已实装）— 测试 wrapper 行为
- **User Story 4 (P2)**: Setup 后可启动 — 完全独立（cli.py 是新文件，不依赖 app.py）

### Within Each User Story

- **Tests 必须先写并 FAIL before implementation**（per FR-021 / Principle III TDD）
- US1 无 test tasks（per FR-021：US1 的 acceptance scenarios 是 user-facing UI 流程，wrapper 测试在 US3 覆盖）
- US3 测试（TC-1/TC-2/TC-3）依赖 US1 的 T014 wrapper — 但 spec 005 是 sequential dev，T014 先实装再 T017-T019
- US4 测试（TC-4~TC-8）依赖 T025-T027 CLI 实装 — T025-T027 在 T020-T024 之后
- 各 story 内 task 顺序由 FR 编号决定（FR-001 → FR-002 → ... → FR-022）

### Parallel Opportunities

- **Phase 1 内**: T001 / T002 / T003 都 modify `pyproject.toml` → **sequential**（同文件冲突）
- **US1 内 (T004-T014)**: 全部 modify `app.py` → **sequential**（同文件）
- **US2 内 (T015-T016)**: 都 modify `app.py` 侧栏 section → **sequential**
- **US3 内 (T017-T019)**: 全部 modify `test_app_cli.py` 但**不同 test function** → **[P] parallelizable**
- **US4 内 tests (T020-T024)**: 全部 modify `test_app_cli.py` 但**不同 test function** → **[P] parallelizable**
- **US4 内 impl (T025-T027)**: 都 modify `cli.py` → **sequential**（同文件）
- **跨 story 并行**: US1 完成后 US3 可并行；US4 可与 US1+US2+US3 全部并行（独立文件）
- **Polish 内 (T028-T029)**: T028 modify README.md；T029 是 test run → **[P] parallelizable**

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Phase 1: Setup（T001-T003）— 加依赖
3. Phase 3: US1（T004-T014）— Streamlit 单 tab 端到端 MVP
4. **STOP and VALIDATE**: SC-001 + SC-002 per quickstart.md
5. **MVP demo**: User 在 `localhost:8501` 输入 query → 看 plan → 点 Approve → 看 final report

### Incremental Delivery

1. Setup + US1 → **MVP ready**（spec 005 最小可交付）
2. + US2 → 跨 tab resume（Lesson 0007 next-day resume 必需）
3. + US3 → Progress UX 验证（3 个 wrapper 测试 pass + spinner/completion 行为正式覆盖）
4. + US4 → CLI smoke test（CI 第一个真正全链路 hook 点 + Lesson 0007 评测入口）
5. 每个 story 增加价值不破坏前序 story

### Parallel Team Strategy (Solo Dev: Sequential per Story)

Per Assumption "User 是单用户本地用 cai_he 自己"，单人 dev 串行交付。故事内任务**严格顺序**（同文件）；跨 story 间 US4 可与 US1+US2+US3 并行（不同文件 `cli.py` vs `app.py`），但单人 dev 串行化即可：

| 阶段 | 任务 | 文件 |
|---|---|---|
| Week 1 | T001-T003 | pyproject.toml |
| Week 1 | T004-T014 (US1) | app.py |
| Week 1 | T015-T016 (US2) | app.py |
| Week 2 | T017-T019 (US3 tests) | test_app_cli.py |
| Week 2 | T020-T024 (US4 tests) | test_app_cli.py |
| Week 2 | T025-T027 (US4 impl) | cli.py |
| Week 2 | T028-T029 (Polish) | README.md + coverage verify |

---

## Notes

- spec 005 引入 pytest (per FR-016 + Constitution Principle III "tests 在 spec 005 落地") — 项目**首次**关注 pytest
- `--cov-fail-under=80` 是 Constitution Principle III coverage gate 第一次落地（per FR-022 + SC-007）
- US1 的 rerun dispatch（T008）调用 US1 的 wrapper（T014）— 同文件但不同函数，可任意顺序实装；建议 T014 在 T008 后实装以便调试
- US3 测试（T017-T019）引用 US1 的 wrapper（T014）— T014 必须先实装
- CLI 测试 TC-8（T024）需要 mock `psycopg.connect` — per R4 + FR-014 + spec 001 已锁定的 `psycopg[binary]>=3.3.5`
- spec 005 PR diff 应**只**新增 `app.py` + `cli.py` + `tests/apps/deep_research/test_app_cli.py` + 修改 `pyproject.toml` + `README.md`（per FR-017 + NFR-004 + Constitution Principle I）；**不**改 `src/apps/deep_research/{graph.py, state.py, schemas.py, prompts.py, tools.py, nodes/}`