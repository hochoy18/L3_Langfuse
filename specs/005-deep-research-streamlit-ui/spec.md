# Feature Specification: Streamlit UI 入口（Streamlit 单页 + CLI 烟雾测试）

**Feature Branch**: `[005-deep-research-streamlit-ui]`
**Created**: 2026-10-19
**Status**: Draft
**Input**: User description: "基于 001 + 002 + 004，规划 spec 005：Streamlit UI 入口。app.py 实现 Streamlit 单页 UI（session_state 存 thread_id / 输入框收 user_query / 检测 __interrupt__ 渲染 plan + Approve/Edit 按钮 / run 结束 st.markdown(final_report)），progress 显示 research/write 阶段，cli.py 实现 CLI 烟雾测试 auto_confirm_plan=True 全流程 print final_report 到 stdout。验收：streamlit run app.py 输入 query → 看 plan → 点 Approve → 看 progress → 看 final report；关掉 terminal 后用同一 thread_id 重新 invoke 能续上；CLI 跑通无异常。"

**Source of truth**: 本 spec 只覆盖 Phase 5（UI 入口层：Streamlit 单页 + CLI 烟雾测试）。在 spec 001 foundation / spec 002 plan-confirm / spec 003 research-loop / spec 004 write-loop 之上增量实现，不引入新的 LangGraph 节点 / state 字段 / prompt / tool。设计意图见 `src/apps/deep_research/CONTEXT.md §1 Actors` 与 `docs/adr/0003-interrupt-for-plan-confirm.md` / `0006-streamlit-ui-entry.md`。

---

## Clarifications

### Session 2026-10-19

- **Q1**（plan 编辑粒度）：UI 上的 "Edit plan" 交互是让 User 改一个**整段反馈文本**（如 "增加可观测性章节"），还是给每个 `PlanStep` 提供独立文本框做精细编辑？→ A: **整段反馈文本**模式。理由：(a) spec 0、ADR-0003 已锁定 `Command(resume="edit: <自由文本>")` 走 plan_node 二次重生成（spec 002 FR-008），UI 只需把 User 文本喂给 confirm_plan_node，无需做 per-step 编辑器；(b) 整段反馈对齐 LLM "用自然语言描述意图" 的 ergonomic；per-step 编辑会强迫 User 进入 "agent 内部数据结构"心智模型，反而不自然；(c) spec 002 conditional edge route 已处理 "edit:" 重生成链路（per spec 002 FR-008 + graph.py `_after_confirm_plan`），UI 不需要新增 schema。**实装**：`st.text_area("反馈（留空 = approve）")` 单文本框 + Approve 按钮——空文本 = approve，非空文本 = `Command(resume="edit: " + text)`。

- **Q2**（progress 显示颗粒度）：研究 / 写作子图（spec 003 research-loop / spec 004 write-loop）目前不流式输出——`graph.invoke()` 是阻塞到 END 才返回。UI 要在 invoke 期间显示进度，只能靠 `st.spinner` / `st.empty()` 文字 placeholder，**无法**显示真实 sub-graph iteration 数（per-state 实时 snapshot 需要 spec 006 Langfuse hooks 介入）。→ A: UI 阶段用 **单段 spinner + 完成收尾**（per Q4 澄清，2026-10-19），**不**显示 iteration 数。理由：(a) 单段 `st.spinner("研究进行中...")` 已经是阻塞 invoke 模型的诚实反馈（无虚假阶段切换）；(b) 真实 iteration 数 + node-level 推送靠 spec 006 Langfuse `@observe` hook（sse / websocket），spec 005 提前引入会 over-engineer；(c) 阶段切换的真实信号只能来自 LangGraph NodeCallEvent / asyncio wait hack，引入会让 FR-010 与 Q2 "不引入 sub-graph streaming hook" 决策冲突。**实装**：引入 `_run_research_with_stages(...)` 包装器，里面只做 `st.spinner("研究进行中...")` 包裹 invoke + invoke 完成后 single-shot `st.empty().markdown("✅ 完成")` 收尾；不修改 `run_research` 签名。

