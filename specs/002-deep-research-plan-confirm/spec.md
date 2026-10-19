# Feature Specification: Plan 与 Confirm 阶段（真实 LLM + interrupt）

**Feature Branch**: `[002-deep-research-plan-confirm]`

**Created**: 2026-10-18

**Status**: Draft

**Input**: User description: "@specs/001-deep-research-foundation/spec.md 基于已完成的 spec 001（foundation：包骨架 + schemas + state + 空图 + stub 节点），规划 spec 002：plan 阶段。Scope：1. 把 nodes/plan.py 的 stub plan_node 替换为真实 LLM 调用，从 Langfuse Prompt Management 拉取 deep-research-plan prompt，产出结构化 Plan（list of PlanStep）2. 把 stub confirm_plan_node 替换为 interrupt({"plan": state["plan"]}) 实现 3. 在 graph.py 暴露 auto_confirm_plan 旁路（Lesson 0007 实验用）4. 验证 PostgresSaver 跨进程 resume：用同一 thread_id 关掉重连能续上 5. 验收：run_research() 第一次 invoke 触发 __interrupt__，Command(resume="approve") 后能从 confirm_plan 继续跑到 research 入口。不要做：search / write / UI / Langfuse hooks（后续 spec）"

**Source of truth**: 本 spec 只覆盖 Phase 2（plan + confirm），在 spec 001 foundation 之上增量实现。设计意图与领域词汇见 `src/apps/deep_research/CONTEXT.md §2 Entities` 与 `docs/adr/0001-nested-subgraphs.md` / `0003-interrupt-for-plan-confirm.md`。

---

## Clarifications

### Session 2026-10-18

