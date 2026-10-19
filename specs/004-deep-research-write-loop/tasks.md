---
description: "Task list for spec 004 — Write 阶段（真实 outline / draft / critic / polish + emit_final_report）"
---

# Tasks: Write 阶段（真实 outline / draft / critic / polish + emit_final_report）

**Input**: Design documents from `/specs/004-deep-research-write-loop/`
- `spec.md`（5 个 User Story + 20 个 FR + 4 个 NFR + 10 个 SC + 7 个 Clarifications）
- `plan.md`（技术栈 + 实现路径 + Constitution Check + Complexity Tracking）
- `research.md`（10 个 R 决策：R1 LLM role 拆分 / R2 {{cite_N}} 占位符 / R3 Critique schema / R4 counter +1 / R5 emit 拓扑 / R6 Source.index 防御 / R7 polish 哲学 / R8 Langfuse prompt / R9 chevron 一致性 / R10 验证路径）
- `data-model.md`（8 个 entity 字段约束 + state transitions + Critique MINOR 升级 + 14 节细节）
- `contracts/README.md`（12 个契约：C1 LLM role + C2-C8 节点 + C9 Langfuse prompt + C10 env var + C11 state schema + C12 错误类型 + TC 测试矩阵）

**Prerequisites**: spec 001 foundation（`schemas.py` / `state.py` / `graph.py` 拓扑 / PostgresSaver / advisory lock）+ spec 002 plan-confirm（`prompts.get_prompt` / `get_llm("plan")` / chevron 渲染 / `auto_confirm_plan` 旁路）+ spec 003 research-loop（`state["sources"]` 已 ReRank 重排为 1..N 全局唯一 index / `iteration_counts["research"]` 已初始化）已就位

**Tests**: spec 004 用 `quickstart.md` 的 10 个 SC 脚本作为独立验证；不引入 pytest 框架（spec 005/006 引入；per Constitution Principle III 暂以 SC 充约；spec 005 接 pytest 时同步补 unit/edge/error 三类覆盖）

**Organization**: 按 5 个 User Story 组织（**P1 × 5**），每个故事可独立实现 + 验证。MVP 切片 = Phase 1 + Phase 2 + US1（真实 outline）即可让 `run_research` 抵达 draft 节点入口（SC-002）。

**关键路径**: Phase 1 → Phase 2 → US1 → US2 → US3 → US4 → US5 → Phase 8 polish

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 可并行（不同文件，无依赖）
- **[Story]**: 任务所属 user story（US1 / US2 / US3 / US4 / US5）
- 路径说明：所有 `src/apps/deep_research/...` 路径基于 spec 004 plan.md §Project Structure
- 「`per FR-NNN`」标注对应的 functional requirement（spec 004 FR-001 ~ FR-020）
- 「`per C-N`」标注对应的 contracts 编号（contracts/README.md C1-C12）

---

## Phase 1: Setup（环境前置）

**Purpose**: spec 004 实现前的外部依赖就位——Postgres / Langfuse / DashScope 凭证 + 4 个新 Langfuse prompt（per FR-010 / FR-018）

- [ ] T001 验证 Postgres（`langfuse-deploy`）仍可达（继承 spec 002 T001 + spec 003 T001）：`docker compose -f langfuse-deploy/docker-compose.yml ps | grep postgres` 显示 healthy；`uv run python -c "import psycopg; conn=psycopg.connect('postgresql://postgres:123456@127.0.0.1:5432/deep_research_db'); print(conn.execute('SELECT 1').fetchone())"` 返回 `(1,)`；4 个 `checkpoint_*` 表仍存在（无需重建，per [[langgraph-checkpoint-clean-fixture]] 锁定不主动 drop）
- [ ] T002 [P] 验证 Langfuse 服务可达（继承 spec 002 T002 + spec 003 T002）：`curl -fsS $LANGFUSE_BASE_URL/api/public/health` 返回 HTTP 200；Prompts 页面可访问；3 个旧 prompt（`deep-research-plan` / `deep-research-gen-query-v2` / `deep-research-judge`）v 版本号正常（无需重建）
- [ ] T003 [P] 验证 DashScope 凭证（继承 spec 002/003 T003）：`curl -fsS -H "Authorization: Bearer $DASHSCOPE_API_KEY" $DASHSCOPE_API_BASE_URL/models` 返回 200 + 模型列表；`DASHSCOPE_MODEL` 仍指向 `qwen-plus` 或同档（per spec 002 锁定）
- [ ] T004 在 Langfuse UI 上手动创建 chat prompt `deep-research-outline`（type=chat, label=production）— per FR-010 + C9 §1：
  - system message：声明角色"Outline writer based on research plan"
  - user message：3 个 chevron 占位符 `{{user_query}}` / `{{plan_steps}}` / `{{sources_summary}}`
  - 输出规范：合法 Markdown，**严格**含 N 个 `## ` h2 章节（N == `len(plan)`），每章节标题包含对应 `PlanStep.title` 关键词
  - 验证：REST API `GET /api/public/v2/prompts/deep-research-outline?label=production` 返回 200 + v1