- **Q3**（CLI 烟雾测试的"auto_confirm_plan=True"语义 + 报告落盘）：CLI 的目的是 Lesson 0007 实验 + CI 烟雾测试，不是给人类交互。CLI 跑通路径与 Streamlit 一样是 plan → confirm_plan → research → write → emit_final_report，但 CLI 必须**单次 invoke 不卡在 stdin**（per spec 002 FR-011 "auto_confirm_plan=True 单次 invoke 跑到底"）。→ A: CLI 用 `auto_confirm_plan=True` 旁路 interrupt（per spec 002 FR-009 + FR-011），单次 invoke 直接打印 `state["final_report"]` Markdown 到 stdout。理由：(a) 与 ADR-0003 "Lesson 0007 实验通过 auto_confirm_plan=True 旁路" 决策一致；(b) 写到磁盘（如 `report.md`）是 Lesson 0007 实验脚本的职责，CLI 只打印不落盘——避免 CLI 引入文件 IO 失败作为新错误路径；(c) print final_report 到 stdout 让 CI 可直接 grep 验证（如 `assert "## References" in stdout`）。**实装**：`python -m src.apps.deep_research.cli "<query>" [--thread-id=<tid>]`，调 `run_research({"user_query": ..., "auto_confirm_plan": True}, thread_id=...)` 一次，print final_report 到 stdout，exit 0 on success / non-zero on raise。
- **Q4**（`_run_research_with_stages` 阶段占位符颗粒度，澄清 FR-010 自相矛盾）：FR-010 同时声明了"4 段切换 + 单次更新到完成"两条互相矛盾的子句。在 LangGraph `invoke()` 阻塞模型 + 无 sub-graph callback hook 的现状下，UI 端无法在 invoke 期间真实感知 Plan → Research → Write 的 node 切换。→ A: 降为 **单段 `st.spinner("研究进行中...")` + invoke 完成后 single-shot 把 spinner 区切到 `st.empty().markdown("✅ 完成")`**。理由：(a) 与 Q2 决策 "不引入 sub-graph streaming hook" 一致；(b) 4 段切换在阻塞 invoke 模型下要用 LangGraph NodeCallEvent / asyncio wait hack 驱动，会产生 fake placeholder 切换——是 dead code；(c) YAGNI：单段 spinner + "完成" 收尾的 UX 反馈足够覆盖 Lesson 0007 跑 6+ 分钟 research loop 的"页面无响应"焦虑；(d) 真实阶段切换留给 spec 006 Langfuse hooks（推送 state 到前端）。**澄清后**：Q2 阶段文字改为 "单段 spinner + 完成 收尾"；FR-010 (d) 子句从 "3 个阶段切换" 改为 "完成后 single-shot 切到 ✅ 完成"；US3 acceptance scenario 1 + SC-002 同步去 4 段切文字。
- **Q5**（CLI 退出码约定）：CLI 错误路径有两种性质截然不同的失败——`thread_id_busy`（临时冲突，1s 锁等待超时，retry-able）与通用 error（Postgres 不可达 / Langfuse 4xx / LLM 鉴权失败等，fatal）。FR-013 笼统说"exit 1 on error" 与 Edge Cases "exit code 2 for thread_id_busy" 互相矛盾。→ A: **`thread_id_busy` exit 2，通用 error exit 1**，正常完成 exit 0。理由：(a) 对齐 Edge Cases "exit 2 for thread_id_busy" 暗示方向；(b) Lesson 0007 实验脚本能写 `if exit_code == 2: sleep + retry` 区分 retry-able vs fatal；(c) 与 POSIX 惯例一致（< 64 留给应用层特殊信号）。
- **Q6**（CLI `--thread_id` 复用提示）：CLI `--thread_id` 在跨多次调用复用时（如 Lesson 0007 实验脚本连续跑 5 个 query + 同 thread_id 聚合 Langfuse trace），Postgres `checkpoints` 表会追加新 checkpoint（旧的不删，per spec 001 SC-005 "no state leakage"）。spec 当前没有声明 CLI 是否提示。→ A: **stderr 打印 `[cli] WARNING: thread_id=<tid> already has N checkpoint(s); new run will create a fresh checkpoint and not inherit prior state` + 仍 exit 0 正常跑**。理由：(a) Lesson 0007 dev 能区分"聚合 trace"vs"真共享 state"；(b) 不阻止复用（per spec 001 SC-005 "no state leakage"）；(c) 检测方式：invoke 前 `SELECT count(*) FROM checkpoints WHERE thread_id = :tid`，N>0 打印 warning。**不**阻塞 dev 实验（per C 选项太严苛），**不**完全 silent（per B 选项 dev 不可见）。
- **Q7**（CLI `--auto-confirm` flag 是否开放）：CLI 当前在 FR-012 写死 `auto_confirm_plan=True`（per ADR-0006 + spec 002 FR-011 "CLI 仅 CI 烟雾测试"）。是否要开放 `--auto-confirm=true/false` flag 让 dev 用 CLI 测 interrupt 流程。→ A: **保持 hardcoded `auto_confirm_plan=True`**。理由：(a) 严守 ADR-0006 "CLI 仅 CI 烟雾测试 / headless" 边界；(b) interrupt flow 验证有 Streamlit UI（per US2 + US3 acceptance scenarios），CLI 兼任会让 `auto_confirm_plan=True` 不再是 CLI 的"必要前提"——变成"可选 flag"，模糊 smoke test 语义；(c) Lesson 0007 实验需要 `auto_confirm_plan=True` 旁路做 N 个 query 自动化跑，CLI 不能让 dev 误传 `false` 把实验卡在 stdin 上。FR-012 不变。

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 单页 UI 跑通 plan → research → write → final_report（P1）

作为 **deep research agent 的 User**，我能打开 `http://localhost:8501`、在文本框输入研究问题、点 "开始研究"，然后看到一段完整的 **plan markdown 渲染**（来自 LangGraph interrupt payload）——证明 UI 已经正确接到 `run_research()` + LangGraph `interrupt()` 链路。

**Why this priority**: P1，spec 005 的核心交付物。前 4 个 spec 已经把"图能跑出 final_report"做到，但**没人能跑图**——只有 dev 用 `python -c "..."` 单行调用。Streamlit 是把 dev-only 工具变成 User-facing 产品的最后一步。

**Independent Test**: `streamlit run src/apps/deep_research/app.py` 启动后浏览器访问 `http://localhost:8501`，输入任意 query，点 "开始研究" 按钮——页面渲染出 `plan` Markdown（含 ≥3 个章节，对应 `len(state["plan"])` 个 `PlanStep`）。

**Acceptance Scenarios**:

1. **Given** User 首次访问 `http://localhost:8501`（session_state 空），**When** 页面渲染，**Then** `session_state.thread_id` 自动生成 `str(uuid.uuid4())`（per ADR-0006 "Streamlit session_state 模式"），输入框为空，"开始研究" 按钮 enabled。
2. **Given** User 在输入框输入 query 并点 "开始研究"，**When** `app.py` 调 `run_research({"user_query": query}, thread_id=st.session_state.thread_id)`（`auto_confirm_plan=False`，per spec 002 FR-007 + Spec 003），**Then** `app.py` 检测到 `result["__interrupt__"]`（per LangGraph 1.2 `__interrupt__` 返回 dict 形态，per 项目记忆 [[langgraph-1.2-interrupt-dict-and-cmd-goto]]），渲染 plan 为 `st.markdown(plan_md)` 并显示 Approve / Submit 按钮。
3. **Given** User 在 plan review 页点 "Approve"（输入框留空），**When** `app.py` 调 `run_research(Command(resume="approve"), thread_id=st.session_state.thread_id)`，**Then** `app.py` **不**返回 `{"error": "no_pending_interrupt", ...}`，research + write + emit_final_report 节点正常推进。
4. **Given** plan review 页 User 想修改 plan，在文本框输入 "增加可观测性章节" 并点 "Submit"，**When** `app.py` 调 `run_research(Command(resume="edit: 增加可观测性章节"), thread_id=st.session_state.thread_id)`（per spec 002 FR-008 + Q1 决策），**Then** 图通过 `_after_confirm_plan` 条件边回到 `plan_node`（per graph.py `_after_confirm_plan`），重新生成 plan 并**再次**触发 `__interrupt__`，UI 渲染新 plan。

---

### User Story 2 - 跨进程 / 跨浏览器 tab 续跑同一 thread（P2）

作为 **User**，我能从 `Langfuse UI` 看到自己的 `thread_id`（Lesson 0007 实验会把 thread_id 写到 Langfuse trace metadata，spec 006 实装），把它粘贴到 Streamlit 侧栏的 "thread_id" 文本框、点 "Load thread"——证明 PostgresSaver 是**唯一**状态载体，UI 可以接管任何历史 thread_id 继续跑。