- Q1: `plan_node` 走真实 LLM 时，**产出 PlanStep 的条数上限**应该是多少？→ A: 默认 `min_steps=3, max_steps=7`。理由：少于 3 条体现不出"分章节研究"的价值（CONTEXT.md §2.1 中 PlanStep 与报告章节 1:1 对应），多于 7 条单次 search query 量过大（5 条/轮 × 7 步 = 35 次 websearch，超出 DashScope 免费层配额）。LLM 通过 prompt 给出 ≤ 7 步，超出由 Pydantic validation 拦截并提示。
- Q2: `confirm_plan_node` 收到 `Command(resume="edit:...")` 时，应该**回到 plan_node 重新生成**，还是**直接修改 state["plan"]**？→ A: 仅当 payload 以 `"edit:"` 开头且非空时，把 edit 指令写入 `state["messages"]`（用 HumanMessage）并 `Command(goto="plan", update=...)` 回 plan_node 重新生成；`"approve"` 或其他值都视为通过。理由：让 User 在 UI 上既能 Approve 一键通过，也能注入修改意见触发再生成；不直接 mutate plan 是为了保持 plan_node 是 plan 的唯一来源（单一写入点便于打 Langfuse score）。
- Q3: `prompts.py::get_prompt()` 拉不到 Langfuse 上的 `deep-research-plan` prompt 时，应该**抛错**还是**降级到 hardcoded fallback**？→ A: 抛 `langfuse.core.prompt.PromptNotFoundError`（透传，不包成自定义异常），由 `plan_node` 顶层 `try/except` 转成 `NotYetImplementedError` 让上层 UI 显示"prompt 缺失"。理由：spec 002 阶段 Langfuse prompt 必须存在（Lesson 0006 同步创建），不存在是**配置错误**而非代码分支，不应静默降级导致 plan 质量不可控。
- Q4: 跨进程 resume 验证时，**第二个进程应该模拟哪些操作**才能算"真跨进程"？→ A: 第二个进程必须 `import` 图（调用 `build_graph()` 重新拿到新 saver 实例）、只用 `thread_id` 字符串 + `Command(resume="approve")` 调用 `run_research()`，不应共享任何 Python 对象（不复用 graph 实例、不复用 LLM 客户端、不复用 Langfuse client）。理由：跨进程的核心是 checkpointer 是**唯一**状态载体；任何 Python 层缓存都会绕过这条约束，验证失去意义。
- Q5: `plan_node` 走真实 LLM 时，是否需要把 Langfuse prompt 的 chat_template（system + user）拼成 messages 后调 LLM，还是**直接 `prompt.compile(...)`**？→ A: 用 Langfuse `langfuse.get_prompt(name, label=).compile(user_query=state["user_query"], max_steps=7)` 返回的 chat-style `list[dict]` 作为 LLM 输入。理由：Langfuse prompt 是 chat template（不是单字符串），直接字符串拼接会让 prompt 改格式时 break；compile() 是 Langfuse 2.x 官方推荐用法。**实装注意**：Langfuse 4.x `TextPromptClient.compile(**kwargs)` 不解析 Mustache 段（`{{#if user_feedback}}...{{/if}}`），实装（`plan.py:248-258`）改用 `prompt.prompt`（原始模板）+ chevron 客户端渲染 + 手动 `_build_chat_messages` 拼 2-message OpenAI chat payload。行为等价（chat-style messages 喂给 LLM），详见 FR-001 注释 + `prompts.py:1-14`。
- Q6: 当两个独立 Python 进程（或同进程两个请求）使用**同一个 `thread_id`** 并发地调 `run_research(...)`（一个 invoke、一个 resume，或两个 resume）时，spec 002 应该怎么处理？→ A: `run_research` 在 invoke / resume 入口用 Postgres advisory lock（`pg_advisory_xact_lock(hashtext(:thread_id))`）做 thread_id 级互斥；并发第二个进程拿不到锁时返回 `{"error": "thread_id_busy", "thread_id": tid}` 结构化错误，由 UI（spec 005）提示 User 重试。理由：spec 002 的"跨进程 resume"承诺（US4）反向用例就是并发；LangGraph 默认行为会在 race condition 下让两个进程的 state 互相覆盖，导致 plan / checkpoint 数据静默损坏（FR-013 已经把"Postgres 是唯一状态载体"作为约束，所以必须用 Postgres 的并发原语保护）；advisory lock 比 `SELECT FOR UPDATE` 轻量，事务结束自动释放。
- Q7: 当 `plan_node` 调 DashScope LLM 时遇到 HTTP 429（rate limit）或 HTTP 5xx（DashScope 服务端错误），spec 002 期望 plan_node 怎样处理？→ A: `plan_node` 内部对 HTTP 429 / 5xx 用指数退避（base=1s, max_retries=2, exponential=2x）重试共 2 次；非 429 / 5xx 错误（如 400 bad request、401 unauthorized）不重试直接抛 `NotYetImplementedError`。理由：免费层限流是预期失败而非异常，2 次重试足够覆盖 1 分钟内的瞬时限流；不引入 SDK-level retry 是为了把策略集中在 plan_node（便于打 Langfuse score "plan retry count"）。
- Q8: 当 User 用 `Command(resume="edit: <修改意见>")` 触发二次 plan 生成时，spec 002 期望 `deep-research-plan` Langfuse prompt template 如何处理"修改意见"？→ A: `deep-research-plan` prompt template MUST 包含 `user_feedback: str | None` 占位符；`plan_node` 在 invoke 前从 `state["messages"]` 提取最近一条 HumanMessage（若 `content` 以 `"edit:"` 开头则截掉前缀 5 字符作为 `user_feedback`）作为 `user_feedback` 变量传给 chevron 渲染 Mustache（`user_query=..., max_steps=7, user_feedback=...`）；User 在 Langfuse UI 上创建 prompt 时把这个字段加进 system / user message 模板即可。理由：FR-008 已经写"edit 写入 messages 回 plan_node"，但 prompt 渲染不传 feedback 会让 HumanMessage 被静默丢弃——这是 US2 验收场景 3 的隐性 bug；用同一个 prompt 的可选字段比新增 `deep-research-plan-edit` 副本能避免 prompt 数量翻倍。**实装注意**：Langfuse 4.x `TextPromptClient.compile(**kwargs)` 不解析 Mustache 段（`{{#if user_feedback}}...{{/if}}`），spec 002 改用 `prompt.prompt`（原始模板）+ `chevron` 客户端渲染（见 FR-001 注释 + `prompts.py:1-14`）。

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 真实 LLM 生成结构化 Plan（P1）

作为 deep research agent 的 **User**，我提交一个研究问题后能看到一份**结构化的 Plan**（3–7 个 `PlanStep`），每个 `PlanStep` 都有 title + 可选 description + 可选 depends_on，而不是一段自由文本——这样我能逐章节审阅、修改、批准。

**Why this priority**：P1，spec 002 的核心交付物。foundation spec 的 stub 只能产出 1 条占位 PlanStep，无法验证"分章节研究"的设计意图。

**Independent Test**：单次 `python -c "from src.apps.deep_research.graph import run_research; r = run_research({'user_query': '分析 LangGraph interrupt 在 deep research 中的用法', 'auto_confirm_plan': True}, thread_id='plan-gen-001'); print(len(r['plan']))"` 返回 ≥ 3。

**Acceptance Scenarios**：

1. **Given** Langfuse 上存在 `deep-research-plan` prompt（label="production"）且 Qwen LLM 可达，**When** User 调用 `run_research({'user_query': '<某研究问题>', 'auto_confirm_plan': True}, thread_id='...')`，**Then** 返回 state 中 `len(state["plan"]) >= 3` 且每个元素都是 `PlanStep` 实例（含 `title` 必填字段）。
2. **Given** LLM 产出 ≥ 8 个 PlanStep（超出 max_steps=7），**When** Pydantic validation 校验，**Then** 抛出 `ValidationError` 且 `plan_node` 把异常包成 `NotYetImplementedError("plan steps exceed max_steps=7")`，由上层清晰报告，不静默丢弃。
3. **Given** Langfuse 上**没有** `deep-research-plan` prompt，**When** `plan_node` 调 `prompts.get_prompt('deep-research-plan', label='production')`，**Then** 抛出 `PromptNotFoundError`（透传），`plan_node` 不静默降级到 hardcoded prompt。