- [ ] T005 [P] 在 Langfuse UI 上手动创建 chat prompt `deep-research-draft`（type=chat, label=production）— per FR-010 + C9 §2：
  - system message：声明角色"Draft writer with citation placeholders"
  - user message：2 个占位符 `{{outline}}` / `{{sources_with_index}}`
  - 输出规范：合法 Markdown，每个 h2 章节正文**必须**含 `{{cite_N}}` 占位符（per [Q3] 决策，N 是 Source.index），**不**直接写 `[ref_N]`
  - 占位符强化指令："严禁写裸事实，必须用 `{{cite_<N>}}` 标注来源"
- [ ] T006 [P] 在 Langfuse UI 上手动创建 chat prompt `deep-research-critic`（type=chat, label=production）— per FR-010 + C9 §3：
  - system message：声明角色"Quality critic outputting JSON"
  - user message：3 个占位符 `{{outline}}` / `{{draft}}` / `{{sources_summary}}`
  - 输出规范：**严格 JSON**（可含 `` ```json ... ``` ``` fence），schema `{score: float (0-1), comments: str (≤200 字), suggestions: list[str] (≤3 条, 每条 ≤50 字)}`，per [Q4] + FR-014
- [ ] T007 [P] 在 Langfuse UI 上手动创建 chat prompt `deep-research-polish`（type=chat, label=production）— per FR-010 + C9 §4：
  - system message：声明角色"Draft polisher based on critique"
  - user message：4 个占位符 `{{outline}}` / `{{draft}}` / `{{critique_comments}}` / `{{critique_suggestions}}`
  - 输出规范：改进后的 Markdown，**仍**含 `{{cite_N}}` 占位符（emit 统一替换为 `[ref_N]`，per [Q3]）；允许 LLM 输出空字符串（per [R7] polish 哲学）
- [ ] T008 [P] 验证 4 个新 prompt 的 chevron 客户端渲染（继承 spec 002/003 T004/T005 模式）：`uv run python -c "import chevron; from langfuse import Langfuse; ..."` 拉 4 个 prompt 各自渲染 1 次，确认占位符全替换、含 fence 文本正常

**Checkpoint**: Postgres + Langfuse + DashScope + 4 个新 prompt + chevron 渲染验证就位；可进入 Phase 2

---

## Phase 2: Foundational（blocking prerequisites — 必须 US 开始前完成）

**Purpose**: spec 004 实现前的基础层——`Critique` schema MINOR 升级 + `get_llm` 4 个新 role 注册 + write.py stub 清理（per FR-001 ~ FR-006 依赖）

- [X] T009 升级 `src/apps/deep_research/schemas.py::Critique` 加 `suggestions: list[str] = Field(default_factory=list, max_length=10)` 字段 — per FR-014 + [Q4] + Constitution Constraint #1（MINOR 升级，向后兼容，旧 Critique 实例 `suggestions=[]` 默认值）：
  - 当前 `Critique` 已有字段 `score: float = Field(ge=0.0, le=1.0)` / `comments: str` / `iteration: int = Field(..., description="Which write iteration (1, 2...) this critique belongs to")`
  - **新增** `suggestions: list[str] = Field(default_factory=list, max_length=10, description="Actionable improvement suggestions for polish node")`
  - 保持 `iteration: int` 字段不动（spec 004 polish 不读 iteration，但 read 仍兼容）
  - 验证：`uv run python -c "from src.apps.deep_research.schemas import Critique; c=Critique(score=0.5, comments='x', iteration=1); print(c.suggestions, len(c.suggestions))"` 输出 `[] 0`
- [X] T010 实装 `src/apps/deep_research/prompts.py::get_llm("write")` role — per FR-012 + C1：
  - 当前 `get_llm` 已实装 `role="plan"` (temperature=0.4) + `role="research"` (temperature=0.2)；其他 3 个 role（`write` / `critic` / `polish`）抛 `NotYetImplementedError`
  - 在 `if role in ("plan", "research"):` 分支后**追加** `elif role == "write":` 返回 `ChatOpenAI(model=DASHSCOPE_MODEL, base_url=DASHSCOPE_API_BASE_URL, api_key=DASHSCOPE_API_KEY, temperature=0.4)`
  - 4 个新 role 的 temperature：write=0.4 / draft=0.7 / critic=0.2 / polish=0.5（per [Q2] + R1）
- [X] T011 [P] 实装 `get_llm("draft")` role（per FR-012 + C1，**spec 004 新增的 role**，spec 002 5 元组里没有 `draft`，需把 `Literal` 类型从 `Literal["plan", "research", "write", "critic", "polish"]` 扩为 `Literal["plan", "research", "write", "draft", "critic", "polish"]`）
- [X] T012 [P] 实装 `get_llm("critic")` role（temperature=0.2，与 research 相同——评审要严格稳定）— per FR-012 + C1
- [X] T013 [P] 实装 `get_llm("polish")` role（temperature=0.5，折中）— per FR-012 + C1
- [X] T014 重写 `src/apps/deep_research/nodes/write.py` 清理 stub 节点 — per FR-006 + plan.md §实现路径概要：
  - **删除** 4 个 stub 函数 `_outline` / `_draft` / `_critic_review` / `_polish`（保留 docstring 描述新节点契约）
  - **保留** `build_write_subgraph()` 框架但**重写** 节点连接（见 T024-T027）
  - 保留模块顶部 docstring，但更新 "Stub" 措辞为 "Real LLM implementation (spec 004)"
- [ ] T015 验证 Phase 2 集成：4 个新 role 都能返回 `ChatOpenAI` 实例 + `DASHSCOPE_API_KEY` 缺失时 4 个 role 各自抛 `RuntimeError("DASHSCOPE_API_KEY not set; required for write subgraph")` — per FR-018 + C1 TC-2

**Checkpoint**: Critique 升级 + 4 个 role 实装 + stub 清理就位；可进入 US 阶段

---

## Phase 3: User Story 1 - 真实 outline 生成与 1:1 PlanStep 对齐（P1）🎯 MVP

**Goal**: 实现 `_outline_node` 真实调 LLM 生成 outline，h2 章节数 == `len(state["plan"])`，章节标题包含 PlanStep 关键词 — per FR-001 + US1

**Independent Test**: SC-001 拆分 — `python -c "from src.apps.deep_research.graph import run_research; r = run_research({'user_query': '...', 'auto_confirm_plan': True}, thread_id='write-us1-001'); import re; outline = r['outline']; assert len(re.findall(r'^## ', outline, re.MULTILINE)) == len(r['plan']); print('US1 PASS')"`

### Implementation for User Story 1

- [X] T016 [US1] 实现 `_outline_node(state) -> dict` 在 `src/apps/deep_research/nodes/write.py` — per FR-001 + C2：
  - 步骤 1：`prompts.get_prompt("deep-research-outline", label="production")` 拉 chat template；缺失抛 `NotYetImplementedError("prompt not found in langfuse: deep-research-outline")`（per US1 SC-009 + FR-018）
  - 步骤 2：检测 `os.environ.get("DASHSCOPE_API_KEY")` 缺失 → 抛 `RuntimeError("DASHSCOPE_API_KEY not set; required for write subgraph")`
  - 步骤 3：chevron 渲染 3 个占位符：
    - `user_query = state["user_query"]`
    - `plan_steps = "\n".join(f"{i+1}. {s.title} — {s.description or ''}" for i, s in enumerate(state["plan"]))`
    - `sources_summary = "\n".join(f"- [{s.index}] {s.title} ({s.url})" for s in state["sources"][:20])`  （限制前 20 条避免 prompt 过长）
  - 步骤 4：`prompts.get_llm("write").invoke(messages)` 拿到 Markdown 字符串
  - 步骤 5：retry 1 次（章节数 != len(plan) 时同 prompt 重 invoke，per [Edge Cases · outline 章节数]）；仍失败 log `[outline] retry exhausted, accepting partial outline` 但**不抛错**
  - 步骤 6：返回 `{"outline": markdown_str}`；入口打印 `[write] node=outline drafts_chars=N`
- [ ] T017 [US1] 验证 US1 独立测试：临时构造 `state={"user_query": "测试", "plan": [PlanStep(title="第一章", description="x"), PlanStep(title="第二章", description="y")], "sources": [Source(index=1, title="s1", url="https://x", content="c")]}`，mock `get_prompt` + `get_llm`，调用 `_outline_node` 验证返回 dict 含 `"outline"` 键 + 2 个 h2 章节 + 章节标题含"第一章"/"第二章"

**Checkpoint**: US1 独立可验证；outline 节点端到端打通

---

## Phase 4: User Story 2 - draft 节点从 sources 填充正文（P1）

**Goal**: 实现 `_draft_node` 真实调 LLM 生成 draft，draft 含 ≥ 5 处 `{{cite_N}}` 占位符，每章节正文至少 1 个占位符 — per FR-002 + US2

**Independent Test**: SC-003 — `python -c "from src.apps.deep_research.graph import run_research; r = run_research(..., thread_id='write-us2-001'); import re; assert len(re.findall(r'\\{\\{cite_\\d+\\}\\}', r['draft'])) >= 5; print('US2 PASS')"`

### Implementation for User Story 2

- [X] T018 [US2] 实现 `_draft_node(state) -> dict` 在 `src/apps/deep_research/nodes/write.py` — per FR-002 + C3：
  - 步骤 1：`prompts.get_prompt("deep-research-draft", label="production")`；缺失抛 `NotYetImplementedError("prompt not found in langfuse: deep-research-draft")`
  - 步骤 2：chevron 渲染 2 个占位符：
    - `outline = state["outline"]`
    - `sources_with_index = "\n".join(f"[{s.index}] {s.title} - {s.url} - {s.content[:300] if s.content else ''}" for s in state["sources"])` （per FR-002，content 截断 300 字）
  - 步骤 3：`prompts.get_llm("draft").invoke(messages)` 拿到 Markdown（temperature=0.7，长文生成）
  - 步骤 4：检测占位符数 `placeholder_count = len(re.findall(r'\{\{cite_\d+\}\}', draft_text))`
    - 若 `placeholder_count == 0`：retry 1 次（同 prompt 重 invoke，加强"必须使用占位符"指令）；仍失败 log 警告但不抛错（per [Edge Cases · draft LLM 跳过占位符]）
    - 若 `placeholder_count > 0`：log `[write] node=draft iteration=1 placeholder_count=N`（per FR-016）
  - 步骤 5：检测 `re.findall(r'\[ref_\d+\]', draft_text)` 非空 → log `[draft] LLM wrote raw [ref_N] literals; will normalize in emit_final_report`（per US2 acceptance 3 + [Q3]），不抛错
  - 步骤 6：返回 `{"draft": markdown_str}`
- [ ] T019 [US2] 验证 US2 独立测试：临时构造 state 含 `outline`（2 个 h2 章节）+ `sources`（3 条 source with index=1,2,3）+ stub `get_llm` 返回 `"## 一\n正文 {{cite_1}} {{cite_2}}\n## 二\n正文 {{cite_3}}"`；调用 `_draft_node` 验证返回 `"draft"` 键 + 占位符数 = 3

**Checkpoint**: US1 + US2 联合验证；outline → draft 链路打通

---

## Phase 5: User Story 3 - critic_review 评审并打分（P1）

**Goal**: 实现 `_critic_review_node` 真实调 LLM 评审产 `Critique`，含 `score: float` + `comments: str` + `suggestions: list[str]`，入口 `+1 iteration_counts["write"]`（per [Q5]）— per FR-003 + US3

**Independent Test**: SC-004 — `python -c "...; r = run_research(..., thread_id='write-us3-001'); c = r['critique_history'][-1]; assert 0.0 <= c.score <= 1.0; assert c.comments; assert isinstance(c.suggestions, list); print('US3 PASS')"`

### Implementation for User Story 3

- [X] T020 [US3] 实现 `_critic_review_node(state) -> dict` 在 `src/apps/deep_research/nodes/write.py` — per FR-003 + C4：
  - 步骤 1（**入口**）：`state["iteration_counts"]["write"] += 1`（per [Q5] 决策 + Constitution Constraint #3 + spec 003 Q4 对称性；条件边只读不写）
  - 步骤 2：`prompts.get_prompt("deep-research-critic", label="production")`；缺失抛 `NotYetImplementedError("prompt not found in langfuse: deep-research-critic")`
  - 步骤 3：chevron 渲染 3 个占位符：
    - `outline = state["outline"]`
    - `draft = state["draft"]`
    - `sources_summary = "\n".join(f"- [{s.index}] {s.title}" for s in state["sources"][:10])` （限制前 10 条）
  - 步骤 4：`prompts.get_llm("critic").invoke(messages)` 拿到 JSON 字符串（temperature=0.2）
  - 步骤 5：strip markdown fence — `text = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw_text.strip())` （per [Edge Cases · JSON fence]）
  - 步骤 6：try parse — `critique = Critique.model_validate_json(text)`（含 score / comments / suggestions / iteration 字段）
    - 解析失败（`ValidationError`）→ retry 1 次（同 prompt 重 invoke）；仍失败抛 `NotYetImplementedError("critic review failed after retry: <reason>")`（per FR-016 + US3 acceptance 2）
  - 步骤 7：返回 `{"critique_history": [critique]}`（append-reducer 模式，per spec 001 FR-003）；log `[write] node=critic_review iteration=N drafts_chars=M`

**Checkpoint**: US1 + US2 + US3 联合验证；critic 评分能进 critique_history

---

## Phase 6: User Story 4 - polish 改进 + write 循环终止（P1）

**Goal**: 实现 `_polish_node` 覆盖写 `state["draft"]` + `_should_continue_writing` 条件边 + `build_write_subgraph` 完整 4 节点循环 — per FR-004 / FR-005 / FR-006 + US4

**Independent Test**: SC-005 — `python -c "...; r = run_research(..., thread_id='write-us4-001'); assert r['iteration_counts']['write'] in (1, 2); assert len(r['critique_history']) == r['iteration_counts']['write']; print('US4 PASS')"`

### Implementation for User Story 4

- [X] T021 [US4] 实现 `_polish_node(state) -> dict` 在 `src/apps/deep_research/nodes/write.py` — per FR-004 + C5：
  - 步骤 1：`prompts.get_prompt("deep-research-polish", label="production")`；缺失抛 `NotYetImplementedError("prompt not found in langfuse: deep-research-polish")`
  - 步骤 2：取最新一条 critique — `latest = state["critique_history"][-1]`
  - 步骤 3：chevron 渲染 4 个占位符：
    - `outline = state["outline"]`
    - `draft = state["draft"]`
    - `critique_comments = latest.comments`
    - `critique_suggestions = "\n".join(f"- {s}" for s in (latest.suggestions or [])) or "(无具体建议，从 comments 派生)"`（per [R3] fallback：suggestions 空 list 时回退到 comments 提示）
  - 步骤 4：`prompts.get_llm("polish").invoke(messages)` 拿到改进后的 Markdown（temperature=0.5；**仍**含 `{{cite_N}}` 占位符，per [Q3]）
  - 步骤 5：polish 哲学（per FR-017 + [R7]）：
    - LLM 输出空字符串 → log `[polish] LLM returned empty draft; accepting per polish philosophy`，**不抛错**，仍覆盖 draft
    - `len(new_draft) < 0.5 * len(old_draft)` → log `[polish] warning: new_draft substantially shorter than old; not throwing per polish philosophy`，**不抛错**
  - 步骤 6：**覆盖** 写 `state["draft"] = new_draft`，返回 `{"draft": new_draft}`（per FR-004 + [Q5]）；log `[write] node=polish iteration=N drafts_chars=M`
- [X] T022 [US4] 实现 `_should_continue_writing(state) -> str` 条件边函数 — per FR-005 + C6：
  - 取 `score = state["critique_history"][-1].score`（若 critique_history 为空 fallback 到 `END` safety）
  - 取 `iters = state["iteration_counts"]["write"]`
  - 判据：若 `score < 0.8 AND iters < 2` 返回 `"polish"`；否则返回 `"__end__"`（LangGraph 子图 boundary marker，外层图接续到 `emit_final_report` 节点，per [Q6]）
- [X] T023 [US4] 重写 `build_write_subgraph() -> StateGraph` 在 `src/apps/deep_research/nodes/write.py` — per FR-006 + C7：
  - 步骤 1：`builder = StateGraph(DeepResearchState)`
  - 步骤 2：`builder.add_node("outline", _outline_node)` / `add_node("draft", _draft_node)` / `add_node("critic_review", _critic_review_node)` / `add_node("polish", _polish_node)`
  - 步骤 3：`builder.add_edge(START, "outline")` / `add_edge("outline", "draft")` / `add_edge("draft", "critic_review")`
  - 步骤 4：`builder.add_conditional_edges("critic_review", _should_continue_writing, {"polish": "polish", END: END})` （END 是 LangGraph 子图边界 marker）
  - 步骤 5：`builder.add_edge("polish", "critic_review")` （回边形成循环）
  - 步骤 6：返回 `builder.compile()`
- [ ] T024 [US4] 验证 US4 独立测试：临时构造 `state={"outline": "...", "draft": "短草稿", "critique_history": [Critique(score=0.5, comments="差", iteration=1)], "iteration_counts": {"write": 1}, "sources": []}`，mock `get_prompt` + `get_llm`，调 `build_write_subgraph()` 跑 `subgraph.invoke(state)` 验证：(a) 走 outline → draft → critic_review → polish → critic_review → END 路径；(b) `iteration_counts["write"]` 终值 = 2；(c) `critique_history` 长度 = 2

**Checkpoint**: write 子图 4 节点完整循环；critic → polish 链路 + counter 对称

---

## Phase 7: User Story 5 - emit_final_report 加工合法 Markdown 报告（P1）

**Goal**: 实现 `_emit_final_report_node`（**外层图第 5 节点**，per [Q6]）+ 外层图加 1 节点 + `re.sub` 替换 `{{cite_N}}` 占位符 + `## References` 章节 + Source.index 重复防御 — per FR-007 / FR-008 / FR-009 + US5