**Why this priority**: P2，spec 002 US4 + ADR-0005 跨进程 resume 的"前端化"。Lesson 0007 长时实验（interrupt 后人工下班、第二天再续）依赖这条；Streamlit 默认每个 tab 自动生成新 `uuid.uuid4()` 不利于跨 session 复用。

**Independent Test**: 浏览器 tab A 跑 plan → 触发 interrupt → tab A 关掉不点 Approve；浏览器 tab B（同一进程或不同进程）侧栏输入 tab A 的 thread_id 并点 "Load thread"——页面渲染 tab A 已生成的 plan（不重新 invoke `plan_node`）。

**Acceptance Scenarios**:

1. **Given** tab A 首次访问，`st.session_state.thread_id` 已自动生成，**When** tab A 跑 `run_research` 触发 interrupt，**Then** UI 显示 `st.session_state.thread_id` 在侧栏 "thread_id" 文本框（只读，方便 User 复制）。
2. **Given** tab B 首次访问，自动生成新 uuid，**When** User 把 tab A 的 thread_id 粘贴到侧栏 "thread_id" 文本框并点 "Load thread"，**Then** `app.py` 不调 `run_research`，而是仅把 `st.session_state.thread_id` 覆盖为粘贴值，并 `st.rerun()`——下次 "开始研究" 按钮触发 `Command(resume=...)` 时使用此 thread_id。
3. **Given** tab B 已 Load thread 成功（无报错），**When** User 在 tab B 点 "开始研究" 调 `run_research(Command(resume="approve"), thread_id=<粘贴值>)`，**Then** `_has_pending_interrupt(graph, config)` 返回 True（Postgres `checkpoints` 表存在该 thread_id），图继续跑 research + write + emit_final_report。

---

### User Story 3 - 进度显示（单段 spinner + 完成收尾）(P2)

作为 **User**，我在 `run_research()` invoke 期间能看到一个**单段 spinner**（"研究进行中..."）持续旋转，invoke 完成后切到 `"✅ 完成"` 字样——证明页面不卡死、浏览器 session 不超时。

**Why this priority**: P2，UX 必需。spec 005 不引入 LangGraph state streaming，但单段 spinner + 完成收尾已能消除 "页面卡死？网络挂了？" 焦虑（per Q4 澄清，2026-10-19：阻塞 invoke 模型下没有真实信号驱动 4 阶段切换）。Lesson 0007 实验 6+ 分钟跑完一轮 research loop，没进度反馈 User 会以为浏览器坏了。

**Independent Test**: 跑一个真实 query（含完整 plan → research → write → emit），浏览器在 invoke 期间显示单段 `st.spinner("研究进行中...")` 旋转；invoke 完成后 spinner 区切到 `st.empty().markdown("✅ 完成")`；最终 `final_report` Markdown 渲染覆盖 "✅ 完成" 字样。

**Acceptance Scenarios**:

1. **Given** User 点 "开始研究" 触发 invoke，**When** `_run_research_with_stages` 包装器用 `st.spinner("研究进行中...")` 包裹 invoke 调用，**Then** 页面显示一个旋转 spinner，**不**切换阶段文字（per Q4 澄清，2026-10-19：阻塞 invoke 模型下无真实信号驱动 4 阶段切换）。
2. **Given** invoke 完整跑通到 END，**When** `_run_research_with_stages` 包装器在 spinner 上下文退出后写 `st.empty().markdown("✅ 完成")`，**Then** UI 在 spinner 区显示 "✅ 完成" 字样一次（无重复切换）。
3. **Given** invoke 期间 raise 任何 `NotYetImplementedError` 或 `RuntimeError`，**When** `app.py` try/except 捕获，**Then** UI 显示 `st.error(f"❌ {error_type}: {error_msg}")` 红条，**不**让 Streamlit crash——User 可改输入重试或切 thread_id。

---

### User Story 4 - CLI 烟雾测试 auto_confirm_plan=True 单次跑通（P2）

作为 **Lesson 0007 实验脚本**，我能调 `python -m src.apps.deep_research.cli "..."` 一次 invoke 拿到 `final_report` Markdown 打印到 stdout，**不**卡在 stdin、不需要 Streamlit——证明 agent 端到端跑通、Lesson 0007 实验脚本可以直接接住 stdout 做评测。

**Why this priority**: P2，CI 烟雾测试 + Lesson 0007 评测入口。per ADR-0006 "CLI 保留，仅用于 CI 烟雾测试 + 不需要 UI 的 headless 环境"，CLI 是 spec 005 必交付物之一。**也是 spec 005 上线后 CI 第一次能跑真实 deep research 的 hook 点**。

**Independent Test**: 在 Postgres + Langfuse + DashScope 凭证齐备的环境下，跑 `python -m src.apps.deep_research.cli "为什么 LangGraph 1.2 用 interrupt() + Command(resume)?"`——stdout 输出含 `## References` 的合法 Markdown，exit code 0。

**Acceptance Scenarios**:

1. **Given** Postgres + Langfuse + DashScope 凭证齐备，**When** 开发者跑 `python -m src.apps.deep_research.cli "..."`，**Then** stdout 打印 `state["final_report"]` Markdown（含 `## References` 章节 + ≥5 个 `[ref_N]` 行内引用），exit code 0。
2. **Given** CLI 在 invoke 期间 raise 任何 exception，**When** `cli.py` 顶层 try/except 捕获，**Then** stdout 打印 `[ERROR] <exception type>: <message>` + 非 0 exit code——CI 可 grep 验证。
3. **Given** User 想给 CLI 实验指定 thread_id（用于跨 CLI 多次调用 share state），**When** 跑 `python -m src.apps.deep_research.cli "..." --thread-id=<tid>`，**Then** `run_research(...)` 用 `<tid>` 作为 thread_id（per spec 002 FR-012 + ADR-0005 "Postgres 是唯一状态载体"）。

---

### Edge Cases