---

### User Story 2 - Interrupt + Resume（P1）

作为 **User**，我能在 LangGraph 抛 `__interrupt__` 时看到完整 plan，并通过 `Command(resume="approve")` 让图继续推进到 `research` 子图入口——证明 LangGraph 1.x 的 `interrupt() + Command(resume)` 模式在 deep research 图上**真正工作**。

**Why this priority**：P1，ADR-0003 的核心承诺。如果 interrupt 没真触发或 resume 不能续跑，整个"plan 审阅"的设计意图破产，后续所有 spec 003-005 都依赖这个机制。

**Independent Test**：单 Python 脚本顺序跑两步——(1) `run_research({'user_query': '...'})` 应在第一次 invoke 后**不返回最终状态**而是抛 `GraphInterrupt`；(2) 同 `thread_id` + `Command(resume="approve")` 应顺利抵达 `research` 节点（在 Postgres `checkpoints` 表可见 node 名序列）。

**Acceptance Scenarios**：

1. **Given** `auto_confirm_plan=False`（默认），**When** `run_research({'user_query': '...'})` 第一次 invoke，**Then** 抛 `langgraph.errors.GraphInterrupt`（或 LangGraph 1.x 等价的 `__interrupt__` payload），**且** Postgres `checkpoints` 表中最新一条 state 的 `next` 字段为 `"research"`（图已"准备好"往下走，但被 interrupt 拦下）。
2. **Given** 步骤 1 已抛 interrupt，**When** 用同 `thread_id` 调 `run_research(Command(resume="approve"), thread_id=...)`，**Then** `run_research` 不返回 `{"error": "no_pending_interrupt", ...}` 错误，而是返回最终 state（含 `state["plan"]` 与 `sources`）。
3. **Given** User 想修改 plan，**When** 调 `Command(resume="edit: 增加 '可观测性' 章节')` resume，**Then** `confirm_plan_node` 把 edit 指令写进 `state["messages"]` 作为 HumanMessage，再 `Command(goto="plan", update={"messages": [...]})` 回 plan_node 重新生成（不直接 mutate `state["plan"]`）。

---

### User Story 3 - Auto-Confirm 旁路（P2，Lesson 0007 实验用）

作为 **Lesson 0007 的实验脚本**，我能在 `run_research(input, ..., auto_confirm_plan=True)` 时**跳过 interrupt** 让图一次跑到底——用于离线跑 N 个研究问题的自动化实验，不需要 UI 介入。

**Why this priority**：P2，是 foundation spec 已预留的 `auto_confirm_plan` 控制位（FR-008）的"兑现"——spec 002 必须保证它在真实 LLM + interrupt 模式下仍然工作，否则 Lesson 0007 的实验链路断裂。

**Independent Test**：`run_research({'user_query': '...', 'auto_confirm_plan': True}, thread_id='auto-confirm-001')` **单次调用**直接返回最终 state，**不抛** GraphInterrupt，Postgres `checkpoints` 表无 `__interrupt__` 残留。

**Acceptance Scenarios**：

1. **Given** `auto_confirm_plan=True`，**When** `run_research({'user_query': '...', 'auto_confirm_plan': True})`，**Then** `confirm_plan_node` 直接返回空 dict，**不**触发 `interrupt()`，图一次跑到底（跑到当前未实现 stub 的 research 子图就会抛 stub `NotYetImplementedError`，但 plan + confirm 阶段完整通过）。
2. **Given** `auto_confirm_plan=False`（默认），**When** 同上 input dict，**Then** interrupt 触发（同 US2 行为）。

---

### User Story 4 - PostgresSaver 跨进程 Resume（P2）

作为 **User**，我能关掉 Python 进程、重新启动、再用同一个 `thread_id` 调 `run_research(Command(resume="approve"), thread_id=...)`——证明 PostgresSaver 是**唯一**状态载体，interrupt 后的状态没有丢失在内存里。

**Why this priority**：P2，是 ADR-0005 的承诺。Lesson 0007 跑长时实验（interrupt 后人工下班，第二天再 resume）依赖这条；Streamlit UI（spec 005）每次按钮 rerun 也相当于一次"跨进程"调用。