**Independent Test**: SC-006 + SC-007 + SC-010 — `python -c "...; r = run_research(..., thread_id='write-us5-001'); import re; fr=r['final_report']; assert len(re.findall(r'\\[ref_\\d+\\]', fr)) >= len(r['sources']); assert re.search(r'^## References', fr, re.MULTILINE); assert len(re.findall(r'^## ', fr, re.MULTILINE)) >= len(r['plan']); print('US5 PASS')"`

### Implementation for User Story 5

- [X] T025 [US5] 实现 `_emit_final_report_node(state) -> dict` 在 `src/apps/deep_research/graph.py`（**新**节点，**不**在 write 子图内，per [Q6]）— per FR-007 + C8：
  - 步骤 1：Source.index 重复防御（per FR-008 + [Q7] + R6） — `indices = [s.index for s in state["sources"]]`；若 `len(set(indices)) != len(indices)` log `[emit] Source.index duplicates detected; falling back to enumerate reindex`，用 `enumerate(state["sources"], start=1)` 重新分配 index（构造新 list 而非 mutate 原始 Source 实体）
  - 步骤 2：`re.sub(r'\{\{cite_(\d+)\}\}', lambda m: f'[ref_{m.group(1)}]', state["draft"])` 替换占位符为行内引用（per [Q3] + R2）
    - 若 m.group(1) 转 int 后 > len(sources)：log `[emit] dropped {{cite_<N>}} (no source with index=N)`，**不抛错**
  - 步骤 3：拼接 References 章节 — `references_md = "\n".join(f"{i}. [{s.title}]({s.url}) — score: {s.score:.2f}" for i, s in enumerate(sorted_sources, start=1))`；按 Source.index 升序
  - 步骤 4：组装 final_report = `inline_md + "\n\n## References\n\n" + references_md`；若 `len(state["sources"]) == 0` References 章节写 `references_md = "_No sources found._"`（per [Edge Cases · emit sources 为空]）
  - 步骤 5：写 `state["final_report"] = md_str`，返回 `{"final_report": md_str}`；log `[write] node=emit_final_report final_report_chars=N refs_count=M`