- **Postgres 不可达**：`build_graph()` 抛 `psycopg.OperationalError` 连接错误——UI 显示 "Postgres 不可达，请检查 `langfuse-deploy/docker compose up -d postgres`"；CLI 打印错误信息 + exit 1。两条路径**不**静默 fallback 到 in-memory saver。
- **Langfuse 不可达**：`prompts.get_prompt(...)` 抛连接错误——`plan_node` 包成 `NotYetImplementedError`（per spec 002 NFR-004）；UI 显示 "Langfuse 不可达，请检查 `langfuse-deploy/docker compose up -d langfuse`"；CLI 同。
- **同 thread_id 并发**：`run_research` 返回 `{"error": "thread_id_busy", "thread_id": tid}`（per spec 002 FR-017）——UI 显示 "Thread 正在被另一个 session 占用，请稍后重试或换 thread"；CLI 打印 `[ERROR] thread_id_busy` + exit 2。
- **User 输入框为空 query 点 "开始研究"**：`app.py` 检测到 `not st.session_state.user_query.strip()` 时 `st.warning("请输入研究问题")` + **不**调 `run_research`。
- **User 在 plan review 时点 "Reset thread"**（侧栏按钮）：清空 `st.session_state.thread_id` + 重生成 `uuid.uuid4()` + `st.rerun()`——用于从错误状态恢复。
- **CLI 无 query 参数**：`python -m src.apps.deep_research.cli` 无位置参数 → CLI 打印 usage 提示 + exit 1。
- **Streamlit tab 重启后 `session_state` 丢失**：首次访问总是自动生成新 `uuid.uuid4()`；老的 thread_id 需要 User 主动从侧栏粘贴（per US2 流程）。**不**做 URL 参数持久化（避免引入 Streamlit query params dependency）。
- **invoke 期间浏览器 tab 关闭**：state 在 Postgres `checkpoints` 表，下次同 thread_id + `Command(resume="approve")` 可续（per spec 002 US4 + ADR-0005）。**不**做 client-side 持久化。

---

## Requirements *(mandatory)*

### Functional Requirements

#### Streamlit UI（核心：US1-US3）

- **FR-001**：`src/apps/deep_research/app.py` MUST 是可被 `streamlit run src/apps/deep_research/app.py` 直接启动的单页 Streamlit 脚本（per ADR-0006 + US1 acceptance scenario 1）。脚本顶部 import `streamlit as streamlit` + `from langgraph.types import Command` + `from src.apps.deep_research.graph import run_research, build_graph` + `from src.apps.deep_research.state import DeepResearchState`。
- **FR-002**：`app.py` MUST 在 script body 顶部（任何 widget 之前）初始化 `st.session_state.thread_id = str(uuid.uuid4())`（仅当 key 不存在时，避免 rerun 覆盖）。**不**接受外部传入 thread_id——侧栏 "thread_id" 文本框的 "Load thread" 按钮是另一路径（per US2）。
- **FR-003**：`app.py` MUST 在侧栏（`st.sidebar`）暴露 (a) `st.text_input("thread_id", value=st.session_state.thread_id, disabled=True)` 只读显示当前 thread_id；(b) `st.text_input("Load thread_id (paste from another session)")` 文本框 + `st.button("Load thread")` 按钮——按钮点击把 `st.session_state.thread_id = <粘贴值>` 并 `st.rerun()`；(c) `st.button("Reset thread (新 uuid)")` 按钮——按钮点击 `st.session_state.thread_id = str(uuid.uuid4())` 并 `st.rerun()`。
- **FR-004**：`app.py` MUST 暴露 `st.text_input("研究问题", key="user_query")` 输入框 + `st.button("开始研究", key="start_btn")` 按钮。按钮点击后：(a) 校验 `user_query.strip()` 非空（per Edge Cases "User 输入框为空 query 点开始研究"）；(b) 设置 `st.session_state.last_action = "start"`；(c) `st.rerun()`。
- **FR-005**：`app.py` MUST 在每次 rerun 顶部根据 `st.session_state.last_action` 分支：
  - `"start"`：调 `_run_research_with_stages(None, thread_id)`（首次 invoke `{"user_query": ...}`），清空 `last_action`；
  - `"approve"` 或 `"edit"`：调 `_run_research_with_stages(Command(resume=<payload>), thread_id)`，清空 `last_action`；
  - `None`：只渲染空 state 首页（首次访问）。
- **FR-006**：`app.py` MUST 检测 `result["__interrupt__"]`（per LangGraph 1.2 返回 dict 形态，per项目记忆 [[langgraph-1.2-interrupt-dict-and-cmd-goto]]）：`payload = result["__interrupt__"][0].value`；`plan = payload["plan"]`（Pydantic list of PlanStep）。`app.py` MUST 渲染 plan 为 Markdown：
    - 顶部 `st.markdown("## Plan 审阅")` + `st.caption("agent 已生成研究计划，请审阅后选择 Approve 或 Edit")`；
    - 中部 `st.markdown(format_plan_md(plan))` 其中 `format_plan_md(plan)` MUST 把每个 `PlanStep` 渲染为 `### {i}. {title}\n{description or ''}\n`（per US1 acceptance scenario 2）；
    - 底部 `st.text_area("反馈（留空 = approve；非空 = edit 重生成）", key="plan_feedback")` + `st.button("Approve", key="approve_btn")` + `st.button("Submit Edit", key="edit_btn")`。
- **FR-007**：plan review 页 "Approve" 按钮 MUST 设置 `st.session_state.last_action = "approve"` + `st.session_state.user_input = "approve"` 并 `st.rerun()`。"Submit Edit" 按钮 MUST 校验 `st.session_state.plan_feedback.strip()` 非空（per Edge Cases），设置 `st.session_state.last_action = "edit"` + `st.session_state.user_input = "edit: " + st.session_state.plan_feedback.strip()` 并 `st.rerun()`。
- **FR-008**：`app.py` MUST 在 `run_research` 正常返回（无 `__interrupt__`、无 `error` key）时渲染 `st.markdown(result["final_report"])` + `st.success("✅ 研究完成")` + 提供 `st.download_button("下载 Markdown", data=result["final_report"], file_name=f"{thread_id}.md", mime="text/markdown")` 按钮（per US1 acceptance scenario 4）。
- **FR-009**：`app.py` MUST 检测 `result.get("error")`：值为 `"no_pending_interrupt"` → `st.warning("⚠️ 该 thread 无 pending interrupt，可能已完成或不存在")`；值为 `"thread_id_busy"` → `st.warning("⚠️ Thread 正在被另一个 session 占用，请稍后重试")`；其他 → `st.error(f"❌ {result['error']}")`。`app.py` MUST **不**在 `result.get("error")` 时调 `st.markdown(result["final_report"])`——避免渲染错误 state。
- **FR-010**：`app.py` MUST 实现 `_run_research_with_stages(input_payload, thread_id) -> dict` 包装器：(a) 创建 `st.spinner("研究进行中...")` 上下文管理器包裹 invoke；(b) 调 `run_research(input_payload if input_payload else {"user_query": st.session_state.user_query}, thread_id=thread_id)` 实际跑图；(c) 检测到 `__interrupt__` → 不吞 spinner，让上层渲染 plan review；(d) invoke 完成后 single-shot 在 spinner 区或 `st.empty()` 里写 `st.markdown("✅ 完成")` 收尾，**仅一次**（per Q4 澄清，2026-10-19）——**不**做 sub-graph 内部 Plan → Research → Write 的虚假阶段切换（阻塞 invoke 模型下无真实信号触发）；**不**引入 sub-graph streaming hook（spec 006 Langfuse hook 范畴）。