**Independent Test**：两个独立 Python 进程分别执行——进程 A 调 `run_research({'user_query': '...'})` 触发 interrupt 后**正常退出**；进程 B 用同 `thread_id` 调 `run_research(Command(resume="approve"), thread_id=...)`，**不**报 `{"error": "no_pending_interrupt", ...}`，而是正常推进图。

**Acceptance Scenarios**：

1. **Given** 进程 A 已触发 interrupt 且 Postgres `checkpoints` 表存在对应 `thread_id` 行，**When** 进程 B 全新 import + 全新 `build_graph()` + `run_research(Command(resume="approve"), thread_id=<同 thread_id>)`，**Then** `_has_pending_interrupt(graph, config)` 返回 True，`run_research` 不返回 no_pending_interrupt 错误，图继续跑。
2. **Given** 进程 A 与进程 B 不共享任何 Python 对象（不复用 graph 实例、不复用 LLM 客户端、不复用 Langfuse client），**When** 检查 Postgres `checkpoints` 表 `thread_id` 列，**Then** 两进程的 checkpoint 共用同一行（不重复创建）。
3. **Given** 跨进程 resume 成功后，**When** 检查 Postgres `checkpoints` 表写入时间戳，**Then** 同一 `thread_id` 下至少有 2 条 checkpoint（一来自 invoke，一来自 resume），且都可在新进程里读到。

---

### Edge Cases

- **Langfuse Prompt 模板不合法 / 缺少必需占位符**：`plan_node` 拉到的 `deep-research-plan` prompt template 缺失必需占位符（`{{user_query}}` / `{{max_steps}}`）或 Mustache 语法非法时，chevron 渲染会抛 `chevron.MustacheError` 或 `KeyError`。spec 002 必须在 `plan_node` 入口 try/except 捕获并抛清晰错误（"prompt 模板不合法：<具体 Mustache 错误>"），不静默回退到 hardcoded prompt。
- **LLM 输出 JSON 解析失败**：LLM 返回的 plan JSON 不符合 Pydantic schema（例如 PlanStep 缺 `title` 字段）。spec 002 必须由 `plan_node` 内部 try/except 处理一次 LLM 重试（最多 1 次），仍失败则抛 `NotYetImplementedError("plan generation failed after retry")`。
- **Plan 全空 / 全重复**：LLM 返回 `[]` 或所有 `PlanStep.title` 完全相同。Pydantic validation 应拒绝空 Plan；全重复由 `plan_node` 内部检测并强制重试一次。
- **`Command(resume=<其他字符串>)`**：User 传 `"reject"` 或 `"不知道"` 等非 `"approve"` / `"edit:"` 前缀的值。当前 spec 002 把这些也视为"通过"（与 ADR-0003 决策一致），未来可在 UI 层加 reject 按钮。
- **Resume 时 Postgres 不可达**：spec 002 不覆盖（这是 foundation spec 已声明的"Postgres unreachable 时 fail fast"行为的延伸，由 `build_graph()` 抛连接错误）。
- **同 thread_id 并发 invoke / resume**：两个进程（或同进程两个请求）使用同一 `thread_id` 并发调 `run_research` 时，第二个进程在 advisory lock 等待超时后 MUST 返回 `{"error": "thread_id_busy", "thread_id": tid}`（per FR-017），不阻塞到第一个进程结束。锁等待超时默认 `1s`（`SET LOCAL lock_timeout = '1s'`），超时即放弃，避免 UI 卡死。
- **DashScope 连续 429**：连续 3 次以上 HTTP 429（即 2 次 retry 都失败），plan_node MUST 抛 `NotYetImplementedError("dashscope rate limited after 2 retries")`，由 UI 提示"DashScope 限流中，请稍后重试"。Lesson 0007 实验跑 N 个 query 时预期会偶发，UI 需支持"重试"按钮（spec 005 接）。
- **LLM 返回空 Plan（`[]`）**：Pydantic validation 拒绝空 list，plan_node 检测到 `len(parsed) == 0` 后 MUST 走与"JSON 解析失败"相同的 1 次重试路径（per FR-006(a)），重试仍空才抛错。

---

## Requirements *(mandatory)*

### Functional Requirements

#### Plan 生成（核心：US1）

