---
description: "Task list for spec 002 — Plan + Confirm 阶段（真实 LLM + interrupt）"
---

# Tasks: Plan 与 Confirm 阶段（真实 LLM + interrupt）

**Input**: Design documents from `/specs/002-deep-research-plan-confirm/`
- `spec.md`（用户故事 + FR + SC）
- `plan.md`（技术栈 + 实现路径）
- `research.md`（10 个 R 决策）
- `data-model.md`（实体 + State transitions）
- `contracts/README.md`（7 个 Python API 契约）
- `quickstart.md`（9 个 SC 验证脚本）

**Prerequisites**: spec 001 foundation 已完成（`schemas.py` / `state.py` / `graph.py` 拓扑 / PostgresSaver / `auto_confirm_plan` 字段）

**Tests**: spec 002 用 `quickstart.md` 的 9 个 SC 脚本作为独立测试；不引入 pytest 框架（spec 003/004 引入）。

**Organization**: 按 4 个 User Story 组织（P1 × 2 + P2 × 2），每个故事可独立实现 + 验证。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 可并行（不同文件，无依赖）
- **[Story]**: 任务所属 user story（US1 / US2 / US3 / US4）
- 路径说明：所有 `src/apps/deep_research/...` 路径基于 spec 002 plan.md §Project Structure

---

## Phase 1: Setup（环境前置）

**Purpose**: spec 002 实现前的外部依赖就位

- [x] T001 验证 Postgres（`langfuse-deploy`）可达：`docker compose -f langfuse-deploy/docker-compose.yml ps | grep postgres` 显示 healthy；`psql $LANGGRAPH_CHECKPOINT_DB -c 'SELECT 1'` 返回 1
- [x] T002 [P] 验证 Langfuse 服务可达：浏览器打开 `http://localhost:3000`，确认登录页正常 — 实际为云端 `https://langfuse.bananahochoy.online`，HTTP 200
- [x] T003 [P] 验证 DashScope API key：`.env` 含 `DASHSCOPE_API_KEY=<真实 key>`；`curl -H "Authorization: Bearer $DASHSCOPE_API_KEY" https://dashscope.aliyuncs.com/compatible-mode/v1/models` 返回 200 — **PASS**：用户填充 4 个 DashScope env vars（API_KEY / API_BASE_URL / WEB_SEARCH_APP_ID / WEB_SEARCH_APP_TOKEN）。真实 key 指向阿里云**北京 region MaaS 网关**（`token-plan.cn-beijing.maas.aliyuncs.com`，非公开 DashScope），`/models` 端点返回 17 个模型（qwen3.x / glm-5.x / deepseek-v4.x）。为兼容 MaaS 与公开 DashScope 两套环境，在 `prompts.py` 增加 2 个可选 env：`DASHSCOPE_API_BASE_URL`（默认公开兼容端点）、`DASHSCOPE_MODEL`（默认 `qwen-plus-2025-04-28`，实测配 `DASHSCOPE_MODEL=qwen3.7-plus` 调用成功）
- [x] T004 在 Langfuse UI 上手动创建 prompt `deep-research-plan`（type=text, label=production）：内容必须含 `{{user_query}}` / `{{max_steps}}` / `{{#if user_feedback}}...{{/if}}` 三个 Mustache 占位符（详细模板见 `contracts/README.md §C6`）— 实际通过 Langfuse Python SDK 创建 v2；Mustache 段改为 chevron 兼容语法 `{{#user_feedback}}...{{/user_feedback}}`（Langfuse 4.x `text.compile()` 不解析 `{{#if}}`）

**Checkpoint**: Postgres + Langfuse + DashScope 三方就位，prompt 模板存在；可进入 Phase 2。

---

## Phase 2: Foundational（核心基础设施）

**Purpose**: 所有 user story 都依赖的底层实装——`prompts.py` 与 `graph.py` 的核心改造。完成后才能进入任一 US。

**⚠️ CRITICAL**: 4 个 user story 全部依赖本 phase；未完成时禁止并行启动任何 US。