#### CLI 烟雾测试（核心：US4）

- **FR-011**：`src/apps/deep_research/cli.py` MUST 是可被 `python -m src.apps.deep_research.cli "..." [--thread-id=<tid>]` 直接调用的模块（per ADR-0006 "CLI 保留，CI 烟雾测试用" + US4 acceptance scenario 1）。CLI MUST 在模块顶层 try/except 捕获所有 exception 并 sys.exit(<non-zero>)，避免 unhandled exception 留 shell 半天。
- **FR-012**：CLI 位置参数 MUST 是 user query 字符串（**必填**，无默认值；空字符串视作 400）；`--thread-id=<tid>` 是可选命名参数（不传则用 `str(uuid.uuid4())`）。CLI MUST 调 `run_research({"user_query": query, "auto_confirm_plan": True}, thread_id=tid)`（per Q3 决策 + spec 002 FR-011）。
- **FR-013**：CLI MUST 在 invoke 成功返回时 `print(result["final_report"])` 到 stdout（per Q3 决策；**不**写文件到磁盘）；invoke raise 或 `result.get("error")` 非空时按 exit-code 约定处理（per Q5 澄清，2026-10-19）：`thread_id_busy` → stderr 打印 `[ERROR] thread_id_busy: <thread_id>` + `sys.exit(2)`（retry-able，per POSIX 惯例 < 64 留给应用层特殊信号）；其他 error（Postgres / Langfuse / DashScope 等 fatal 错误）→ stderr 打印 `[ERROR] <error_key>: <message>` + `sys.exit(1)`。invoke 异常未捕获 → stderr 打印 `[ERROR] <ExceptionType>: <message>` + `sys.exit(1)`。CLI 错误约定 per US4 acceptance scenario 2 + Q5 决策。
- **FR-014**：CLI MUST 在启动时 `print(f"[cli] thread_id={tid}", file=sys.stderr)` + `print(f"[cli] query={query[:80]}", file=sys.stderr)` 到 stderr——CI grep stderr 可观察 invoke 入口（stdout 留给 final_report，避免污染 Markdown 输出）。如果 `--thread-id=<tid>` 显式传入且 Postgres `checkpoints` 表 `SELECT count(*) WHERE thread_id = :tid` > 0（per Q6 澄清，2026-10-19），CLI MUST 额外打印 `print(f"[cli] WARNING: thread_id={tid} already has N checkpoint(s); new run will create a fresh checkpoint and not inherit prior state", file=sys.stderr)`（N = 上面的查询结果），仍正常 exit 0——dev 可见"我在聚合 trace"，但不阻塞实验。

#### 依赖与配置（跨切关注）

- **FR-015**：`pyproject.toml` MUST 新增 `streamlit>=1.30.0` 依赖（per ADR-0006 "Streamlit 1.30+ 已能覆盖 80% UI 需求" + Constitution Constraint #8 "新依赖需 ADR + spec 增量"）。Streamlit **不**进 dev dependency group——它是 runtime 必需（不是测试依赖）。
- **FR-016**：`pyproject.toml` MUST 新增 `pytest>=8.0` + `pytest-cov>=5.0` + `pytest-asyncio>=0.23` 进 `[dependency-groups] dev`（per Constitution Principle III "pytest 在 spec 005 落地"）——spec 005 同时是项目**首次**引入 pytest 的 spec。**新增 dev dep 不需要新 ADR**——它已经隐含在 Constitution Principle III "CI 跑 pytest --cov-fail-under=80" + Constraint #8 "pytest 在 spec 005 加入"的规划里。
- **FR-017**：`app.py` + `cli.py` MUST **不**修改 `src/apps/deep_research/{graph.py, state.py, schemas.py, prompts.py, tools.py, nodes/}` 任何现有文件（per Constitution Principle I "MUST NOT introduce new top-level state fields" + Principle V Simplicity "do not pre-wire hooks for un-specced features"）。UI / CLI 只是 `run_research()` 的 client，无 graph 拓扑修改。
- **FR-018**：`app.py` + `cli.py` MUST **不**新增 env var（per Constraint #7 "Env-var hygiene"）——所有现有 env var（`LANGGRAPH_CHECKPOINT_DB` / `LANGFUSE_*` / `DASHSCOPE_*` / `USE_RERANK` / `USE_WEB_SEARCH_APP`）不变。
- **FR-019**：`src/apps/deep_research/README.md` MUST 新增 `## spec 005 增量（Streamlit UI + CLI 烟雾测试）` 章节（per Constraint #6 spec-conformance + ADR-0006），列出 `app.py` / `cli.py` 入口命令 + 新增 `streamlit` 依赖 + pytest 引入。
- **FR-020**：spec 005 **不**引入 sub-graph 内部进度流式推送（per Q2 决策）——仅在 stage 包装器层做 4 段 placeholder。**不**做 SSE / websocket / state push。**不**做 Langfuse trace 写入。**不**做 Plan 自动评分。

#### 测试