- **FR-001**：`nodes/plan.py::plan_node(state)` MUST 调用 `prompts.get_prompt('deep-research-plan', label='production')` 拉取 Langfuse 上的 chat template prompt，再调 `prompts.get_llm('plan').invoke(...)` 拿到 LLM 返回。**实现细节**：因 Langfuse 4.x `TextPromptClient.compile(**kwargs)` 只做 `{{var}}` 替换不解析 Mustache 段（`{{#if user_feedback}}...{{/if}}`），实装（`prompts.py:1-14` + `plan.py:248-258`）改用 `prompt.prompt`（原始模板）+ `chevron` 客户端渲染，再手动拼成 2-message OpenAI chat payload。行为等价（chat-style messages 喂给 LLM），但允许 prompt 模板使用完整 Mustache 语言。
- **FR-002**：`plan_node` MUST 把 LLM 返回的 JSON 解析为 `list[PlanStep]`（每个 PlanStep 含必填 `title: str`、可选 `description: str | None`、`depends_on: list[int]`），写入 `state["plan"]`。当 LLM 返回条目数超出 `[3, 7]` 区间时 MUST 抛 `ValidationError`。
- **FR-003**：`plan_node` MUST 把 Langfuse prompt 的输入参数透传：`user_query: str`（来自 state）、`max_steps: int = 7`（hardcoded）、`user_feedback: str | None`（从 `state["messages"]` 最近一条 HumanMessage 提取，若 `content` 以 `"edit:"` 开头则去掉前缀；无 HumanMessage 则为 `None`）。不得硬编码中文 prompt 文本——所有 prompt 字面值必须从 Langfuse 拉。`deep-research-plan` prompt template MUST 包含 `user_feedback` 占位符（系统/用户消息模板均可，由 User 在 Langfuse UI 上创建）。
- **FR-004**：`prompts.py::get_prompt(name, label)` MUST 真实接入 Langfuse 2.x client（`from langfuse import Langfuse; lf = Langfuse()`），返回 `langfuse.api.text_prompt.TextPrompt` 对象（带 `.compile(**kwargs)` 方法）。`get_prompt` MUST 把 `PromptNotFoundError` 透传，不包成自定义异常。
- **FR-005**：`prompts.py::get_llm(role)` MUST 为 `role="plan"` 返回一个 `ChatOpenAI` 实例（DashScope 兼容 OpenAI 接口，`base_url="https://dashscope.aliyuncs.com/compatible-mode/v1"`、`model="qwen-plus-2025-04-28"`、`temperature=0.4`）。其他 role（`"research" / "write" / "critic" / "polish"`）仍保持 stub 抛 `NotYetImplementedError`——spec 003/004 才实装。
- **FR-006**：`plan_node` MUST 对 LLM 调用包一层 `try/except` 处理：(a) JSON 解析失败 → 重试 1 次（同 prompt 再 invoke 一次）；(b) 重试仍失败 → 抛 `NotYetImplementedError("plan generation failed: <reason>")`；(c) `ValidationError` 超出 max_steps → 直接抛 `NotYetImplementedError("plan steps exceed max_steps=7")`；(d) HTTP 429 / 5xx → 指数退避 2 次（base=1s, exponential=2x），仍失败抛 `NotYetImplementedError("dashscope rate limited after 2 retries")`；(e) 其他 HTTP 错误（400 / 401 / 403）→ 不重试直接透传。retry 计数写入 Langfuse span attribute（`langfuse.update_current_span(metadata={"plan_retry_count": N})`，spec 006 接 hook 前临时用 print 占位）。

#### Confirm 与 Interrupt（核心：US2）

- **FR-007**：`nodes/plan.py::confirm_plan_node(state)` MUST 调用 `interrupt({"plan": state["plan"]})` 暂停图，payload 是 `{"plan": <list of PlanStep dicts>}`（Pydantic 实例经 `.model_dump(mode="json")` 序列化，避免 msgpack 兼容问题）。
- **FR-008**：`confirm_plan_node` MUST 解析 `interrupt` 返回值（`Command(resume=...)` 的 payload）：(a) `str == "approve"` → 返回 `{}`（空 dict，让图继续）；(b) `str.startswith("edit:")` 且 len > 5 → 把 edit 文本包成 `HumanMessage(content=str)` 并返回 `{"messages": [HumanMessage(content=user_decision)]}`（仅追加新消息，`messages` 字段 reducer `operator.add` 自动合并），由 `graph.py` 的 `add_conditional_edges("confirm_plan", _after_confirm_plan, {"plan": "plan", "research": "research"})` 路由回 `plan_node` 重生成；(c) 其他值 → 视为 approve，返回 `{}`。**LangGraph 1.2.x 不支持 `Command(goto=...)` 与 LastValue 通道在同一 tick 多写（会抛 `InvalidUpdateError: At key 'plan': Can receive only one value per step`），故 spec 002 采用条件边模式而非 `Command(goto="plan", update={...})`。**
- **FR-009**：`confirm_plan_node` MUST 在 `state.get("auto_confirm_plan", False) == True` 时直接返回 `{}`，不调 `interrupt()`（保证 Lesson 0007 实验可绕过）。

#### Auto-Confirm 旁路（US3）