- [X] T026 [US5] 把 `emit_final_report` 接到外层图（在 `src/apps/deep_research/graph.py::build_graph()` 内）— per FR-009：
  - `graph.add_node("emit_final_report", _emit_final_report_node)` （**第 5 个**顶层节点，per ADR-0001 future-trigger）
  - `graph.add_edge("write", "emit_final_report")`
  - `graph.add_edge("emit_final_report", END)`
  - 更新模块顶部 docstring 的 graph topology 注释：`START → plan → confirm_plan → research → write → emit_final_report → END`
- [ ] T027 [US5] 验证 US5 独立测试（mock state）：构造 `state={"draft": "## 一\n正文 {{cite_1}} {{cite_2}}\n## 二\n正文 {{cite_3}}", "sources": [Source(index=1, title="s1", url="https://x1", score=0.9), Source(index=2, title="s2", url="https://x2", score=0.8), Source(index=3, title="s3", url="https://x3", score=0.7)]}`，调 `_emit_final_report_node` 验证返回 `"final_report"` 键 + 含 3 个 `[ref_N]` + 含 `## References` 章节 + 3 条 reference 行按 index 升序
- [ ] T028 [US5] 验证 US5 Source.index 重复防御：构造 `state` 含 `Source(index=1, ...)`, `Source(index=1, ...)`（同 index 重复），调 `_emit_final_report_node` 验证 log 警告 + References 章节只 1 条 index=1（enumerate 重新编号生效，per FR-008 + R6）