- **FR-021**：spec 005 MUST 新增 `tests/apps/deep_research/test_app_cli.py`（pytest，per Constitution Principle III "tests written FIRST"）：
  - TC-1: `app.py` 的 `_run_research_with_stages` 包装器对 `result["__interrupt__"]` 的分支返回 True，UI 不调 `st.markdown(result["final_report"])`——用 monkeypatch 替换 `st.empty` / `st.spinner` + `run_research` mock 返回 `{"__interrupt__": [(...,{"plan": [PlanStep(title="x")]})]}`,
  - TC-2: `app.py` 的 `_run_research_with_stages` 包装器对正常 final_report 返回的分支——mock 返回 `{"final_report": "## References\n..."}`，
  - TC-3: `app.py` 的 error 分支（`no_pending_interrupt` / `thread_id_busy`）——mock 对应两种错误返回，
  - TC-4: `cli.py` 调 `run_research` 单次 + print final_report 到 stdout（用 capsys 验证），
  - TC-5: `cli.py` invoke raise 时 `sys.exit(1)` + stderr 打印 `[ERROR]`，
  - TC-6: `cli.py` 无位置参数时 `sys.exit(1)` + stderr 打印 usage。
  - TC-7: `cli.py` 收到 `{"error": "thread_id_busy", "thread_id": tid}` 时 `sys.exit(2)` + stderr 打印 `[ERROR] thread_id_busy`（per Q5 澄清，2026-10-19 退出码约定）。
  - TC-8: `cli.py --thread-id=<existing_tid>`（该 tid 已有 Postgres `checkpoints` 行）启动时 stderr 打印 `[cli] WARNING: thread_id=<tid> already has N checkpoint(s); ...`（per Q6 澄清，2026-10-19），仍正常 exit 0。
- **FR-022**：`pytest --cov=src/apps/deep_research/app.py --cov=src/apps/deep_research/cli.py --cov-branch --cov-fail-under=80` MUST 在 spec 005 PR 上 exit 0（per Constitution Principle III coverage gate）。`app.py` / `cli.py` 的 `st.*` / `print` / `sys.exit` 调用 MUST 通过 monkeypatch 隔离——Streamlit 没有 headless 单测 fixture，社区惯例是 monkeypatch。

---

### Key Entities *(include if feature involves data)*

本 spec **不**新增任何 Pydantic 实体 / state 字段——它纯 UI 层 / 入口层。引用的实体（spec 001 / 002 / 004 已定义）：

- **PlanStep**（已在 `schemas.py`）：plan review 页 `format_plan_md(plan)` 渲染每个 step 为 `### {i}. {title}\n{description}`。
- **UserQuery**（已在 `schemas.py`）：作为 `state["user_query"]: str` 传入 `run_research`，UI 层仅传 raw string。
- **FinalReport**（已在 `schemas.py`）：CLI / Streamlit 完成后渲染的 Markdown 字符串，含 `## References` + `[ref_N]` 引用。
- **DeepResearchState**（已在 `state.py`）：UI 层**只读**以下字段：`plan`（interrupt payload 渲染用）/ `final_report`（完成后渲染用）/ `messages`（_after_confirm_plan 决策读最新 HumanMessage）。**不**修改 state schema。

### Non-Functional Requirements

- **NFR-001**：`app.py` 单次 Streamlit rerun P50 wall-clock latency < 200ms（除 `run_research` 调用本身；per ADR-0006 "Streamlit 每次 rerun 整个脚本重跑——需要小心 session_state 初始化"）。`session_state` 初始化 MUST 集中在 script body 顶部，避免每次 rerun 重新生成 thread_id。
- **NFR-002**：CLI P50 wall-clock latency 不应超过 research + write 全流程 P50 latency（spec 003 / 004 NFR-001）+ 5s overhead（CLI 自身的 print + argparse + import overhead）。
- **NFR-003**：`app.py` MUST NOT 在 invoke 期间阻塞主线程超过 30s 不放 spinner context——`st.spinner` 必须包裹 `run_research` 调用。否则浏览器会显示 "页面无响应"。
- **NFR-004**：`app.py` + `cli.py` MUST NOT 修改 `src/apps/deep_research/graph.py` / `state.py` / `schemas.py` / `prompts.py` / `tools.py` / `nodes/`（per FR-017 + Constitution Principle I "spec-compatible")——UI 层改图内部是 spec 006 / future spec 的工作。
- **NFR-005**：UI 层 MUST NOT 在 prompt / state schema / reducer / PostgresSaver 配置上**任何**额外约束（per spec 001-004 已 frozen 约束）。新增 UI 行为（如进度条、下载按钮）必须通过 `run_research()` 已暴露的接口实现。