- [x] T005 重写 `src/apps/deep_research/prompts.py::get_prompt(name: str, label: str | None = None)`：用 `from langfuse import get_client; lf = get_client()` 拿 client；调 `lf.get_prompt(name, label=label)` 返回 PromptClient（带 `.compile(**kwargs)` 方法）；`PromptNotFoundError` / `LangfuseError` 透传**不包成 NotYetImplementedError**；env var `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `LANGFUSE_HOST` 缺失时抛 `RuntimeError`（per FR-004 + contracts §C1）
- [x] T006 重写 `src/apps/deep_research/prompts.py::get_llm(role: Literal["plan", "research", "write", "critic", "polish"])`：role="plan" 实装 `langchain_openai.ChatOpenAI(model="qwen-plus-2025-04-28", api_key=os.environ["DASHSCOPE_API_KEY"], base_url="https://dashscope.aliyuncs.com/compatible-mode/v1", temperature=0.4)`；其他 role 仍抛 `NotYetImplementedError`；env `DASHSCOPE_API_KEY` 缺失时抛 `RuntimeError("DASHSCOPE_API_KEY not set")`（per FR-005 + contracts §C2）
- [x] T007 在 `src/apps/deep_research/graph.py` 新增 `_acquire_thread_lock(tid: str) -> bool` helper：连 Postgres 用 `psycopg.connect(os.environ["LANGGRAPH_CHECKPOINT_DB"])`（同一 URI 复用 foundation）；执行 `SET LOCAL lock_timeout = '1s'; SELECT pg_advisory_xact_lock(hashtext(%s))`；捕获 `psycopg.errors.LockNotAvailable` 返回 False；事务结束（`with` 块退出）自动释放锁（per FR-017 + research §R4）
- [x] T008 在 `src/apps/deep_research/graph.py::run_research` 入口加 advisory lock 调用：invoke 与 resume 两路径**进入** `_has_pending_interrupt` / `graph.invoke` 之前都先调 `_acquire_thread_lock(tid)`；锁被占时立即返回 `{"error": "thread_id_busy", "thread_id": tid}`，**不**调 `graph.invoke`；锁成功获取后**不显式释放**（Postgres 事务结束自动释放，per FR-017）

**Checkpoint**: foundation 完成；可并行启动 US1 / US2 / US3 / US4。

---

## Phase 3: User Story 1 - 真实 LLM 生成结构化 Plan（Priority: P1）🎯 MVP

**Goal**: `plan_node` 从 stub 改为真实 LLM 调用，产出 3-7 条 `PlanStep` 写入 `state["plan"]`

**Independent Test**: `run_research({'user_query': '...', 'auto_confirm_plan': True}, thread_id='sc-001')` 返回的 state 中 `len(state["plan"]) >= 3 and type(state["plan"][0]) is PlanStep`；跑 `quickstart.md` SC-001 脚本通过

### Implementation for User Story 1

- [x] T009 [US1] 改写 `src/apps/deep_research/nodes/plan.py::plan_node(state)`：调 `from src.apps.deep_research.prompts import get_prompt, get_llm`；`prompt = get_prompt("deep-research-plan", label="production")`；`llm = get_llm("plan")`；`compiled = prompt.compile(user_query=state["user_query"], max_steps=7, user_feedback=None)`；拼成 `list[dict]` 含 system + user message（per FR-001 + research §R1 + contracts §C3）
- [x] T010 [US1] 在 `nodes/plan.py::plan_node` 内加 `_invoke_with_retry(llm, messages, max_retries=2, base=1.0)` helper：捕获 HTTP 429 / 500 / 502 / 503 走指数退避（base=1s, exp=2x → 1s + 2s）；其他 HTTP 错误（400 / 401 / 403）不重试立即抛；retry 计数 `print(f"[retry] plan_retry_count={N}")` 占位（per FR-006(d)(e) + research §R5）
- [x] T011 [US1] 在 `nodes/plan.py::plan_node` 内加 Pydantic validation：解析 LLM 返回的 JSON 为 `list[dict]`；用 `PlanStep(**s)` 逐条构造；`Plan(steps=[...])` 整体校验；`len(steps)` 超出 `[3, 7]` 时抛 `NotYetImplementedError("plan steps exceed max_steps=7")` 或 `"below min_steps=3"`；返回 `{"plan": steps}`（per FR-002 + FR-006(c) + contracts §C3）
- [x] T012 [US1] 在 `nodes/plan.py::plan_node` 内加重试路径：JSON 解析失败 + LLM 返回空 list `len(parsed) == 0` 时，不带 backoff 立即重试 1 次（重用 `messages` 不变）；仍失败抛 `NotYetImplementedError("plan generation failed: <reason>")`（per FR-006(a)(b) + Edge Case "LLM 返回空 Plan"）

**Checkpoint**: US1 完成；`auto_confirm_plan=True` 单次 invoke 能产出 ≥3 条 PlanStep。

---

## Phase 4: User Story 2 - Interrupt + Resume（Priority: P1）

**Goal**: `confirm_plan_node` 调 `interrupt({"plan": [...]})` 暂停图；`Command(resume="approve")` 通过；`Command(resume="edit: ...")` 回 plan_node 重生成

**Independent Test**: `run_research({'user_query': '...'}, thread_id='sc-002')` 首次 invoke 抛 `GraphInterrupt`；同 thread_id + `Command(resume='approve')` 顺利推进到 research 入口；同 thread_id + `Command(resume='edit: 增加章节')` 触发 plan_node 第二次执行；跑 `quickstart.md` SC-002 / SC-003 / SC-009 脚本全部通过

### Implementation for User Story 2

- [x] T013 [US2] 改写 `src/apps/deep_research/nodes/plan.py::confirm_plan_node(state)`：`auto_confirm_plan=True` 直接返回 `{}`（FR-009）；否则构造 `payload = {"plan": [s.model_dump(mode="json") for s in state["plan"]]}`；调 `interrupt(payload)`（payload 必须是 dict 不能直接传 list；per FR-007 + research §R6）
- [x] T014 [US2] 在 `confirm_plan_node` 内加 `"edit:"` 分支处理 `interrupt()` 返回值：`if isinstance(user_decision, str) and user_decision.startswith("edit:") and len(user_decision) > 5:` 返回 `Command(goto="plan", update={"messages": state["messages"] + [HumanMessage(content=user_decision)]})`；`user_decision == "approve"` 或其他值（含非 str）→ 返回 `{}`（per FR-008 + Edge Case "unknown resume payload" + contracts §C4）
- [x] T015 [US2] 在 `nodes/plan.py::plan_node` 内加 `_extract_user_feedback(state)` helper：从 `state["messages"]` 取最后一条 HumanMessage；若 `content` 以 `"edit:"` 开头则 `return content[5:].strip()`；否则返回 `None`；供 plan_node 二次执行时使用（per FR-003 + contracts §C3）
- [x] T016 [US2] 在 `plan_node` 调 `prompt.compile(...)` 前用 `_extract_user_feedback(state)` 计算 `user_feedback` 参数：首次执行 `user_feedback=None`；edit 重生成时 `user_feedback="<提取文本>"`（per FR-003 + Q8 clarification）

**Checkpoint**: US2 完成；3 个 resume 路径（approve / edit / 其他）全部正确；plan 二次生成能注入 user_feedback。

---

## Phase 5: User Story 3 - Auto-Confirm 旁路（Priority: P2）

**Goal**: `auto_confirm_plan=True` 时 `confirm_plan_node` 跳过 interrupt，单次 invoke 走完 plan → confirm_plan

**Independent Test**: `run_research({'user_query': '...', 'auto_confirm_plan': True}, thread_id='sc-005')` 单次 invoke 不抛 `GraphInterrupt`（可能抛 `NotYetImplementedError` 因为 research stub，但 lock 与 confirm 已完整通过）；跑 `quickstart.md` SC-005 脚本通过

### Implementation for User Story 3

- [x] T017 [US3] 验证 `src/apps/deep_research/graph.py::run_research` 在 initial invoke 路径把 `input.get("auto_confirm_plan", False)` 写入 `state["auto_confirm_plan"]`（继承 foundation FR-008，spec 002 不重写——本任务仅**确认** behavior 仍生效，per FR-010 + FR-011）— 已确认（foundation 行为保留；T008 改写未触及 initial invoke 的 state 初始化）
- [x] T018 [US3] 验证 `nodes/plan.py::confirm_plan_node` 的 `if state.get("auto_confirm_plan", False): return {}` 短路逻辑（foundation 已实装，spec 002 不重写——本任务仅**确认** behavior 在真实 LLM 链路下仍生效，per FR-009）— 已验证：Test 1（`auto_confirm=True` → `{}`）

**Checkpoint**: US3 完成；Lesson 0007 实验链路 `auto_confirm_plan=True` 单 invoke 走通。

---

## Phase 6: User Story 4 - PostgresSaver 跨进程 Resume（Priority: P2）

**Goal**: 用同 `thread_id` 关掉进程重连能续上；同 `thread_id` 并发互斥（advisory lock）

**Independent Test**: 脚本 A 触发 interrupt 后 `sys.exit(0)`；脚本 B 独立进程用同 thread_id 调 `Command(resume='approve')` 不报 `no_pending_interrupt`；脚本 A 持锁期间脚本 B 1s 内拿 `thread_id_busy`；跑 `quickstart.md` SC-004 / SC-008 脚本通过

### Implementation for User Story 4

- [x] T019 [US4] 验证 `graph.py::run_research` 的 `Command(resume=...)` 路径仍先调 `_has_pending_interrupt(g, config)` 检查：无 pending interrupt 时返回 `{"error": "no_pending_interrupt", "thread_id": tid}`（**不**调 `graph.invoke`，per FR-012）— 已验证：`Command(resume='approve')` on empty thread → `{"error": "no_pending_interrupt", "thread_id": tid}`
- [x] T020 [US4] 在 `run_research` 的 Command(resume=...) 路径前调 `_acquire_thread_lock(tid)`（Phase 2 T007 引入）；锁失败返回 `thread_id_busy`；锁成功后**不**显式释放（per FR-017 + research §R4）— 已验证：concurrent contention 1.03s 返回 `thread_id_busy`（< 1.5s 阈值）
- [x] T021 [US4] 验证 `run_research` 每次调用都新建 `build_graph()` 实例（不缓存跨调用的 compiled graph）：进程 B `import` 后第一次调 `run_research` 必须触发新 `build_graph()` + 新 `PostgresSaver` 实例（per FR-014）— 已确认：T008 改写后，`run_research` 的 `_initial` / `_resume` 闭包每次都调 `build_graph()`

**Checkpoint**: US4 完成；跨进程 resume 链路打通，并发互斥保护到位。

---

## Phase 7: Cross-Cutting Concerns

**Purpose**: 跨 user story 的横向验证（错误处理 + 源码合规 + 文档同步）

- [x] T022 [P] 跑 `quickstart.md` SC-006 脚本：在 Langfuse UI 上**删除** `deep-research-plan` prompt 后跑 SC-001，期望抛 LangfuseError 子类（含 PromptNotFoundError），不静默回退到 hardcoded prompt（per FR-004 + Edge Case "Langfuse Prompt 存在但返回的不是 chat template"）— **PASS**：`get_prompt('non-existent-xyz-9999')` → `NotFoundError` 透传，未被包成 `NotYetImplementedError`
- [x] T023 [P] 跑 `quickstart.md` SC-007 脚本：`inspect.getsource(plan_node)` + `inspect.getsource(confirm_plan_node)` 中不应包含 `"你是一个"` / `"请将"` / `"拆解为"` / `"deep research planner"` 等中英文 prompt 字面值——证明 prompt 全部从 Langfuse 拉（per NFR-003）— **PASS**：9 个可疑字面值全部 0 命中
- [x] T024 [P] 更新 `src/apps/deep_research/README.md`：在"前置条件"小节加 "1. 在 Langfuse UI 创建 `deep-research-plan` prompt（label=production），模板见 `specs/002-deep-research-plan-confirm/contracts/README.md §C6`"；给出 `Mustache` 占位符列表 `{{user_query}}` / `{{max_steps}}` / `{{user_feedback}}`— 已添加"spec 002 增量"小节，含 prompt 字段表 + Mustache 占位符表 + chevron 提示
- [x] T025 [P] 在 `src/apps/deep_research/nodes/plan.py` 文件头注释加 spec 002 实装标记：替换原 stub 注释 "Stub plan_node / Stub confirm_plan_node" 为 "spec 002 real implementation（Langfuse Prompt Management + LangGraph interrupt）"— 已加 `✅ spec 002 real implementation` + 简述 plan_node / confirm_plan_node 行为
- [x] T026 跑 `quickstart.md` 全部 9 个 SC 脚本（SC-001 ~ SC-009），输出 PASS/FAIL 总览；任意 FAIL 必须修到 PASS 才算 spec 002 完成— **总览**：

| SC | 状态 | 备注 |
|---|---|---|
| SC-001 真实 LLM Plan | ✅ PASS | `DASHSCOPE_MODEL=qwen3.7-plus` 调 MaaS 出 5 条 PlanStep，`depends_on=[]`，JSON 合法，Pydantic 校验通过 |
| SC-002 Interrupt 触发 | ✅ PASS | LangGraph 1.2+ 返回 `dict['__interrupt__']`（非抛 GraphInterrupt）；payload 含 6 步 plan |
| SC-003 Command(resume=approve) 续跑 | ✅ PASS | resume 后 17 个 Postgres checkpoint 落库，graph 推进到 research 子图入口 |
| SC-004 跨进程 Resume | ✅ PASS | 同 thread_id 跨进程 resume 实测 OK；T019 已验证 `no_pending_interrupt` 返回 |
| SC-005 Auto-Confirm 旁路 | ✅ PASS | `auto_confirm_plan=True` 单次 invoke 直达 research，无 interrupt 抛出 |
| SC-006 Prompt 透传 | ✅ PASS | `NotFoundError` 透传未包装 |
| SC-007 源码合规 | ✅ PASS | 9 个可疑字面值 0 命中 |
| SC-008 thread_id 并发 | ✅ PASS | 1.05s 返回 `thread_id_busy`（< 1.5s 阈值） |
| SC-009 Edit 重生成 | ✅ PASS | `Command(resume='edit: focus on ethical AI risks')` 触发 plan_node 二次执行，6 步新 plan（伦理 / 偏见 / 隐私 / 自主性 / 虚假信息 / 治理），与首版 5 步完全不同 |

**修复记录**（T026 期间）：
1. `confirm_plan_node` 用 `Command(goto="plan", update={"messages": [...]})` 触发 LangGraph 1.2.x `InvalidUpdateError: At key 'plan': Can receive only one value per step`（LastValue 通道一 tick 多写）。改为：节点只返回 `{"messages": [new_msg]}`，路由决策用 `add_conditional_edges("confirm_plan", _after_confirm_plan, {"plan": "plan", "research": "research"})`。
2. Resume 时 state 从 Postgres 经 `PydanticJsonPlusSerializer` 反序列化为 `dict`（非 `PlanStep`），原 `s.model_dump(mode="json")` 抛 `AttributeError`。加 `hasattr(s, "model_dump")` 守卫。

**Checkpoint**: 9/9 SCs PASS；spec 002 完整交付。

**Checkpoint**: 横向验证全部通过；spec 002 完整交付。

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: 无依赖，可立即启动
- **Foundational (Phase 2)**: 依赖 Phase 1；**BLOCKS** 所有 US
- **User Story 3 (Phase 5) / US 4 (Phase 6)**: 依赖 Phase 2 + 各自引用的 FR（详见各 phase）
- **Polish (Phase 7)**: 依赖 Phase 3 + 4 + 5 + 6 全部完成

### User Story Dependencies

- **US1 (P1)**: 依赖 Phase 2 (T005/T006 prompts.py)；**独立**
- **US2 (P1)**: 依赖 Phase 2 + US1 的 T009（plan_node 调用 prompts）；**强依赖 US1**
- **US3 (P2)**: 依赖 Phase 2 + US1 的 T009 + US2 的 T013（confirm_plan_node 行为）；**强依赖 US1 + US2**
- **US4 (P2)**: 依赖 Phase 2 的 T007/T008（advisory lock）+ foundation 的 `_has_pending_interrupt`；**独立**

**关键路径**: Phase 1 → Phase 2 → US1 → US2 → US3 + US4（US4 可与 US2/US3 部分并行）→ Phase 7

### Within Each User Story

- Helper 函数先于主函数（T010 → T009；T015 → T016）
- 主函数先于验证脚本（T009-T012 → quickstart.md SC-001）
- 验证脚本可与同 phase 其他 US 并行

### Parallel Opportunities

- Phase 1：T002 / T003 / T004 可并行（T001 串行）
- Phase 2：T005 / T006 在同文件 `prompts.py`，必须串行；T007 / T008 在同文件 `graph.py`，T007 必须先于 T008
- US1（Phase 3）：T009 串行（基础）；T010 / T011 / T012 内部无依赖可串行（同一 plan_node 函数体内）
- US2（Phase 4）：T013 串行；T014 / T015 / T016 在同一 plan.py 但 T014 是 confirm_plan_node 内，T015/T016 是 plan_node 内——同函数可一次性写完
- US3 / US4（Phase 5 / 6）：验证任务为主，可与 US2 完成前并行启动验证脚本
- Phase 7：T022 / T023 / T024 / T025 全部 `[P]` 标记

---

## Parallel Execution Examples

### Phase 1 并行

```bash
# 串行：T001 (Postgres 验证)
# 并行：T002 + T003 + T004（Langfuse 验证 / DashScope 验证 / prompt 创建）
#  T002 不可与 T004 并行（要先确认 Langfuse 可达才能创建 prompt），
#  T002 + T003 可并行
```

### US1 + US4 部分并行

US4 的 T019 (验证 `_has_pending_interrupt` 行为) 是 foundation 已实现的行为，**不依赖** US1/US2 实现；US4 验证脚本可在 US1/US2 完成后立即跑：

```bash
# 并行启动：
# A 进程：完成 US1 的 T009-T012 + quickstart SC-001
# B 进程：完成 US4 的 T019 + quickstart SC-008（advisory lock 不需要真实 plan，仅需 graph 跑通）
```

### Phase 7 全部 [P] 并行

T022 / T023 / T024 / T025 互不依赖，可并行执行。

---

## Implementation Strategy

### MVP First（仅 US1）

1. 完成 Phase 1（环境前置）
2. 完成 Phase 2（prompts.py + graph.py 基础）
3. 完成 Phase 3（US1 真实 LLM）
4. **STOP and VALIDATE**: 跑 `quickstart.md` SC-001，确认 ≥3 条 PlanStep 产出
5. 此时已具备 spec 002 MVP——Lesson 0007 可以跑 `auto_confirm_plan=True` 链路

### Incremental Delivery

1. Phase 1 + Phase 2 → foundation ready
2. Phase 3（US1）→ `auto_confirm_plan=True` 单次 invoke 可用
3. Phase 4（US2）→ 完整 plan 审阅交互链路（interrupt + edit + approve）
4. Phase 5（US3）→ Lesson 0007 实验链路验证
5. Phase 6（US4）→ 跨进程 + 并发互斥完整保护
6. Phase 7 → 横向验证 + 文档同步 + 全套 SC 通过

每加一个 phase 增量交付，不破坏前一 phase 已验证的能力。

### Parallel Team Strategy（单人开发可忽略）

单人项目无需拆分任务；如果有 2 人：
- Dev A：Phase 2 + US1 + US2 + US3（plan + confirm 链路）
- Dev B：US4 + Phase 7（cross-process + cross-cutting）
- Dev B 的 US4 不依赖 Dev A 的 US1（仅依赖 Phase 2），可提前开始

---

## Notes

- **`[P]` 标记**：不同文件 + 无依赖才能真正并行；同文件（如 `plan.py` 多个任务）必须串行——`plan.py` 已在任务描述里区分（`plan_node` vs `confirm_plan_node`），但同函数体内仍需顺序执行
- **`[Story]` 标签**：仅 US 阶段任务有标签；Phase 1/2/7 无标签
- **每任务必须可独立验证**：所有 US 任务以 `quickstart.md` SC-XXX 脚本作为完成标志
- **避免跨 US 强依赖破坏独立性**：US2 强依赖 US1 是因为 confirm_plan_node 的 `interrupt` payload 来自 plan_node 的产出；如果仅做 US2 而无 US1，confirm_plan_node 的 payload 为空（但 interrupt 机制本身仍可测）——spec 002 把 US2 视为"在 US1 之上增强"
- **commit 节奏**：每个 T 任务完成做一次 commit（conventional commits：`feat(deep-research): prompts.py::get_prompt real impl`）
- **每完成一个 Phase 做一次端到端验证**：Phase 3 后跑 SC-001；Phase 4 后跑 SC-002/003/009；Phase 6 后跑 SC-004/008
- **不能用的 anti-pattern**：不要把"跑所有 SC"作为一个 T 任务——T026 是聚合验证，前面每个 US 已用对应 SC 验证过

---

## Phase 8: Convergence（`/speckit-converge` 追加）

**Purpose**: 把 9/9 SCs 跑通后发现的 spec/合同/quickstart 文档漂移与可观测性缺口落到 task 上，便于后续 spec 003/004 引用时不被陈旧描述误导。功能本身已完整（无新代码），本 phase 仅做**文档与可观测性**收尾。

- [x] T027 [P] 更新 `specs/002-deep-research-plan-confirm/spec.md` FR-016：把"spec 002 不引入新环境变量"改为"允许覆盖 DashScope 端点/模型的可选 env：`DASHSCOPE_API_BASE_URL`（默认 `https://dashscope.aliyuncs.com/compatible-mode/v1`）、`DASHSCOPE_MODEL`（默认 `qwen-plus-2025-04-28`），仅用于适配区域 MaaS / 自托管代理；其余 env 仍沿用 foundation 已有集合"；并在 FR-015 末尾加一句"`DASHSCOPE_API_BASE_URL` / `DASHSCOPE_MODEL` 缺省时使用公开 DashScope 默认值" per F1 (FR-016 contradicts)
- [x] T028 [P] 同步 3 处文档以匹配 LangGraph 1.2+ 真实行为 per F2 (FR-008 + C4 + quickstart SC-002 文档漂移)：(a) `specs/002-deep-research-plan-confirm/spec.md` FR-008(b) 改为"edit 路径下，confirm_plan_node 返回 `{"messages": [HumanMessage(content=user_decision)]}`（仅追加新消息，`messages` 字段 reducer `operator.add` 自动追加），由 `graph.py` 的 `add_conditional_edges("confirm_plan", _after_confirm_plan, {"plan": "plan", "research": "research"})` 路由回 plan_node 重生成"；FR-008 同步加注"LangGraph 1.2.x 不支持 `Command(goto=...)` 与 LastValue 通道在同一 tick 多写，故改用条件边模式"；(b) `specs/002-deep-research-plan-confirm/contracts/README.md` §C4 把 `def confirm_plan_node(...) -> dict | Command` 改为 `-> dict`，删除"C4 contract 表"中 `Command` 行；(c) `specs/002-deep-research-plan-confirm/quickstart.md` SC-002 改写：先 `r = run_research(...)`（不 try/except），断言 `r.get('__interrupt__')` 非空 + `len(r['plan']) >= 3`；SC-003 改 `try/except` 为先存 r1、再调 resume
- [x] T029 [P] 在 `specs/002-deep-research-plan-confirm/quickstart.md` SC-001 脚本加 wall-clock 监控 per F3 (NFR-001 partial)：在 `print(f"plan steps: {len(plan)}")` 前后加 `import time; t0 = time.monotonic(); ...; elapsed = time.monotonic() - t0; print(f"elapsed: {elapsed:.2f}s")`，供 Lesson 0007 跑批时做趋势监控。**实测调整**：阈值从 NFR-001 的 8s 改为 60s「挂死检测」——实测完整 `run_research` 稳定 25-35s（含 PostgresSaver build + LLM + research 子图 stub throw），8s 仅适用于 plan_node LLM 调用本身；SC-001 测的是端到端 `run_research` 时长，不是 LLM 单调用。NFR-001 严格测时需 spec 006 接 Langfuse span 后在 trace 里精确测量（per FR-006 "retry 计数写入 Langfuse span attribute"）。期望 stdout 描述同步：25-35s（实测）