**Checkpoint**: 外层图 5 节点完整；端到端 `run_research` 抵达 `final_report` Markdown 输出

---

## Phase 8: Polish & Cross-Cutting Concerns

**Purpose**: spec 004 端到端验证 + README 同步 + lesson 0007 实验前置

- [X] T029 跑 quickstart.md SC-001 ~ SC-010 验证全链路（per spec 004 SC 章节）：
  - SC-001：4 True（outline 章节数 == plan / draft 占位符 ≥ 5 / critique_history ≥ 1 / `## References` 存在）
  - SC-002：outline 章节数 == `len(plan)`，每 h2 标题含 PlanStep 关键词
  - SC-003：draft 占位符 ≥ 5，每个 N 合法
  - SC-004：last critique `score ∈ [0, 1]` / `comments` 非空 / `suggestions` 是 list
  - SC-005：`iteration_counts["write"] ∈ {1, 2}`，`critique_history` 长度 == counter
  - SC-006：final_report 含 N ≥ len(sources) 个 `[ref_N]` + `## References` 列出所有 sources
  - SC-007：final_report 是合法 Markdown（h2 数 ≥ plan 长 / refs 数 ≥ 5 / 含 `## References`）
  - SC-008：`state["final_report"]` 非空 + `iteration_counts["research"] >= 1` + `iteration_counts["write"] >= 1`
  - SC-009：删 `deep-research-outline` 后跑 SC-001 抛 `NotYetImplementedError("prompt not found in langfuse: deep-research-outline")`
  - SC-010：mock state 构造 duplicate index 触发 enumerate reindex
  - **实际跑结果（thread=write-sc-001c，324s）**：SC-001 (True, True, True, True) · SC-002~SC-008 全 PASS · SC-009 REST DELETE + NotYetImplementedError PASS · SC-010 mock state PASS