---

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**：dev 启动 `streamlit run src/apps/deep_research/app.py`，浏览器访问 `http://localhost:8501`，输入任意 query（如 "为什么 LangGraph 1.2 用 interrupt + Command(resume)"）点 "开始研究"——页面渲染 plan markdown（≥ 3 个 `### N. <title>` 子章节，对应 `len(state["plan"])` 个 `PlanStep`）+"Approve" + "Submit Edit" 按钮 + 反馈文本框。
- **SC-002**：在 SC-001 plan review 页输入框留空，点 "Approve"——按钮触发 `Command(resume="approve")` 续跑；UI 在 invoke 期间显示单段 `st.spinner("研究进行中...")` 旋转（per Q4 澄清，2026-10-19，**不**做 4 阶段切），invoke 完成后 spinner 区 single-shot `st.markdown("✅ 完成")`；最终页面渲染 `st.markdown(result["final_report"])` 含 `## References` 章节 + ≥ 5 个 `[ref_N]` 行内引用 + "下载 Markdown" 按钮（点击下载 `<thread_id>.md`）。
- **SC-003**：跨浏览器 tab 验证——tab A 跑 SC-001 触发 interrupt，**不**点 Approve 直接关掉；tab B（同进程）侧栏粘贴 tab A 的 `thread_id` + 点 "Load thread" + 点 "开始研究" 触发 `Command(resume="approve")`——tab B 不返回 `{"error": "no_pending_interrupt", ...}`，正常推进到 research + write + emit_final_report。Postgres `checkpoints` 表 `SELECT count(*) FROM checkpoints WHERE thread_id = '<tab_a_tid>'` ≥ 2。
- **SC-004**：CLI 烟雾测试——dev 跑 `python -m src/apps/deep_research.cli "为什么 LangGraph 1.2 用 interrupt + Command(resume)?"`——输出 ≥ 1000 字 Markdown（含 `## References` 章节 + ≥ 5 个 `[ref_N]` 行内引用 + 至少 1 个 outline h2 章节对应 plan step），exit code 0。stderr 含 `[cli] thread_id=<uuid>` 行。
- **SC-005**：CLI 错误路径——临时把 `LANGGRAPH_CHECKPOINT_DB` 指向不可达 Postgres（如 `postgresql://wrong:wrong@localhost:1/wrong`）后跑 CLI——stderr 打印 `[ERROR] <ExceptionType>: <message>` + exit code 1；stdout **不**含 "## References"。`thread_id_busy` 错误路径——脚本 A 持有 advisory lock 期间跑脚本 B（独立进程，同 `thread_id`，per spec 002 SC-008 模式）——脚本 B stderr 打印 `[ERROR] thread_id_busy: <tid>` + exit code 2（per Q5 澄清，2026-10-19 退出码约定）；脚本 A 完成后脚本 B 再跑一次能正常通过（exit 0）。
- **SC-006**：CLI `--thread-id` 参数——`python -m src.apps.deep_research.cli "..." --thread-id=cli-cross-001` 跑成功后，`python -m src.apps.deep_research.cli "..." --thread-id=cli-cross-002`（不同 thread_id）跑**新一次** invoke；Postgres `checkpoints` 表存在两 thread_id 各自 ≥ 1 的 run。
- **SC-007**：测试覆盖率——`pytest --cov=src/apps/deep_research/app.py --cov=src/apps/deep_research/cli.py --cov-branch --cov-fail-under=80 tests/apps/deep_research/test_app_cli.py` exit 0；`app.py` 行覆盖 ≥ 80%（除 `if __name__ == "__main__"` + Streamlit `st.*` 单测无法触发的 import-time 副作用外）；`cli.py` 行覆盖 ≥ 90%（CLI 是 deterministic Python，无 Streamlit monkeypatch 难度）。
- **SC-008**：依赖完整性——`pip install -e .` 在干净 venv 内 install `streamlit>=1.30.0` + `pytest>=8.0` + `pytest-cov>=5.0` + `pytest-asyncio>=0.23` 不报错；`pytest --version` + `streamlit --version` 都可执行。
- **SC-009**：README 更新——`src/apps/deep_research/README.md` 新增 "## spec 005 增量（Streamlit UI + CLI 烟雾测试）" 章节（含 `streamlit run src/apps/deep_research/app.py` + `python -m src.apps.deep_research.cli` 入口命令 + 新依赖列表 + 测试运行命令），与代码实现 1:1 对齐（per Constraint #6 spec-conformance）。

---

## Assumptions

- **Postgres** 在 `localhost:5432` 可达（来自 `langfuse-deploy/docker-compose.yml`），per spec 001 / 002 / 003 / 004 已验证。
- **Langfuse** 在 `http://localhost:3000` 可达，4 个 prompt 已创建（`deep-research-plan` / `deep-research-gen-query-v2` / `deep-research-judge` / `deep-research-outline` / `deep-research-draft` / `deep-research-critic` / `deep-research-polish`，per spec 002 / 003 / 004）。
- **DashScope** 凭证齐备（`DASHSCOPE_API_KEY` / `DASHSCOPE_WEB_SEARCH_APP_ID` / `DASHSCOPE_WEB_SEARCH_APP_TOKEN`），per spec 002 / 003。
- **User** 是单用户本地用 cai_he 自己——Streamlit 不做 auth / multi-tenant（per ADR-0006 "Streamlit session_state 与 thread_id 1:1 对应" + "Consequences: 多用户 / 鉴权需要额外方案（当前单用户本地用，不考虑）"）。
- **Streamlit** 部署模式是本地 `streamlit run`（不是 Streamlit Cloud / Docker），per ADR-0006 "未来触发条件：若需要多用户协作 / 远程访问，再评估 FastAPI"。
- **Streamlit 版本** 锁定 `>=1.30.0,<2.0`——1.30 是 ADR-0006 决策时刻的稳定版本；与 `langchain-openai>=1.6.2` / `langgraph>=1.2.11` 无冲突。
- **pytest** 锁定 `>=8.0,<9.0`——与 Constitution Principle III "CI 跑 pytest --cov-fail-under=80" 一致；`pytest-asyncio>=0.23` 兼容 spec 003 之后的 async node。
- **UI 不做 iteration 数实时显示**（per Q2 决策）——iteration 数实时推送属于 spec 006 Langfuse hooks 范畴。
- **CLI 不写文件到磁盘**（per Q3 决策）——文件落盘是 Lesson 0007 实验脚本的职责，CLI 只 print 到 stdout。
- **CLI 与 Streamlit 共享同一 `run_research()` 入口**——CLI **不**绕开 PostgresSaver 跑 in-memory mode（per Constraint #5 "Postgres is the only state source"）；CLI 也要 `langfuse-deploy/postgres` 可达。
- **spec 005 不写 lesson-0007 实验脚本本身**——CLI 是 spec 005 交付物；Lesson 0007 用 CLI 跑 1000 个 query 是后续 spec / 后续工作。

---

## Out of Scope

- Langfuse `@observe` hook + TraceMetadata + `thread_id` 写入 Langfuse trace（spec 006）
- 多用户 / auth / remote 访问（per ADR-0006 "未来触发条件：若需要多用户协作 / 远程访问"）
- 流式输出（plan / research / write 节点 LLM 增量输出；per CONTEXT.md §8）
- Plan 编辑可视化（per-step 文本框；per Q1 决策走整段反馈文本模式）
- Iteration 数实时推送 / state streaming（per Q2 决策 + spec 006 Langfuse hooks 范畴）
- File upload / DB / GraphQL 后端（per ADR-0006 "Streamlit 已覆盖 80% 需求"）
- 实时协作（多人同时编辑同一研究；per CONTEXT.md §8）
- 多语言 UI 切换（per CONTEXT.md §8 "多语言报告（当前默认中文输出，但未做语言切换机制）"）
- Lesson 0007 实验脚本（用 CLI 跑 N 个 query + 评测；后续 spec / 后续工作）
- Streamlit 1.30 新特性（如 `st.dialog` / `st.fragment`）——本 spec 用 1.30 基础 API（`st.text_input` / `st.button` / `st.markdown` / `st.spinner` / `st.empty`），保证跨 Streamlit minor 版本兼容
- Streamlit Cloud 部署 / Docker 镜像（per ADR-0006 "未来触发条件"）
- `_run_research_with_stages` 之外的 invoke 包装器（如把 progress 写 Langfuse `thread_metadata`；spec 006）

---

## Dependencies