- **FR-010**：`graph.py::run_research(input, *, thread_id, config)` MUST 接受 `input["auto_confirm_plan"]: bool`（默认 `False`），并在初始 state 中把它写入 `state["auto_confirm_plan"]`，由 `confirm_plan_node` 读取。已有 foundation FR-008 已声明此字段；spec 002 必须保证它在真实 LLM + interrupt 模式下仍生效。
- **FR-011**：`run_research` MUST 把 `auto_confirm_plan` 的初始化逻辑**显式保留**（不删），且 MUST 接受 `auto_confirm_plan=True` 时**单次 invoke** 就走完 plan → confirm_plan（跳过 interrupt）→ 抛下游 research stub `NotYetImplementedError`（spec 003 才接）。

#### 跨进程 Resume（核心：US4）

- **FR-012**：`graph.py::run_research` MUST 接受 `Command(resume=...)` 作为 `input` 参数，并在收到 `Command(resume=...)` 时先调用 `_has_pending_interrupt(graph, base_config)` 检查当前 thread 是否真有 pending interrupt——若没有返回 `{"error": "no_pending_interrupt", "thread_id": tid}`（继承 foundation FR-007 / Q3 澄清），**不**调 `graph.invoke(input, config=base_config)`。
- **FR-013**：跨进程 resume 必须完全依赖 Postgres `checkpoints` 表——任何 LangGraph 内部 in-memory 缓存都不能成为状态来源。验证手段：进程 B 不复用进程 A 的 `compiled_graph` 实例（必须重新 `build_graph()`）、不复用 Langfuse client 实例（每次 `get_prompt` 都新连）。
- **FR-014**：`run_research` MUST 在每次调用时**新建** `build_graph()` 实例（不复用跨调用的图对象），保证 PostgresSaver 重新连接到当前进程的 connection pool——这本身就是跨进程 resume 的隐式验证。

#### 配置与依赖

- **FR-015**：`prompts.py::get_prompt()` 与 `get_llm()` MUST 从环境变量读取 Langfuse 与 DashScope 配置：`LANGFUSE_HOST`（默认 `http://localhost:3000`，可被 `LANGFUSE_BASE_URL` 覆盖）、`LANGFUSE_PUBLIC_KEY`、`LANGFUSE_SECRET_KEY`、`DASHSCOPE_API_KEY`。无 `DASHSCOPE_API_KEY` 时 `get_llm` MUST 抛 `RuntimeError("DASHSCOPE_API_KEY not set")`——不静默降级。`DASHSCOPE_API_BASE_URL`（默认 `https://dashscope.aliyuncs.com/compatible-mode/v1`）与 `DASHSCOPE_MODEL`（默认 `qwen-plus-2025-04-28`）缺省时使用公开 DashScope 默认值；不设置也不报错，仅在区域 MaaS / 自托管代理场景下需要覆盖。
- **FR-016**：spec 002 不引入 Lesson 0007 的实验用 flag；只允许 2 个**可选**覆盖 env 用于适配区域 MaaS / 自托管代理：`DASHSCOPE_API_BASE_URL`、`DASHSCOPE_MODEL`（缺省行为见 FR-015）。其余 env 仍沿用 foundation 已有 `LANGGRAPH_CHECKPOINT_DB` / `LANGFUSE_*` / `DASHSCOPE_API_KEY`，不新增也不重命名。
- **FR-017**：`graph.py::run_research` MUST 在 invoke / resume 入口对当前 `thread_id` 加 Postgres advisory lock（`pg_advisory_xact_lock(hashtext(:thread_id))`，事务提交时自动释放）；锁被占时返回 `{"error": "thread_id_busy", "thread_id": tid}` 结构化结果（与 Q3 的 `no_pending_interrupt` 错误形状一致），由 UI（spec 005）渲染提示。锁的范围：仅覆盖 `_has_pending_interrupt` + `graph.invoke()` 之间的 critical section，不覆盖 LLM 调用本身（避免长 transaction 拖死连接池）。

### Key Entities *(include if feature involves data)*

- **PlanStep**（已在 `schemas.py`）：单条 PlanStep 由 LLM 产出，包含 `title: str`（必填）、`description: str | None`、`depends_on: list[int]`。
- **Plan**（已在 `schemas.py`）：包装 `list[PlanStep]`，LLM 输出经 Pydantic validation 校验。Plan 仅在 LLM 输出阶段作为中间容器存在，写入 state 后解构为 `list[PlanStep]`。
- **UserQuery**（已在 `schemas.py`）：User 提交的研究问题，本 spec 中仅作为 `state["user_query"]: str` 传入，不强制 schema 校验。
- **interrupt payload**：本 spec 新出现的"in-memory 数据契约"——`{"plan": <list of PlanStep JSON>}`。注意：是 Pydantic 的 `.model_dump(mode="json")` 输出（dict 形式），不是裸 list——LangGraph `interrupt()` 的 payload 必须是 msgpack-serializable。

### Non-Functional Requirements