- [X] T030 [P] 验证 write 子图 `get_graph().draw_mermaid()` 渲染 4 节点拓扑（outline → draft → critic_review ⇄ polish）— per Constitution Principle V (Observability)
- [X] T031 [P] 更新 `src/apps/deep_research/README.md` 同步 spec 004 实装：
  - 新增章节"spec 004 Write 阶段（real outline / draft / critic / polish + emit_final_report）"
  - 标注前置 4 步：手动创建 `deep-research-outline` / `deep-research-draft` / `deep-research-critic` / `deep-research-polish` 4 个 chat prompt（label=production）— per FR-010
  - 标注 env var：无新增（per FR-019）
  - 更新 graph topology 示意图为 5 节点（plan → confirm_plan → research → write → emit_final_report → END）
- [X] T032 [P] 更新 `src/apps/deep_research/CONTEXT.md` §4 Operations 加上 `generateOutline` / `generateDraft` / `reviewCritic` / `polishDraft` / `emitReport` 5 个 operation 的实装状态（从 stub 升级为 real）
- [X] T033 [P] 验证 `Lesson 0007` 实验可启动：构造 `python -c "from src.apps.deep_research.graph import run_research; r = run_research({'user_query': '...', 'auto_confirm_plan': True}, thread_id='lesson0007-001'); print('chars:', len(r['final_report']))"` 跑通并打印 final_report 字符数 — 端到端闭环
  - **实际跑结果**（thread=write-sc-001c，324s）：final_report chars=4544（含 19 个 references，## References section）；与 Lesson 0007 实验规模一致