- spec 001（foundation）：包骨架、schemas、state、`run_research()`、`_has_pending_interrupt()`、`build_graph()`、`_after_confirm_plan()` 全部继承。
- spec 002（plan-confirm）：`run_research(Command(resume=...))` 接口、`auto_confirm_plan` 旁路、`_acquire_thread_lock`、`thread_id_busy` 错误返回、`__interrupt__` 返回 dict 形态（per项目记忆 [[langgraph-1.2-interrupt-dict-and-cmd-goto]]）。
- spec 003（research-loop）：`state["sources"]` 在 invoke 完成时已 ReRank 重排 + 写入；UI 不需要单独处理。
- spec 004（write-loop）：`state["final_report"]` 在 emit_final_report 后写入；UI 直接 `st.markdown(result["final_report"])` 渲染。
- ADR-0003（interrupt for plan-confirm）：`interrupt()` + `Command(resume=...)` 模式；UI 跟随 `__interrupt__` payload 渲染。
- ADR-0005（Postgres checkpointer）：跨进程 / 跨 tab resume 的物理基础；UI 的 `Load thread` 按钮依赖。
- ADR-0006（Streamlit UI entry）：Streamlit 单页 + session_state 模式 + CLI 烟雾测试 scope。
- 项目记忆 [[langgraph-1.2-interrupt-dict-and-cmd-goto]]：UI 检测 `result["__interrupt__"]` 而非 `GraphInterrupt` 异常。
- 项目记忆 [[langgraph-1.2-send-fanin-reducer-multicall]] / [stage orders] / [LangGraph thread_id_busy]：CLI / UI 错误处理与 SC 验证依赖。
- `langfuse-deploy` Postgres（`localhost:5432`）：UI 跨 tab resume + CLI 烟雾测试的物理基础。
- `langfuse-deploy` Langfuse（`localhost:3000`）：UI / CLI 跑通的 Langfuse prompt 来源。
- LangGraph 1.2.11+（已在 `pyproject.toml` 锁定）：`__interrupt__` dict 形态 + `Command(resume=...)` 接口。
- Streamlit 1.30+（spec 005 新增依赖，per FR-015）：单页 UI 框架。
- pytest 8.0+ + pytest-cov 5.0+ + pytest-asyncio 0.23+（spec 005 新增 dev dep，per FR-016 + Constitution Principle III）。

---

## Notes

- spec 005 是 deep_research agent "从 Cloud 到可消费产品"的最后一步——前 4 个 spec 把"图能跑出 final_report"做完，spec 005 把"图能用 Streamlit 跑 / CLI 跑"做完。spec 006 才是可观测性层（Langfuse hooks）。**不**是 spec 005 把 spec 006 也做完（YAGNI per Constitution Principle V Simplicity）。
- **FR-010 的 `_run_research_with_stages` 包装器**是 spec 005 唯一新增的 `run_research` 周边逻辑——单段 `st.spinner("研究进行中...")` + invoke 完成后 single-shot `st.empty().markdown("✅ 完成")` 收尾（per Q4 澄清，2026-10-19）。**不**改 `run_research` 签名（per FR-017）。它存在意义是让 Streamlit 不在 invoke 期间显示 "页面无响应"——属于 UI 层 UX 而非生产逻辑。
- **FR-008 的下载按钮** 用 `st.download_button` + `file_name=f"{thread_id}.md"`——文件名带 thread_id 便于 User 区分多轮 run 产物；这是 Lesson 0007 实验产物落盘的最小入口。spec 005 不直接做 Lesson 0007，但留下载钩子。
- **FR-009 的 error 分支处理** 是 spec 005 的"防御性 UX"——前 4 个 spec 的 `{"error": ...}` 返回结构化错误，spec 005 在 UI 层把它们转成 user-friendly 文案。Lesson 0007 跑实验时，CI grep 这些 error 字符串可触发重试。
- **FR-011 的 CLI smoke test** 是项目**首次**有 headless 端到端 smoke test——`pytest` 跑 `test_app_cli.py::test_cli_smoke_e2e` 可触发 CLI 真实 invoke 跑通完整 pipeline（per US4）。这是 CI 第一个真正"全链路 pass/fail 信号"的 hook 点。
- **Q1 决策的"整段反馈文本"plan 编辑模式** 是 spec 002 `Command(resume="edit: ...")` 的最自然 UI 映射——User 不需要理解 PlanStep schema，输入 "我想加可观测性章节" 即可触发 plan_node 二次重生成（spec 002 FR-008）。Per-step 编辑器会让 UI 过度耦合 PlanStep schema，未来 schema 演化 UI 也要改。
- **Q2 决策的"4 阶段 placeholder + 不显示 iteration 数"** 是 Constitution Principle V Simplicity + YAGNI 的体现——iteration 数实时推送需要 spec 006 Langfuse hooks 介入（langfuse.update_current_span + streamlit websocket），spec 005 提前引入会 over-engineer。Lesson 0007 跑 6+ 分钟 research loop 时，User 看到 "Research 中..." spinner 旋转已是足够反馈。
- **Q3 决策的"CLI auto_confirm_plan=True 必交付"** 是 ADR-0006 + spec 002 FR-011 "Lesson 0.7 实验用 auto_confirm_plan 旁路" 的兑现——CLI 跑 Lesson 0007 不需要人工 terminate interrupt。
- **Constitution Principle III coverage gate 在 spec 005 首次落地**——spec 003 / 004 README 提到的 "TDD enforced in CI" 到 spec 005 才真正引入 pytest。这是 Constitution v1.0.0 的规划意图，spec 005 兑现。
- **新增 streamlit + pytest 是 spec 005 唯一的 dependency change**（per FR-015 + FR-016）——这是 Constitution Constraint #8 "新依赖需 ADR" 的兑现：本 spec 即是 ADR（specs 是 acceptance gate 到 1.0+ 级别）。spec 005 在 pyproject.toml 新增 streamlit + 3 个 pytest dev deps，**不**新增 / 重命名任何 env var（per FR-018）。
- **Constitution Principle IV "Integration Testing at Module Boundaries"** 同样适用——`app.py` 调 `run_research()` 是 cross-module 边界；`cli.py` 调 `run_research()` 也是。`test_app_cli.py` TC-1 / TC-3 用 monkeypatch 替换 `run_research` mock + assert result dict shape（per spec 001-004 节点 contracts §C3 模式）。