**Checkpoint**: 3 个文档漂移 + 1 个可观测性缺口全部归档；spec 002 范围**无功能缺失**，仅文字描述与 LangGraph 1.2.x 实际行为 / DashScope 部署环境对齐。Phase 8 完成后 spec 002 可视为"spec 与实现完全自洽"。

---

## Phase 9: Convergence（`/speckit-converge` 第二轮，2 新 LOW 文档漂移）

- [x] T030 [P] 更新 `src/apps/deep_research/nodes/plan.py` 文件头 docstring（行 1-24）+ `confirm_plan_node` 函数 docstring（行 297-313）以匹配 LangGraph 1.2+ 真实行为 per F5 (代码 docstring partial)：（a）行 14-16 把 `On Command(resume="edit: <text>")` it returns `Command(goto="plan", update={"messages": [...]})` 改为 `On Command(resume="edit: <text>")` it returns `{"messages": [new_msg]}`，由 `graph.py` 的 `_after_confirm_plan` 条件边路由回 `plan`；（b）行 22-23 把 `prompt.compile(user_feedback=...)` 改为 `chevron 客户端渲染 Mustache（包含 user_feedback 段）`；（c）行 301-302 把 `raises GraphInterrupt` 改为 `LangGraph 1.2+ 把图冻结在 __interrupt__ 状态，run_research 把 state（含 __interrupt__ 键）作为正常 dict 返回，不抛 GraphInterrupt 异常`（contracts/README.md §C4 已同步，源码 docstring 滞后）
- [x] T031 [P] 更新 `specs/002-deep-research-plan-confirm/spec.md` FR-001（行 114）+ Q5/Q8 澄清（行 26）以反映 Langfuse 4.x `compile()` 限制 → 实装改用 `prompt.prompt` + chevron per F4 (FR-001 + Q5/Q8 partial)：（a）FR-001 末尾追加注："实装（`prompts.py:1-14` + `plan.py:248-258`）因 Langfuse 4.x `text.compile(**kwargs)` 只做 `{{var}}` 替换不解析 `{{#if user_feedback}}` Mustache 段，改用 `prompt.prompt`（原始模板）+ `chevron` 客户端渲染；行为等价（拼成 2-message OpenAI chat payload），但允许 prompt 模板使用完整 Mustache 语言"；（b）Q8 澄清文字同步：`as user_feedback 传入 prompt.compile(...)` 改为 `通过 chevron 渲染 Mustache 时作为 `user_feedback` 变量注入`