- **NFR-001**：单次 `plan_node` 调 LLM 的 P50 latency（本地 DashScope `qwen-plus`）应 ≤ 8 秒。若超过 15 秒 UI 应显示"生成中…"占位（spec 005 实现，本 spec 仅保证不阻塞主线程）。
- **NFR-002**：plan_node MUST NOT 修改 `state["iteration_counts"]`——counter 由 spec 003/004 才动，本 spec 只在 `graph.py::run_research` 入口初始化（继承 foundation FR-004）。
- **NFR-003**：所有 prompt 文本字面值（MUST 从 Langfuse 拉取）不得在 Python 源码中以字符串字面量形式出现——便于 Lesson 0006 通过 Langfuse UI 修改 prompt 时不需重发版。
- **NFR-004**：`prompts.py::get_prompt()` 与 `get_llm()` MUST 在 Langfuse 不可达时**不静默 fallback**——失败必须抛出真实异常，让 `plan_node` 在 try/except 里捕获并转换为 `NotYetImplementedError` 上抛。

---

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**：开发者在 Langfuse UI 上**预先**创建 chat 类型 prompt `deep-research-plan`（label="production"），内容含 system message + user message template（占位符 `{user_query}` 与 `{max_steps}`）。运行 `python -c "from src.apps.deep_research.graph import run_research; r = run_research({'user_query': '分析 LangGraph interrupt 在 deep research 中的用法', 'auto_confirm_plan': True}, thread_id='sc-001'); print(len(r['plan']), type(r['plan'][0]).__name__)"`，stdout 输出形如 `4 PlanStep`。
- **SC-002**：运行 `python -c "from src.apps.deep_research.graph import run_research; r = run_research({'user_query': '...'}, thread_id='sc-002-int'); print(r)"`（`auto_confirm_plan=False`），**第一次**调用不返回最终 state——而是在 `try` 块内抛 `langgraph.errors.GraphInterrupt`（或 `result['__interrupt__']` 非空）。
- **SC-003**：在 SC-002 抛 interrupt 之后，立即在**同一脚本**或**另一脚本**（相同 `thread_id='sc-002-int'`）调 `from langgraph.types import Command; run_research(Command(resume='approve'), thread_id='sc-002-int')`，返回的 state 含 `state["plan"]`（非空 list）、`state["auto_confirm_plan"] == False`、且执行流已经走过 `research` 节点（Postgres `checkpoints` 表可见 `node="research"` 的 state 记录，或因 spec 003 还没实装而抛下游 `NotYetImplementedError`）。
- **SC-004**：跨进程验证——脚本 A 跑 `run_research({'user_query': '...'}, thread_id='sc-004')` 触发 interrupt 后 `sys.exit(0)`；脚本 B 完全独立（`python -c "..."`），同 `thread_id='sc-004'` 调 `run_research(Command(resume='approve'), thread_id='sc-004')` 不返回 `{"error": "no_pending_interrupt", ...}`。验证 Postgres `SELECT count(*) FROM checkpoints WHERE thread_id = 'sc-004'` ≥ 2。
- **SC-005**：`auto_confirm_plan=True` 时，单次 invoke 走完 plan → confirm_plan 不抛 GraphInterrupt——通过 `try: run_research({'user_query': '...', 'auto_confirm_plan': True}, thread_id='sc-005'); except langgraph.errors.GraphInterrupt: print('FAIL: interrupt triggered'); else: print('PASS')` 验证。
- **SC-006**：当 Langfuse 上**没有** `deep-research-plan` prompt 时，`plan_node` 调 `prompts.get_prompt('deep-research-plan', label='production')` 抛 `langfuse.api.errors.PromptNotFoundError`（透传），不静默回退到 hardcoded prompt——可通过 `del Langfuse 上的 prompt` + 重新跑 SC-001 验证。
- **SC-007**：spec 002 完成后，`from src.apps.deep_research.nodes.plan import plan_node, confirm_plan_node; import inspect; print(inspect.getsource(plan_node))` 中**不应**出现 LLM prompt 字面值（如 `"你是一个"`、`"请将研究问题拆解为"` 等中文 prompt 字符串）——所有 prompt 文本必须来自 Langfuse。
- **SC-008**：并发同 `thread_id` 测试——脚本 A 持有 lock 期间，脚本 B（独立进程，同 `thread_id`）调 `run_research(...)` 应在 1 秒内返回 `{"error": "thread_id_busy", "thread_id": tid}`，不阻塞到脚本 A 完成。脚本 A 结束后脚本 B 再调一次能正常进入图（per FR-017）。
- **SC-009**：edit 重生成测试——先跑 SC-002 触发 interrupt；再跑 `Command(resume="edit: 增加 '可观测性' 章节作为第 5 步")` resume，应触发 plan_node 第二次执行；Postgres `checkpoints` 表可见同一 `thread_id` 有 ≥ 3 条 checkpoint（首次 invoke / edit resume / 再走 confirm），且最终 `state["plan"]` 含新 title（"可观测性"）。验证 Langfuse `deep-research-plan` prompt template 含 `user_feedback` 占位符且被正确填充（per FR-003 + FR-008）。