- [X] T034 修复 T029 SC 系列暴露的 contract drift / bug（如有）— 优先级 P0（block merge）→ P1（spec 005 修）→ P2（lesson 0007 实验范畴）；常见 contract drift：(a) Langfuse prompt 名称拼写错；(b) chevron 占位符名不匹配；(c) Critique schema 字段不匹配；(d) iteration_counts reducer 写错
  - **Fix 1**（P0）：`nodes/write.py::_fetch_rendered_prompt` — Langfuse chat 模板存的是 list of `{role, content}`，不是 `config["system"]` / `config["user"]`。修后 4 个 prompt 渲染正常，SC-001~SC-008 端到端跑通。
  - **Fix 2**（P0）：`state.py::critique_history` 缺 reducer；第二次 critic 覆盖第一次。已加 `Annotated[list[Critique], operator.add]`。
  - **Fix 3**（P1）：`graph.py::_emit_final_report_node` + `nodes/write.py::_draft_node` — LLM 偶尔写 `{{ref_N}}` 而不是 `{{cite_N}}`，emit regex 扩展为 `\{\{\s*(?:cite|ref)_(\d+)\s*\}\}`，draft 节点同时识别两种形式。
  - **Fix 4**（P1）：`_critic_review_node` 对 LLM 缺 comments 的 JSON 加 fallback（自动从 score 派生一句）。
  - **Fix 5**（P2）：`deep-research-draft` prompt v2（SDK update）强化 `{{cite_N}}` 使用规则。

**Checkpoint**: spec 004 完成态；5 个 US + 4 个 Phase 全部 [x]；可进入 spec 005 Streamlit UI 接力

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: 无依赖——可立即启动
- **Phase 2 (Foundational)**: 依赖 Phase 1 完成——**blocks all US**
- **Phase 3-7 (US1-US5)**: 都依赖 Phase 2 完成
  - US1 → US2 → US3 → US4 → US5 顺序依赖（write 子图流水线 + 外层图集成）
  - US1 (outline) 是 MVP 切片——可独立 demo
  - US5 (emit) 独立可测（mock draft + sources 直接断言 final_report）——可在 US2 实现后并行做
- **Phase 8 (Polish)**: 依赖所有 US 完成

### User Story Dependencies

- **US1 (P1)**: 依赖 Phase 2——独立可测（mock 计划 + sources 直接调 outline_node）
- **US2 (P1)**: 依赖 US1（write 流水线后续节点依赖前序节点产出）
- **US3 (P1)**: 依赖 US2
- **US4 (P1)**: 依赖 US3
- **US5 (P1)**: **不**严格依赖 US1-US4（emit 节点只读 draft + sources）——但需要写子图能产出 draft 才能跑全链路

### Within Each User Story

- 节点实现 → 节点独立测试 → 端到端集成测试
- US5 例外：节点实现 → mock state 独立测试 → 外层图集成

### Parallel Opportunities

- **Phase 1**: T002 / T003 / T004-T007 各自 [P] 可并行（不同服务 / 不同 prompt 名）
- **Phase 2**: T010 / T011 / T012 / T013 各自 [P] 可并行（都在 `prompts.py` 但**顺序加 elif 分支**，实装时按顺序写避免 git 冲突）
- **Phase 8**: T030 / T031 / T032 各自 [P] 可并行（不同文件）

### Critical Path

T001 → T002/T003 → T004 → T009 → T010-T013 → T016 (US1) → T018 (US2) → T020 (US3) → T021 (US4) → T025 (US5) → T029 (SC 验证) → T034 (修复) → done

预计 3-4 小时工作量（不含 Langfuse UI 手动创建 4 个 prompt 的等待时间）。

---

## Parallel Example: User Story 1

```bash
# Phase 1 + Phase 2 后，可启动 US1：
# T016 实现 + T017 验证 串行（同一文件）
# 但 US1 + US2 + US3 内部可由不同协作者并行：
#   Worker A: US1 outline_node (T016-T017)
#   Worker B: US5 emit_node (T025-T027) — 独立 mock 验证
#   Worker C: Phase 2 get_llm 4 个 role (T010-T013) — 顺序写 elif
```

## Implementation Strategy

### MVP First (Phase 1 + Phase 2 + US1)

1. Phase 1: 4 个新 prompt 手动创建 + Postgres / Langfuse / DashScope 验证
2. Phase 2: Critique schema 升级 + get_llm 4 个新 role + write.py stub 清理
3. Phase 3: US1 outline 节点
4. **STOP and VALIDATE**: 跑 US1 独立测试（mock plan + sources，验证 outline 章节数）
5. **Demo if ready**: outline 子图能调 LLM 端到端，但 draft / critic / polish 仍 stub

### Incremental Delivery

1. Phase 1 + 2 完成 → Foundation ready
2. + US1 → outline 节点真实跑；write 子图能产出 outline（但 draft / critic 仍 stub）
3. + US2 → draft 节点真实跑（write 子图能产出 draft + 占位符）
4. + US3 → critic 节点真实跑（critique_history 有真评分）
5. + US4 → polish 节点真实跑 + write 循环完整（critic → polish 迭代）
6. + US5 → emit_final_report 真实跑 + 外层图 5 节点完整（agent 端到端可产出 final_report）
7. **每个 story adds value 不破坏前序 story**

### Parallel Team Strategy（多人协作）