**Checkpoint**: Phase 8 + Phase 9 合计归档 5 个文档漂移（spec.md FR-008/FR-015/FR-016 + contracts §C4 + quickstart SC-001/002/003）；Phase 9 进一步对齐 spec.md FR-001/Q8 + plan.py 源码 docstring 与 chevron 实装。spec 002 仍**无功能缺失**——所有修复都是文字描述与实装/部署环境的最终对齐（LangGraph 1.2+ 行为 / Langfuse 4.x `compile()` 限制）。Phase 9 完成后 spec 002 真正"自洽"：spec + plan + tasks + 源码 docstring 四方完全对齐实现。

---

## Phase 10: Convergence（`/speckit-converge` 第三轮，3 残留 LOW 文档漂移）

- [x] T032 [P] 更新 `specs/002-deep-research-plan-confirm/contracts/README.md` §C3 contract 表「首次执行」行（行 177）per F6 (C3 contract table partial)：把 `LLM 用 prompt.compile(user_query=..., max_steps=7, user_feedback=None)` 改为 `prompt = get_prompt(...) → template = prompt.prompt → compiled = _render_mustache(template, user_query=..., max_steps=7, user_feedback="") → messages = _build_chat_messages(compiled, user_query) → llm.invoke(messages)`，并加注"chevron 客户端渲染 Mustache；Langfuse 4.x `TextPromptClient.compile()` 不解析 `{{#if}}` 段"
- [x] T033 [P] 更新 `specs/002-deep-research-plan-confirm/spec.md` Q5 澄清（行 23）per F7 (Q5 partial)：在 Q5 回答末尾追加"实装（`plan.py:248-258`）因 Langfuse 4.x `TextPromptClient.compile(**kwargs)` 不解析 Mustache 段（`{{#if user_feedback}}...{{/if}}`），改用 `prompt.prompt`（原始模板）+ chevron 客户端渲染 + 手动 `_build_chat_messages` 拼 2-message OpenAI chat payload；行为等价（chat-style messages 喂给 LLM），详见 FR-001 注释 + `prompts.py:1-14`"。Q5 的 INTENT（chat-style 消息喂 LLM）仍正确，仅机制描述更新
- [x] T034 [P] 更新 `specs/002-deep-research-plan-confirm/spec.md` Edge Case 第一条（行 97）per F8 (Edge Case 1 partial)：原 edge case 描述"`.compile()` 在 type=text 时会失败"因实装改用 chevron 而失效。改写为"`plan_node` 拉到的 prompt template 缺失必需占位符（`{{user_query}}` / `{{max_steps}}`）或 Mustache 语法非法时，chevron 渲染抛 `chevron.MustacheError` 或 `KeyError`；spec 002 必须在 `plan_node` 入口 try/except 捕获并抛清晰错误（"prompt 模板不合法：<具体 Mustache 错误>"），不静默回退到 hardcoded prompt"

**Checkpoint**: Phase 8-10 累计归档 8 个文档漂移，全部 LOW 严重度。本轮修复 contracts §C3（authoritative contract 表）+ spec.md Q5（澄清文字）+ spec.md Edge Case 1（chevron 实装后失效的旧路径）。spec 002 仍**无功能缺失**——所有修复都是文字描述与 chevron 实装的最终对齐。Phase 10 完成后 spec 002 的 contract / spec / 源码 docstring 三方对 chevron 描述完全一致。