---

## Assumptions

- Langfuse 服务在 `http://localhost:3000` 可达（来自 `langfuse-deploy/docker-compose.yml`），且 `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` 已在 `.env` 配置（Lesson 0006 同步创建）。
- DashScope API key 已配置（`DASHSCOPE_API_KEY`），且使用 `qwen-plus-2025-04-28` 模型；用户同意引入这个 OpenAI 兼容接口作为 plan LLM。
- `deep-research-plan` prompt 由开发者在 Langfuse UI 上**手动创建**（chat 类型，label="production"，含 `{user_query}` 与 `{max_steps}` 占位符）——本 spec 不写创建脚本，假设用户跟随 README 操作。
- Plan 中 PlanStep 的 `depends_on: list[int]` 字段允许 LLM 输出空列表（默认 `[]`），不强制 DAG；spec 003 才做"并行研究"优化（ADR-0002 的 Send fan-out 是按 ResearchQuery 层 fan-out，不是按 PlanStep 层）。
- Resume payload 当前只支持 `"approve"` 与 `"edit: <text>"` 两种；不引入 `"reject"`（与 ADR-0003 "Alternatives Considered" 一致，reject 留给 UI 层单独处理）。
- Postgres 来自 `langfuse-deploy`（`localhost:5432`，DB=`postgres`，user/pass=`postgres`），复用 foundation spec 已验证的连接串。
- spec 002 完成后 research 子图仍为 stub（foundation FR-005 保留），所以 SC-003 中"走到 research 入口"以 Postgres checkpoint 出现 `node="research"` 为准，不以"图真的跑完 research"为准。

---

## Out of Scope

- 真实 web search 与 ReRank 评分（spec 003）
- Write 子图（outline / draft / critic / polish，spec 004）
- Streamlit UI 与 session_state 接线（spec 005）
- Langfuse `@observe` hook + TraceMetadata（spec 006）
- Plan 自动 accept 评分 / Plan quality score（Lesson 0007 dataset experiment，本 spec 不埋点）
- Plan step DAG 校验 / 循环依赖检测（plan schema 不做，未来 spec 才补）

---

## Dependencies

- spec 001（foundation）：包骨架、schemas、state、graph 拓扑、PostgresSaver、`auto_confirm_plan` 字段、`NotYetImplementedError` 异常类——本 spec 全部继承。
- `langfuse-deploy` Postgres（`localhost:5432`）：SC-004 跨进程 resume 验证的物理基础。
- Langfuse 2.x Python SDK（`langfuse>=2.0,<3.0`）：`get_prompt` / `compile` API 来自该 SDK；版本不匹配时 `compile()` 行为差异（2.0 引入）会破坏 FR-001。
- DashScope OpenAI 兼容端点（`https://dashscope.aliyuncs.com/compatible-mode/v1`）：FR-005 的 LLM 实例来自此端点。
- LangGraph 1.2+（已在 `pyproject.toml` 锁定）：`interrupt()` + `Command(resume=...)` 是 LangGraph 1.x 才稳定的 API。

---

## Notes

- spec 002 是 foundation 之后第一个真正"跑通数据"的 spec——之前所有逻辑都是 stub。它证明：(a) Langfuse Prompt Management 与 deep research 图的接线；(b) Postgres checkpointer 跨进程可用；(c) LangGraph 1.x `interrupt()` 模式在子图 + nested graph 上稳定。
- `confirm_plan_node` 接收 resume payload 时，把非 `"approve"` / `"edit:"` 的值默认为 approve——这是**有意的宽松策略**，避免 UI 误传未识别值时把图卡死。后续 spec 005 可在 UI 层做严格校验并显式报"未识别的 resume payload"。
- `plan_node` 内部 LLM 重试只做 1 次（不无限重试）——避免一次 plan 生成拖死整个研究 pipeline。1 次重试足以吸收 JSON 解析抖动；超出则属于上游 prompt 设计问题，应到 Langfuse UI 改 prompt 而非加重试。
- FR-007 的 interrupt payload 用 `model_dump(mode="json")` 而不是 `model_dump()`——因为 LangGraph 内部用 msgpack 序列化 interrupt payload，`HttpUrl` 等非 msgpack-native 字段（参见 schemas.py Source.url）需要 json mode 才能正确转 str。
- spec 002 不引入 `__interrupt__` payload 的 schema 校验（User 直接看到的是 plan list，不是 wrapper），但 graph.py 的 `run_research` 检测到 GraphInterrupt 时**不**把 interrupt payload 透传给 caller——caller 只看 `GraphInterrupt` 异常本身，payload 由 UI 层从 Postgres state 里读。