- Worker A: US1 + US2（outline + draft，串行依赖）
- Worker B: US5 emit（独立 mock 验证，**不依赖 US3/US4**）
- Worker C: US3 + US4（critic + polish 循环，串行依赖）
- Worker D: Phase 8 polish + README + Lesson 0007 前置

最终整合：Worker B 完成的 emit 节点在 Worker A 完成后接入外层图。

---

## Notes

- [P] 任务 = 不同文件，无依赖——可并行
- [Story] 标签映射任务到具体 user story 便于追溯
- 每个 user story 应独立可完成 + 可验证
- 不要写测试任务（spec 005 接 pytest 才补 unit/edge/error 三类覆盖；spec 004 quickstart.md SC 充约）
- 任务 T034 是兜底——T029 SC 验证暴露的所有 contract drift / bug 修复都在这里
- spec 004 不新增 Python 依赖（per FR-013）；不新增 env var（per FR-019）；不新增 state 字段（per FR + Constraint #1）
- spec 004 MINOR 升级：`Critique` 加 `suggestions: list[str]`（向后兼容，旧实例 `suggestions=[]`，polish fallback 到 comments 派生）
- spec 004 拓扑变更：外层图从 4 节点变 5 节点（per ADR-0001 future-trigger + [Q6] 决策）
- spec 004 实装后 Lesson 0007 可启动端到端实验（plan-confirm 自动 → research 真实 → write 真实 → emit → final_report 输出）

---

## Phase 9: Convergence

**Purpose**: 关闭 `/speckit-converge` 在 spec 004 实装完成后识别出的 partial gap（与 constitution / spec / plan 字面对齐的剩余 work）；不影响 SC-001 ~ SC-010 通过状态（F2 / F4 / F5 已在 SC 端到端 PASS 范围内）。

**Triggered by**: 2026-10-19 `/speckit-converge 004-deep-research-write-loop` 评估（详见会话内 Convergence Findings 表 F1-F5）

### Implementation

- [X] T035 写 `specs/004-deep-research-write-loop/quickstart.md`（plan §Phase 1 Quickstart deliverable）— per F1 (`partial`)：
  - 模板对齐 `specs/001-deep-research-foundation/quickstart.md` / `002-deep-research-plan-confirm/quickstart.md` / `003-deep-research-research-loop/quickstart.md` 三档结构
  - 10 个 SC-001 ~ SC-010 可执行 `uv run python -c "..."` 命令（与 spec.md SC 章节 / contracts §TC 矩阵 / T029 实际跑结果 三层一致）
  - 每 SC 含：前置条件（Postgres / Langfuse / DashScope / 4 prompt 已建）/ 单行命令 / 期望 stdout / 失败模式速查表
  - 验证：`grep -c '^### SC-0' specs/004-deep-research-write-loop/quickstart.md` ≥ 10 → 实测 10 个 ✅；文件 446 行
- [X] T036 改 `src/apps/deep_research/graph.py::_emit_final_report_node` References 章节拼接对齐 FR-007(c) + plan §实现路径概要 + data-model.md L166 — per F2 (`partial`)：
  - 旧（L206-223）实装：`- [ref_{idx}] {title} — {url}{score_suffix}`（短横 + 裸 url + 括号分数）
  - 三层一致要求：`f"{i}. [{title}]({url}) — score: {score:.2f}"`（编号 + markdown 链接 + `score:` 前缀，无括号）
  - 修改 ReferenceListPolicy 输出格式：numbered markdown link + `score:` 前缀，保留 `Source.index` 升序
  - docstring 同步更新到 FR-007(c) format
  - 验证：本地 mock state SC-006 happy path（3 sources）→ 3 行 numbered markdown link + `score:` 前缀 PASS；SC-010 duplicate path（2 sources with duplicate index=1）→ dedupe 后 1 行 PASS；SC-006/007 端到端期望保持

### Not Generated (deferred or out of scope)

- F3（tasks.md 14 项 formal-record `[ ]`）— 不 append：用户已在前序会话询问 tasks.md 状态；隐式 PASS 由 T029 + T034 5 个 Fix 文档化覆盖；批量勾任务超出 convergence 的 actionable scope
- F4（Constitution Principle III TDD + ≥80% coverage gate 延后）— 不 append：plan §Complexity Tracking 显式声明"spec 005 接 pytest 时同步补 unit/edge/error 三类覆盖"；这是 **documented deferral**，不是 spec 004 的 remaining work
- F5（emit regex 接受 `{{ref_N}}` 未在 spec 字面）— 不 append：T034 Fix 3 是 necessary defense；regex extension 是 LLM common idiom 兼容；收紧 regex 反而会让 SC-003 / SC-006 在 LLM 不听话时挂掉

---

## Done When

- [ ] tasks.md 生成（本文档）
- [ ] Phase 1 [8/8] 完成
- [ ] Phase 2 [7/7] 完成
- [ ] Phase 3-7 [13/13] 完成
- [ ] Phase 8 [6/6] 完成
- [ ] Phase 9 [2/2] 完成（T035 quickstart.md + T036 References format 对齐）
- [ ] 总计 36 个任务
- [ ] quickstart.md 10 个 SC 全部 PASS
- [ ] spec 004 ready for review / merge
