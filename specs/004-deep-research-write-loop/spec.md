# Feature Specification: Write 阶段（真实 outline / draft / critic / polish + emit_final_report）

**Feature Branch**: `[004-deep-research-write-loop]`

**Created**: 2026-10-19

**Status**: Draft

**Input**: User description:

```
基于 001 + 003，规划 spec 004：write 阶段。
```

**Source of truth**: 本 spec 只覆盖 Phase 4（write 阶段：真实 outline 生成 → draft 填充 → critic 评审 → polish 改进 → emit_final_report 加工 `[ref_N]` 引用 + `## References` 章节）。在 spec 001 foundation / spec 002 plan-confirm / spec 003 research-loop 之上增量实现。设计意图与领域词汇见 `src/apps/deep_research/CONTEXT.md §2.3 Entities / §4 Operations / §6 Policies` 与 `docs/adr/0001-nested-subgraphs.md`（write 是嵌套子图）。

---

## Clarifications

### Session 2026-10-19

- **Q1**: spec 004 需要新增几个 Langfuse prompt？放在一个 `deep-research-write` prompt 里用 section 区分，还是拆成 4 个独立 prompt？→ A: 拆成 4 个独立 prompt（`deep-research-outline` / `deep-research-draft` / `deep-research-critic` / `deep-research-polish`）。理由：(a) 4 个 prompt 各自的输入 schema / 输出 schema / 占位符差异大，强行塞进一个 prompt 会让 Langfuse 模板编辑体验变差；(b) spec 002/003 已建立的"每个节点一个 prompt"模式（plan / gen_query / judge）一致性沿用；(c) Lesson 0007 实验需要单独 tune critic temperature 时只动一个 prompt；(d) 不需要新 prompt 时（如未来加 `summarize` 节点）也能保持"一节点一 prompt"模式。

- **Q2**: `outline` / `draft` / `critic` / `polish` 4 个节点的 LLM role 应该复用 `get_llm("plan")` / `get_llm("research")` 中的某一个，还是新建 4 个独立 role？→ A: 新建 4 个独立 role（`write` / `draft` / `critic` / `polish`），与 spec 002 已预留的 5 元 role（`plan / research / write / critic / polish`）兑现。理由：(a) spec 002 已写"role 五元组预留"，spec 004 是兑现 `write / critic / polish`，并新增 `draft`（spec 002 未单独预留，因为 spec 002 不写 draft 节点）；(b) 4 个 role temperature 各异：outline=0.4（结构化但需创造性）、draft=0.7（创造性高）、critic=0.2（评审要严格稳定）、polish=0.5（折中）；(c) Langfuse UI 上希望分别看 4 个 span 的 token 用量与延迟，便于 Lesson 0007 单独优化某一项；(d) 不复用 `plan` role 是因为 plan 是"分章节规划"（输出结构化 JSON），draft 是"长文本生成"（输出 Markdown 文本），语义差异大。**实装注意**：`get_llm("draft")` 是 spec 004 新增的 role（spec 002 5 元组里没有 `draft`），需要同步更新 `prompts.py::get_llm` 的 role 注册表 + `pyproject.toml` 等不变（只是代码内分支）。

- **Q3**: 草稿生成时（`draft` 节点）是否让 LLM **直接**在文本中插入 `[ref_N]` 引用？→ A: 不让 LLM 写 `[ref_N]`——draft 节点只生成"事实主张 + 引用占位符 `{{cite_N}}`"（N 是 Source.index），最后由 `emit_final_report` 统一替换为 `[ref_N]`。理由：(a) LLM 经常在 `[ref_N]` 处编造不存在的 N，或漏掉该写的地方；(b) Source 列表可能在 draft 之后又被 critic / polish 改动了 Source 集合，需要在 emit 时**最终**确定 `[ref_N]` 与 Source.index 的映射；(c) `{{cite_N}}` 占位符 + emit 时统一替换是 ADR-0003 未明示但 spec 002 / 003 已隐含的模式（plan → research 间用 placeholder）。**实装**：`draft_node` prompt 指示 LLM 在事实主张后用 `{{cite_<title_slug>}}` 或 `{{cite_<index>}}` 占位符（占位符渲染在 chevron 端——Langfuse prompt 内含 `{{sources_with_index}}` 块）；`emit_final_report_node` 用正则把所有 `{{cite_N}}` 替换为 `[ref_N]`，把没匹配上的 `{{cite_*}}` 视为 LLM 幻觉，丢弃。

- **Q4**: `critic_review` 节点的输出结构（JSON schema）应该长什么样？quality_score 是 0-1 单字段，还是多维度（clarity / accuracy / completeness / citation_coverage）？→ A: 单字段 `quality_score: float`（0-1）+ `comments: str`（≤ 200 字的简短评语）+ `suggestions: list[str]`（≤ 3 条改进建议，每条 ≤ 50 字）。理由：(a) spec 002 已确立"LLM-as-Judge 输出 schema 简洁可序列化"模式；(b) 多维度评分会让 critic → polish 的反馈链路变复杂（polish 要同时修 4 个维度 → 难以优先级排序）；(c) 简化 schema 让 Lesson 0007 实验能机械化统计 critic 分数分布；(d) `comments` + `suggestions` 足够 polish 节点理解改进方向。**实装**：Critique Pydantic 实体字段为 `score: float` + `comments: str` + `suggestions: list[str]`（per CONTEXT.md §2.3 + spec 001 schema，但 spec 001 当时只预留 `score` + `comments`；spec 004 必须升级 schema 加 `suggestions` 字段——这是 spec 001 schema 的 MINOR 升级，不破坏 foundation 兼容性）。

- **Q5**: `should_continue_writing` 条件边的 `iteration_counts["write"]` 由谁 `+1`？与 spec 003 research 子图一致在节点入口 `+1`，还是在条件边决策点 `+1`？→ A: 在 `critic_review` 节点入口 `+1`（与 spec 003 research 子图 `gen_query` 入口 `+1` 一致，per Constitution Principle I 状态 schema 不变约束 + spec 002 NFR-002 increment discipline）。理由：(a) Constitution Constraint #3 已锁定"start a new round"语义——节点入口 `+1`，条件边只读不写；(b) write 循环与 research 循环结构对称（critic → polish ↔ gen_query → websearch × N → eval_search），对称决策便于 future debug；(d) [Edge Cases · counter +1 时机] 一致性。

- **Q6**: `emit_final_report` 是写在 write 子图内部末尾（紧跟 polish 后），还是写到外层图（write 子图结束后由 outer graph 一个新节点处理）？→ A: 写在 write 子图**外部**——作为 outer graph 的第 5 个节点（`emit_final_report` 节点），紧接 `write` 子图之后。理由：(a) `emit_final_report` 不需要循环（一次性 deterministic transformation），不属于"write 循环"语义；(b) outer graph 当前已有 `plan / confirm_plan / research / write` 4 个节点，加 `emit_final_report` 为第 5 个顶层节点符合 ADR-0001 "如果外层再加节点考虑平铺"的 future-trigger；(c) `emit_final_report` 不在 write 子图内循环里意味着它只跑一次（即使 write 循环跑了 2 轮，emit 也只跑 1 次），避免重复加工；(d) 单独节点让 emit 逻辑可独立测试（mock draft + sources 直接断言 final_report 字段）。**实装**：outer graph 加 `graph.add_node("emit_final_report", _emit_final_report_node)` + `graph.add_edge("write", "emit_final_report")` + `graph.add_edge("emit_final_report", END)`。

- **Q7**: `emit_final_report` 加工 `[ref_N]` 时，N 与 `Source.index` 的映射规则？spec 003 Q6 已规定 Source.index 在 eval_search_node 重编号为 1..N 全局唯一——spec 004 是否直接复用这个 index？→ A: 直接复用 `Source.index`（1..N 全局唯一，per spec 003 Q6）。理由：(a) spec 003 已经把 Source.index 标准化为"final report references 章节位置"语义，spec 004 复用即一致；(b) emit_final_report 不需要重新编号（research 子图已统一处理）；(c) 如果 spec 003 的 Source 过滤（ReRank score < 0.3 丢弃）在 write 子图开始时已经生效（research 退出 → write 进入），write 端拿到的 sources 就是 final 集合。**实装注意**：spec 004 write 端要校验 `state["sources"]` 中所有 `Source.index` 唯一（`len(set(s.index for s in sources)) == len(sources)`），不一致时 log 但不抛错（去重 fallback：用 `enumerate` 重编号）。

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 真实 outline 生成与 1:1 PlanStep 对齐（P1）

作为 **deep research agent 的 Editor**，我能基于 `state["plan"]` + `state["sources"]` 真实调 LLM 生成报告 outline（Markdown 大纲），outline 的每个 h2 章节与 `PlanStep.title` **1:1 对应**——证明 Editor 的章节规划能力已端到端打通，报告骨架与研究方案严格对齐。

**Why this priority**：P1，spec 004 的核心交付物之一。foundation 的 stub 只能产出 `"<stub outline>"` 裸字符串，没有结构化的章节；write 子图的"骨架→内容→评审→润色"链路需要 outline 是合法 Markdown 才能往下走。

**Independent Test**：`python -c "from src.apps.deep_research.graph import run_research; r = run_research({'user_query': '...', 'auto_confirm_plan': True}, thread_id='write-outline-001'); import re; outline = r['outline']; print(len(re.findall(r'^## ', outline, re.MULTILINE)), len(r['plan']))"` 返回两者相等的整数。

**Acceptance Scenarios**：

1. **Given** Langfuse 上存在 `deep-research-outline` prompt（label="production"）且所有 DashScope 凭证就位，**When** `run_research(...)` 跑过 plan → confirm_plan → research 子图抵达 write 子图入口时，**Then** `_outline_node` 调 `prompts.get_prompt("deep-research-outline", label="production").compile(...)` 拉 chat template，渲染（chevron 客户端）传入 `user_query` / `plan`（join step titles + targets）/ `sources`（join titles + urls），调 `prompts.get_llm("write").invoke(...)` 拿到 Markdown 字符串，写 `state["outline"]`。`outline` 含 N 个 h2 章节（`N == len(state["plan"])`）。
2. **Given** outline 生成完成，**When** 检查 Postgres `checkpoints` 表对应 thread_id 最新 state 快照，**Then** `state["outline"]` 是非空字符串，h2 章节数 == `len(state["plan"])`，且每个 h2 标题包含对应 `PlanStep.title` 的关键词（substring match 算通过）。
3. **Given** Langfuse `deep-research-outline` prompt 不存在，**When** `_outline_node` 执行，**Then** `prompts.get_prompt(...)` 抛 `NotYetImplementedError("prompt not found in langfuse: deep-research-outline")`（per spec 002 FR-007 + FR-018 一致性）——上抛到 `run_research`，由 UI（spec 005）显示"prompt 缺失"提示。

---

### User Story 2 - draft 节点从 sources 填充正文（P1）

作为 **Editor**，我能基于 outline + sources 真实调 LLM 生成 draft，draft 每个章节引用对应 `PlanStep` 的 sources，正文行内用 `{{cite_N}}` 占位符（不直接写 `[ref_N]`）——证明 Editor 的内容填充能力已端到端打通，且引用由 emit_final_report 统一加工（避免 LLM 幻觉 N 编号）。

**Why this priority**：P1，write 子图的核心产物。如果 draft 不能从 sources 填充内容，spec 004 等于空架子。

**Independent Test**：`python -c "from src.apps.deep_research.graph import run_research; r = run_research(..., thread_id='write-draft-001'); import re; print(len(re.findall(r'\\{\\{cite_\\d+\\}\\}', r['draft'])))"` 返回 ≥ 5（即 draft 内至少 5 处 `{{cite_N}}` 占位符）。

**Acceptance Scenarios**：

1. **Given** `state["outline"]` 与 `state["sources"]` 都已填充，**When** `_draft_node` 执行，**Then** 拉 `deep-research-draft` prompt，渲染传入 `outline` / `sources_with_index`（join 每条 source 的 `index: title - url - content`），调 `prompts.get_llm("draft").invoke(...)` 拿到 Markdown 字符串，写 `state["draft"]`。draft 每个 h2 章节与 outline 一致；每个章节正文至少 1 个 `{{cite_N}}` 占位符（per [Q3] 决策）。
2. **Given** draft 生成完成，**When** 检查 `state["draft"]`，**Then** 总 `{{cite_N}}` 占位符数 ≥ `len(state["sources"])`（每个 source 至少被引用一次，或不引用但占位符总数仍 ≥ 5——见 SC-003）；draft 文本合法（不含未闭合的代码块 / 不平衡的 Markdown 括号）。
3. **Given** LLM 在 draft 中直接写出 `[ref_N]` 而非 `{{cite_N}}`（prompt 偶尔不听话），**When** `_draft_node` 检测到 `re.findall(r'\[ref_\d+\]', draft_text)` 非空，**Then** log 警告 `[draft] LLM wrote raw [ref_N] literals; will normalize in emit_final_report` 但**不抛错**——emit_final_report 会把 `[ref_N]` 也视为合法引用（per [Q3] 实现）。

---

### User Story 3 - critic_review 评审并打分（P1）

作为 **Critic**，我能基于 draft 真实调 LLM 评审产出 `Critique`（`score: float` + `comments: str` + `suggestions: list[str]`），`score` ∈ [0.0, 1.0]，critic score 写 `state["critique_history"]`（append-reducer 模式）——证明 LLM-as-Judge 评审能力已端到端打通，为 critic → polish 改进循环提供评分依据。

**Why this priority**：P1，write 循环的"评分-改进"链路核心。没有 critic score，polish 节点没法收敛。

**Acceptance Scenarios**：

1. **Given** `state["draft"]` 已填充，**When** `_critic_review_node` 执行，**Then** 拉 `deep-research-critic` prompt，渲染传入 `outline` / `draft` / `sources_summary`，调 `prompts.get_llm("critic").invoke(...)` 拿到 JSON，解析为 `Critique`（per [Q4] schema：`score` + `comments` + `suggestions`），写 `state["critique_history"]`（list 追加 1 条）。
2. **Given** critic 输出 JSON 解析失败（schema 不匹配或字段缺失），**When** `_critic_review_node` 检测到 `Critique.model_validate_json(...)` 抛 `ValidationError`，**Then** 重试 1 次（同 prompt 重 invoke），仍失败抛 `NotYetImplementedError("critic review failed after retry: <reason>")`。retry 计数写入日志（`print(f"[critic] retry attempt={N}")`，per spec 003 一致性模式）。
3. **Given** critic score < 0.8 且 `iteration_counts["write"] < 2`，**When** `should_continue_writing(state)` 条件边决策，**Then** 返回 `"polish"`（继续迭代）；反之 score ≥ 0.8 OR `iteration_counts["write"] >= 2` 时返回 `"emit_final_report"` 路径（write 子图退出到 outer emit_final_report 节点）。注意：`emit_final_report` 是 outer graph 节点（per [Q6]），write 子图退出用 `END` marker，外层图接续到 emit_final_report 节点。

---

### User Story 4 - polish 改进 + write 循环终止（P1）

作为 **Editor**，我能基于 draft + critique 真实调 LLM 产出 `PolishedDraft`，写 `state["draft"]`（**覆盖**而非追加，per spec 001 状态 schema — critique_history 才是 list append，draft 是 str 文本字段），然后 `critique_history` 追加 1 条新 Critique（开始下一轮评审）——证明 critic → polish → critic 循环能持续改进 draft 质量。

**Why this priority**：P1，write 阶段的"自我否定"机制核心。没有 polish，write 是一次性产物（没有改进空间）。

**Independent Test**：`run_research(...)` 跑通后 `len(state["critique_history"]) >= 1`（至少一轮 polish）或 `state["critique_history"][-1].score >= 0.8`（critic 满意不需 polish）。

**Acceptance Scenarios**：

1. **Given** critic 评分完成且 `iteration_counts["write"] < 2`，**When** `_polish_node` 执行，**Then** 拉 `deep-research-polish` prompt，渲染传入 `outline` / `draft` / `critique`（最新一条 Critique 的 `comments` + `suggestions`），调 `prompts.get_llm("polish").invoke(...)` 拿到改进后的 Markdown 文本（仍含 `{{cite_N}}` 占位符，per [Q3]），**覆盖**写 `state["draft"]`。critique_history **不追加**新 Critique（polish 不评审自己——polish 节点的产物回到 critic_review 节点再评一次）。
2. **Given** polish 完成，**When** LangGraph 走 `polish → critic_review` 回边，**Then** `_critic_review_node` 入口 `iteration_counts["write"] += 1`（per [Q5] 决策），再次评审 polish 后的 draft，把新 Critique 追加到 `critique_history`。
3. **Given** 第 2 轮 critic 评分 ≥ `0.8` OR `iteration_counts["write"] >= 2`，**When** `should_continue_writing(state)` 决策，**Then** write 子图退出（`END` marker），外层图走到 `emit_final_report` 节点。
4. **Given** write 子图连续 2 轮 polish 后 critic score 仍 < 0.5（极少但可能），**When** 退出条件触发（`iteration_counts["write"] >= 2`），**Then** **不**抛错——write 阶段的设计哲学是"尽力而为"，低分 draft 也交付（per ADR-0005 "Postgres 持久化永不丢失"哲学一致）；`final_report` 仍会被 emit 加工，但 reviewer 能在 `critique_history` 看到 score 低的事实。

---

### User Story 5 - emit_final_report 加工合法 Markdown 报告（P1）

作为 **deep research agent 的系统出口**，我能在 write 子图退出后由 `emit_final_report` 节点把 `state["draft"]` + `state["sources"]` 加工成 `FinalReport`（合法 Markdown，含 `[ref_N]` 行内引用 + `## References` 章节），写 `state["final_report"]`——证明 deep research agent 端到端输出可读报告，UI（spec 005）/ Lesson 0007 实验可直接消费。

**Why this priority**：P1，spec 004 的对外契约。没有 emit，agent 只能产出无引用的草稿（不可消费）。

**Independent Test**：`python -c "from src.apps.deep_research.graph import run_research; r = run_research(..., thread_id='write-emit-001'); import re; fr = r['final_report']; print(len(re.findall(r'\\[ref_\\d+\\]', fr)), bool(re.search(r'^## References', fr, re.MULTILINE)))"` 返回 `(≥ 5, True)`。

**Acceptance Scenarios**：

1. **Given** write 子图退出，`state["draft"]` 含 N 个 `{{cite_K}}` 占位符且 `state["sources"]` 已 ReRank 重排为 1..M 全局唯一 index，**When** `_emit_final_report_node(state)` 执行，**Then**：(a) 用 `re.sub(r'\\{\\{cite_(\\d+)\\}\\}', lambda m: f'[ref_{m.group(1)}]', draft_text)` 替换占位符为行内引用；(c) 拼接 `## References\n\n` + 每条 source 的 `1. [title](url) — score: X.XX\n` 行（按 index 升序），写 `state["final_report"]`。最终 `state["final_report"]` 是合法 Markdown。
2. **Given** `emit_final_report` 加工完成，**When** 检查 `state["final_report"]`，**Then**：(a) 全文 ≥ `len(state["sources"]) + 1` 个 `[ref_N]`（每条 source 至少 1 次引用；不足时 log 警告但不抛错）；(b) 末尾 `## References` 章节存在；(c) References 章节列出所有 `state["sources"]`（按 index 升序，含 title / url / score 三字段，per ReferenceListPolicy）。
3. **Given** `state["sources"]` 中有 `Source.index` 重复（违反 spec 003 Q6 保证），**When** `_emit_final_report_node` 检测到 `len(set(s.index for s in sources)) != len(sources)`，**Then** log 警告 `[emit] Source.index duplicates detected; falling back to enumerate reindex`，用 `enumerate(sources, start=1)` 重新编号后 emit——保证 References 章节无重复条目。

---

### Edge Cases

- **outline 章节数与 PlanStep 不一致**：`_outline_node` 在 LLM 输出的 Markdown 里数 `^## ` 章节数，若 ≠ `len(state["plan"])`，重试 1 次（同 prompt 重 invoke）。仍失败 log 警告但**不抛错**——outline 是"参考"而非"硬约束"，后续 draft / polish 节点可以自由发挥。`final_report` 的 h2 数可能与 PlanStep 不一致（用户接受这一可能性）。
- **draft LLM 跳过占位符直接写裸事实**（prompt 不听话）：`_draft_node` 在重试前先 log `[draft] attempt=N placeholder_count=M`——若 M=0，重试 1 次并把 prompt 的"必须使用占位符"指令加强（如"严禁写裸事实，必须用 `{{cite_N}}` 标注来源"）。仍失败 log 警告但不抛错——emit_final_report 会把没占位符的事实作为"无引用主张"保留（ReferenceListPolicy 不强制要求所有事实有引用）。
- **critic score 越界（< 0 或 > 1）**：`Critique` Pydantic schema 已有 `Field(ge=0.0, le=1.0)` 校验（per spec 001 schema 设计），越界直接抛 `ValidationError` 触发重试路径（per FR-016 一致性）。
- **LLM 在 critique JSON 里夹带 markdown fence（````json ... ````）**：`_critic_review_node` 在 `model_validate_json` 之前先 strip 掉 fence（`re.sub(r'^```(?:json)?\s*|\s*```$', '', text.strip())`），再解析。无法 strip 后仍无法解析 → 重试路径。
- **polish 后 draft 比 polish 前短 50% 以上**：`_polish_node` 在覆盖前先比 `len(new_draft) < 0.5 * len(old_draft)`——若缩减过大，log 警告但**不抛错**，仍覆盖（polish 是"尽力改进"哲学，可能大幅缩短冗余内容）。
- **`DASHSCOPE_API_KEY` 缺失**：write 阶段所有 4 个 LLM 调用 + emit 都依赖 `DASHSCOPE_API_KEY`。缺失时 `_outline_node` 入口检测到 `os.environ.get("DASHSCOPE_API_KEY")` 为空 → 抛 `RuntimeError("DASHSCOPE_API_KEY not set; required for write subgraph (outline / draft / critic / polish)")`——per spec 002 / 003 一致性。
- **`iteration_counts["write"]` counter +1 时机**：per [Q5] 决策，在 `_critic_review_node` 节点**入口** `+1`（不是条件边决策点）。`state["iteration_counts"]["write"]` 起始值由 graph.py 入口初始化为 0（per foundation Q4 + spec 001 FR-003）——write 子图首次进入时 counter=0，第一轮 critic 后变 1，第二轮 critic 后变 2，写子图退出。
- **emit 时 sources 为空列表**（理论边界——research 退出 sources ≥ 8 保证下不会发生，但作为防御性编程）：`_emit_final_report_node` 检测到 `len(state["sources"]) == 0` → log 警告但仍 emit，References 章节为空（或写 `_No sources found._`），不抛错。这是"尽力而为"哲学（per US4 acceptance scenario 4）。
- **`Source.url` 无效（HttpUrl 校验失败）**：emit 时 `f"[{title}]({url})"` 拼接——Pydantic `HttpUrl` 已在校验入口拒绝非法 URL（per spec 001 schema），所以此 edge case 在 emit 端不应发生；若仍发生（schema 被绕过的数据），log 警告并把该 source 从 References 中跳过。

---

## Requirements *(mandatory)*

### Functional Requirements

#### Write 子图节点（核心：US1-US4）

- **FR-001**：`nodes/write.py` MUST 真实现 `_outline_node(state) -> dict`：调 `prompts.get_prompt("deep-research-outline", label="production")` 拉 chat template，渲染（chevron 客户端，per spec 002 FR-001 一致性）传入 `user_query` / `plan` / `sources_summary`，调 `prompts.get_llm("write").invoke(...)` 拿到 Markdown 字符串，写 `state["outline"]`。outline 含 N 个 h2 章节（N == `len(state["plan"]`）。
- **FR-002**：`nodes/write.py` MUST 真实现 `_draft_node(state) -> dict`：调 `prompts.get_prompt("deep-research-draft", label="production")` 拉 chat template，渲染传入 `outline` / `sources_with_index`（join 每条 source 的 `index: title - url - content` 前 300 字），调 `prompts.get_llm("draft").invoke(...)`（per [Q2] 新增的 `draft` role，temperature=0.7）拿到 Markdown 字符串，写 `state["draft"]`。draft 每个 h2 章节与 outline 一致；正文用 `{{cite_N}}` 占位符（per [Q3] 决策，不直接写 `[ref_N]`）。
- **FR-003**：`nodes/write.py` MUST 真实现 `_critic_review_node(state) -> dict`：调 `prompts.get_prompt("deep-research-critic", label="production")` 拉 chat template，渲染传入 `outline` / `draft` / `sources_summary`，调 `prompts.get_llm("critic").invoke(...)`（temperature=0.2）拿到 JSON（schema: `{score: float, comments: str, suggestions: list[str]}`，per [Q4] 决策），解析为 `Critique` Pydantic 实体，写 `state["critique_history"]`（append-reducer 模式，per spec 001 FR-003）。**入口**对 `state["iteration_counts"]["write"]` 做 `+1`（per [Q5] 决策 + Constitution Constraint #3 + spec 003 Q4 一致性）。
- **FR-004**：`nodes/write.py` MUST 真实现 `_polish_node(state) -> dict`：调 `prompts.get_prompt("deep-research-polish", label="production")` 拉 chat template，渲染传入 `outline` / `draft` / `critique`（最新一条 Critique 的 `comments` + `suggestions`），调 `prompts.get_llm("polish").invoke(...)`（temperature=0.5）拿到改进后的 Markdown 文本（**仍含 `{{cite_N}}` 占位符**），**覆盖**写 `state["draft"]`（draft 是 str 文本字段，不是 list 追加）。
- **FR-005**：`nodes/write.py` MUST 实现 `_should_continue_writing(state) -> str` 条件边函数：返回 `"polish"`（继续迭代）当 `state["critique_history"][-1].score < 0.8 AND state["iteration_counts"]["write"] < 2`；返回 `"END"`（退出子图）当 `state["critique_history"][-1].score >= 0.8 OR state["iteration_counts"]["write"] >= 2`。**注**：`"END"` 是 LangGraph 子图边界 marker，外层图接续到 `emit_final_report` 节点（per [Q6] 决策）。
- **FR-006**：`nodes/write.py` MUST 实现 `build_write_subgraph() -> StateGraph`：组织 `(a) START → outline`；`(b) outline → draft`；`(c) draft → critic_review`；`(d) critic_review → should_continue_writing` 条件边；`(e) should_continue_writing("polish")` → polish → 回 critic_review（循环）；`(f) should_continue_writing("END")` → END marker。**移除** foundation spec FR-005 的 stub write 节点（替换为新实现，不保留 stub 双轨，与 spec 003 FR-018 一致性）。

#### emit_final_report 节点（核心：US5）

- **FR-007**：`graph.py` MUST 新增 `_emit_final_report_node(state) -> dict` 顶层节点（**不**在 write 子图内，per [Q6] 决策）：(a) 读 `state["draft"]`（含 `{{cite_N}}` 占位符）+ `state["sources"]`；(b) 用 `re.sub(r'\{\{cite_(\d+)\}\}', lambda m: f'[ref_{m.group(1)}]', draft_text)` 替换占位符为行内引用（per [Q3]）；(c) 拼接 `## References\n\n` + 每条 source 的 `1. [title](url) — score: X.XX\n` 行（按 index 升序，per ReferenceListPolicy）；(d) 写 `state["final_report"]`。`emit_final_report` 是 deterministic transformation，**不调 LLM**。
- **FR-008**：`_emit_final_report_node` MUST 校验 `state["sources"]` 中 `Source.index` 唯一性（per [Q7] 决策）：`len(set(s.index for s in sources)) != len(sources)` 时 log 警告 `[emit] Source.index duplicates detected; falling back to enumerate reindex` 并用 `enumerate(sources, start=1)` 重新编号后 emit，保证 References 章节无重复条目。
- **FR-009**：`graph.py` MUST 把 `emit_final_report` 接到外层图：(a) `graph.add_node("emit_final_report", _emit_final_report_node)`；(b) `graph.add_edge("write", "emit_final_report")`；(c) `graph.add_edge("emit_final_report", END)`。外层图变为 5 个顶层节点：`plan → confirm_plan → research → write → emit_final_report → END`，符合 ADR-0001 "外层节点加 1 时考虑平铺"future-trigger。

#### Langfuse Prompt 创建前置（手动操作）

- **FR-010**：spec 004 在 Langfuse UI 上需**预先**创建 4 个 chat template prompt（label="production"），内容见 `specs/004-deep-research-write-loop/contracts/README.md`：

  | Prompt name | 用途 | 必填占位符 |
  |---|---|---|
  | `deep-research-outline` | outline 节点 LLM 输入 | `{{user_query}}` / `{{plan_steps}}` / `{{sources_summary}}` |
  | `deep-research-draft` | draft 节点 LLM 输入 | `{{outline}}` / `{{sources_with_index}}` |
  | `deep-research-critic` | critic 节点 LLM 输入 | `{{outline}}` / `{{draft}}` / `{{sources_summary}}` |
  | `deep-research-polish` | polish 节点 LLM 输入 | `{{outline}}` / `{{draft}}` / `{{critique_comments}}` / `{{critique_suggestions}}` |

  - 必须使用 chevron 兼容的 Mustache 语法（`{{var}}` 替换；plan_steps / sources_with_index / sources_summary 都是 join 字符串而非 list，可直接用 `{{var}}` 展开）。
- **FR-011**：4 个 prompt 不在 Python 源码中以字符串字面量形式出现（per spec 002 NFR-003 一致性）——便于 Lesson 0006 在 Langfuse UI 上修改 prompt 不需重发版。

#### LLM Role 注册（核心：US1-US5）

- **FR-012**：`prompts.py::get_llm("write" | "draft" | "critic" | "polish")` MUST 返回 `ChatOpenAI` 实例（per [Q2] 决策）：与 `get_llm("plan") / get_llm("research")` 共用 `DASHSCOPE_API_KEY` / `DASHSCOPE_API_BASE_URL` / `DASHSCOPE_MODEL` env var，但 temperature 各异（outline=0.4 / draft=0.7 / critic=0.2 / polish=0.5）。`get_llm("draft")` 是 spec 004 新增的 role（spec 002 5 元组里没有 `draft`），需要在 `prompts.py::get_llm` 的 role 分支表里新增（MINOR 升级）。
- **FR-013**：spec 004 不新增 Python 依赖（所有 role 共用 `langchain-openai` 包，`dashscope` 仍仅 spec 003 用）；`pyproject.toml` 不变。

#### Schema 升级

- **FR-014**：`schemas.py::Critique` MUST 升级字段：增加 `suggestions: list[str]` 字段（per [Q4] 决策），默认空 list。spec 001 原 schema 仅含 `score: float` + `comments: str`，spec 004 加 `suggestions` 是 MINOR 升级（向后兼容：旧 Critique 实例只有 score+comments，polish 节点读 `critique.suggestions` 时若为空则 fallback 到 `comments` 派生）。
- **FR-015**：spec 004 不新增 Pydantic 实体；Outline / Draft / Critique / PolishedDraft / FinalReport / Reference 6 个实体已在 spec 001 定义，本 spec 通过节点实现让它们从 stub 文本变为真实数据。`Reference` 实体在 emit_final_report 节点动态生成（按 `Source` 派生视图，不独立存储）。

#### 错误处理与日志

- **FR-016**：所有 write 节点（MUST）入口打印一行 `[write] node=<name> iteration=<N> drafts_chars=<M>` 一行日志（per spec 002 / 003 一致性模式）——便于 Lesson 0007 实验 ops 监控 write 阶段进度。spec 006 接 Langfuse hook 后替换为 `langfuse.update_current_span(...)`。
- **FR-017**：所有"已知错误"（critic JSON 解析失败 / critic score 越界 / outline 章节数 / draft 占位符数 / Source.index 重复 / 等）MUST 包成 `NotYetImplementedError` 并抛出；"未知错误"（如 KeyError）让其透传让 LangGraph 整体失败，便于 review 时 review bug。polish 节点**不抛** NotYetImplementedError（polish 哲学是"尽力改进"——LLM 输出空字符串也接受，写空 draft 进 critic，critic 自然 0 分）。ReRank 不参与 write 阶段（spec 002 FR-006 silent degrade 仅适用于 spec 003 research 阶段）。
- **FR-018**：`prompts.py::get_llm("write" | "draft" | "critic" | "polish")` MUST 在 `os.environ.get("DASHSCOPE_API_KEY")` 为空时抛 `RuntimeError("DASHSCOPE_API_KEY not set; required for write subgraph")`——与 spec 003 `tools.py` 凭证缺失错误模式一致。

#### 配置与依赖

- **FR-019**：spec 004 不新增 env var（per spec 003 FR-023 一致性 + Constitution Constraint #7）；所有现有 env var（`DASHSCOPE_API_KEY` / `DASHSCOPE_MODEL` / `DASHSCOPE_API_BASE_URL` / `LANGFUSE_*` / `LANGGRAPH_CHECKPOINT_DB`）不变。
- **FR-020**：spec 004 不新增 Python 依赖（per FR-013）；所有 LLM 调用走 `langchain-openai`（已在 spec 002 锁定），prompt 渲染走 `chevron`（已在 spec 002 锁定）。

### Key Entities *(include if feature involves data)*

- **Outline**（已在 `schemas.py`）：报告大纲（Markdown 字符串，含 N 个 h2 章节对应 PlanStep）。本 spec 让其从 stub 变为真实 LLM 生成的 Markdown。
- **Draft**（已在 `schemas.py`）：初稿 Markdown（str），含 `{{cite_N}}` 占位符（per [Q3]）。本 spec 让其从 stub 变为真实 LLM 填充的内容。
- **Critique**（已在 `schemas.py`）：`score: float` (0-1) + `comments: str` + `suggestions: list[str]`（**升级**：spec 001 仅 score+comments；spec 004 加 suggestions，per [Q4] + FR-014）。本 spec 让其从 stub 变为真实 LLM 评审产物。
- **PolishedDraft**（已在 `schemas.py`）：经一轮 polish 改进后的 Markdown（str）。**实装注意**：polish 节点**覆盖**写 `state["draft"]` 而非单独存 PolishedDraft 字段（避免 schema 字段膨胀）；`PolishedDraft` 实体在 schemas.py 保留作为类型标签，但 state 字段仍叫 `draft`。
- **FinalReport**（已在 `schemas.py`）：最终报告 Markdown（str），含 `[ref_N]` 行内引用 + `## References` 章节。本 spec 让其从 stub 变为 emit_final_report 节点确定性加工产物。
- **Reference**（已在 `schemas.py`）：`index: int` + `title: str` + `url: HttpUrl` + `score: float | None`。emit_final_report 节点**不**在 state 里存 Reference list——它直接拼接为 `## References` 章节字符串写入 `final_report`；`Reference` 实体仅作为 Pydantic 类型用于渲染时的中间变量。
- **WriteIteration**（state counter）：`state["iteration_counts"]["write"]` 计 write 子图跑了多少轮，由 `_critic_review_node` 节点入口 `+1`（per [Q5] + Constitution Constraint #3 + spec 003 Q4 一致性）。

### Non-Functional Requirements

- **NFR-001**：write 子图单次运行 P50 wall-clock latency ≤ 60 秒（含 outline / draft / critic / polish 各 1 次 LLM 调用 × 平均 2 轮 = 6 次 LLM 调用）、P90 ≤ 120 秒。若超过 120 秒，UI（spec 005）应显示"writing…"占位（spec 004 仅保证不阻塞主线程）。
- **NFR-002**：`iteration_counts["write"]` 的 increment discipline 严格遵循 [Q5] 决策"在 `_critic_review_node` 节点入口 `+1`，不在条件边决策点 `+1`"——与 spec 003 research 子图 `gen_query` 入口 `+1` 一致，per Constitution Constraint #3。
- **NFR-003**：所有 prompt 文本字面值 MUST 从 Langfuse 拉取（per spec 002 NFR-003 一致性）。`prompts.py` / `nodes/write.py` 不写 prompt body 字面值。
- **NFR-004**：`write` 节点在 DashScope 不可达时 MUST NOT 静默 fallback——失败抛真实异常，让 write 子图 try/except 捕获并转换为 `NotYetImplementedError` 上抛（per Constitution Principle IV + spec 003 NFR-004 一致性）。**例外**：polish 节点允许 LLM 输出空字符串（"尽力改进"哲学）。

---

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**：在 Langfuse UI 提前创建 `deep-research-outline` / `deep-research-draft` / `deep-research-critic` / `deep-research-polish` 4 个 chat template prompt（label="production"），凭证 `DASHSCOPE_API_KEY` / `LANGFUSE_*` 均配置。运行 `python -c "from src.apps.deep_research.graph import run_research; r = run_research({'user_query': '...', 'auto_confirm_plan': True}, thread_id='write-sc-001'); import re; outline = r['outline']; print(len(re.findall(r'^## ', outline, re.MULTILINE)) == len(r['plan']), len(re.findall(r'\\{\\{cite_\\d+\\}\\}', r['draft'])) >= 5, len(r['critique_history']) >= 1, re.search(r'^## References', r['final_report'], re.MULTILINE) is not None)"`，stdout 输出 4 个 True。
- **SC-002**：outline 章节对齐验证——运行 SC-001 后 `python -c "..."` 数 `state["outline"]` h2 章节数 == `len(state["plan"])`，且每个 h2 标题包含对应 `PlanStep.title` 的关键词（substring match 算通过）。
- **SC-003**：draft 占位符验证——运行 SC-001 后 `state["draft"]` 中 `{{cite_N}}` 占位符总数 ≥ 5（即至少 5 处引用占位符）；每个 `{{cite_N}}` 中的 N 是合法 `Source.index`（`1 ≤ N ≤ len(state["sources"])`）。
- **SC-004**：critic 评分验证——运行 SC-001 后 `state["critique_history"][-1].score ∈ [0.0, 1.0]` 且 `state["critique_history"][-1].comments` 非空字符串、`state["critique_history"][-1].suggestions` 是 list（可空 list）。
- **SC-005**：write 循环终止验证——运行 SC-001 后 `state["iteration_counts"]["write"] ∈ {1, 2}`（至少 1 轮 critic，最多 2 轮），且 `len(state["critique_history"]) ∈ {1, 2}` 与 counter 一致。
- **SC-006**：emit_final_report 验证——运行 SC-001 后 `state["final_report"]` 含 N 个 `[ref_N]`（N ≥ `len(state["sources"])`）且末尾 `## References` 章节列出所有 `state["sources"]`（按 index 升序，含 title / url / score 三字段）。
- **SC-007**：final_report 是合法 Markdown 验证——运行 SC-001 后 `import re; fr = r['final_report']; assert len(re.findall(r'\\[ref_\\d+\\]', fr)) >= 5; assert re.search(r'^## References', fr, re.MULTILINE); assert len(re.findall(r'^## ', fr, re.MULTILINE)) >= len(r['plan'])` 全部通过——证明 FinalReport 是合法 Markdown 且每个 PlanStep 对应一个 h2 章节。
- **SC-008**：全链路端到端 smoke——运行 SC-001 后 `state["final_report"]` 不为空字符串，且 `state["iteration_counts"]["research"] >= 1`（research 子图跑过）+ `state["iteration_counts"]["write"] >= 1`（write 子图跑过），证明 plan → confirm_plan → research → write → emit_final_report 全链路打通。
- **SC-009**：Langfuse prompt 缺失可视化——把 `deep-research-outline` 从 Langfuse 删除后跑 SC-001，`run_research` 在 `_outline_node` 处抛 `NotYetImplementedError("prompt not found in langfuse: deep-research-outline")`（per US1 acceptance scenario 3）。
- **SC-010**：Source.index 重复防御验证——构造一份 mock state 含 2 个相同 `index=1` 的 source（手动 `state["sources"] = [Source(index=1, ...), Source(index=1, ...)]` 后 emit），跑 `_emit_final_report_node` 后 `state["final_report"]` 的 References 章节**只**有 1 条 index=1 条目（enumerate 重新编号生效，不重复）。

---

## Assumptions

- Langfuse 服务在 `http://localhost:3000` 可达（来自 `langfuse-deploy/docker-compose.yml`），且 `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` 已在 `.env` 配置（Lesson 0006 同步创建）。
- DashScope 凭证 `DASHSCOPE_API_KEY` 已配置（用于 chat LLM）；`DASHSCOPE_MODEL` 与 `DASHSCOPE_API_BASE_URL` 已配置（per spec 002 一致性）——`.env` 已就位。
- `deep-research-outline` / `deep-research-draft` / `deep-research-critic` / `deep-research-polish` 4 个 Langfuse prompt 由开发者**手动创建**（chat 类型，label="production"，含必需占位符）——本 spec 不写创建脚本，假设用户跟随 README 操作；占位符约定见 FR-010。
- spec 004 完成态下 research 子图仍为真实实现（spec 003 已完成），write 子图完成态从 stub 升级为真实实现。
- Postgres 来自 `langfuse-deploy`（`localhost:5432`，DB=`postgres`，user/pass=`postgres`），复用 spec 001/002/003 已验证的连接串与 advisory lock 机制。
- LangGraph 1.2.11+ 已锁定（已在 `pyproject.toml` 锁定）——条件边 + 子图 + add_node + add_edge 语法不变。
- User 接受 `temperature` 4 档配置（outline=0.4 / draft=0.7 / critic=0.2 / polish=0.5）作为 write 阶段的 LLM 默认值——若未来 Lesson 0007 实验发现某档偏差大可调，但本 spec 锁定以保证 SC 可重复。
- write 阶段**不**调 ReRank（spec 003 的 ReRank 仅用于 research 阶段的 Source 评分）；write 节点的 LLM 调用**不**走 spec 003 的 silent degrade 模式（无降级路径，直接抛真实异常，per spec 002 / 003 FR 一致性）。
- `Source.score` 字段在 write 阶段用于 References 章节渲染（`score: X.XX`），不需要 ReRank 重新精排——write 节点**不读** `Source.score` 做内容筛选（仅做展示）。
- `PolishedDraft` Pydantic 实体保留在 `schemas.py` 但不在 state 里实装字段（polish 节点覆盖 `draft` 字段而非单独存 PolishedDraft）——这是 spec 001 schema 的设计意图，spec 004 不变。

---

## Out of Scope

- Streamlit UI 与 session_state 接线（spec 005）
- Langfuse `@observe` hook + TraceMetadata（spec 006）
- Write 节点的多模型 ensemble（如 draft 用 GPT-4 + polish 用 Claude 对比）——本 spec 锁定 DashScope 单模型
- Write 阶段的多模态（图表、表格自动生成）——本 spec 仅文本 Markdown
- Write 阶段的实时预览（incremental 渲染）——本 spec 一次性输出
- Write 阶段的语言切换（中文/英文）——本 spec 默认中文输出（per CONTEXT.md §8）
- Plan / Outline 的 quality score 打分到 Langfuse（Lesson 0007 实验范畴，本 spec 不埋点）
- Source 按相关性做引用优先级（[ref_1] 是否优先于 [ref_5]）——本 spec 仅按出现顺序编号，不做引用优先级排序
- Reference 列表的 per-source 节选（每个 Reference 附 content 片段）——本 spec 仅 title / url / score 三字段（per CONTEXT.md §6 ReferenceListPolicy 最小要求）

---

## Dependencies

- spec 001（foundation）：包骨架、schemas（Outline / Draft / Critique / PolishedDraft / FinalReport / Reference 已 frozen，仅 Critique 加 `suggestions` 字段是 MINOR 升级）、state（`iteration_counts["write"]` 初始化在 graph.py 入口）、graph 拓扑（4 节点 + nested write subgraph）——本 spec 全部继承。
- spec 002（plan-confirm）：prompts.get_prompt / get_llm("plan") / chevron 渲染 / thread_id advisory lock——本 spec 的 write / draft / critic / polish 4 个 LLM role 与 outline / draft / critic / polish 4 个 prompt 沿用 chevron 客户端渲染模式（per spec 002 FR-001 一致性）。
- spec 003（research-loop）：`state["sources"]` 在 write 子图入口已 ReRank 重排为 1..N 全局唯一 index（per spec 003 Q6），write 端直接复用——不重新编号。`Source.score` 字段已 ReRank 后由 eval_search_node 写值，emit_final_report 节点直接读 score 渲染 References。
- `langfuse-deploy` Postgres（`localhost:5432`）：SC 系列的 Postgres checkpoint 验证基础。
- Langfuse 3.x Python SDK（已在 `pyproject.toml` 锁定）：`get_prompt` / `compile` API 沿用 spec 002/003 实装。
- DashScope Python SDK（`dashscope>=1.14.0`）：通过 `langchain-openai` ChatOpenAI 间接使用，per spec 002 一致性。
- LangGraph 1.2.11+（已在 `pyproject.toml` 锁定）：条件边 + 子图 + `add_node` + `add_edge` + END marker 是子图边界语法。
- ADR-0001（nested sub-graphs）：write 是嵌套子图（节点边界 + 状态隔离 per ADR-0001）。
- ADR-0003（interrupt for plan confirm，间接影响）：write 子图无 interrupt（write 是异步自动跑，不需要 user 介入），与 spec 002 plan-confirm 模式不同。

---

## Notes

- spec 004 是 deep research agent **最后一段"做实事"**的 spec——前 3 个 spec 准备基础设施（graph 骨架 / plan 审阅 / research 真实跑），spec 004 完成 write 后，agent 已经可以端到端产出 `final_report` Markdown。spec 005 / 006 是 UI 包装与可观测性增量。
- 本 spec 把 [Q1]-[Q7] 的架构层决策**前置**到 Clarifications 而非 Edge Cases——因为这些决策影响 write 子图主路径与 graph.py 拓扑（不是边界条件）。
- emit_final_report 放到外层图（per [Q6]）是 ADR-0001 设计的"future-trigger"兑现：当初 ADR-0001 说"如果外层再加节点考虑用 `graph.add_node` 平铺"，spec 004 把 emit 平铺到外层。write 子图保持纯循环结构（outline → draft → critic_review ⇄ polish），emit 是一次性 deterministic 在外层 graph 的第 5 节点。
- `[Q3]` 决策"draft 用 `{{cite_N}}` 占位符，emit 替换为 `[ref_N]`"是 spec 004 引用质量的核心防御——LLM 经常在 `[ref_N]` 处编造数字（如 LLM 脑补 `[ref_42]` 而 source 列表只到 [ref_8]），占位符 + emit 替换让引用编号**永远**对应真实 Source.index。这是 Lesson 0007 实验能机械化统计引用质量的 contract。
- `[Q4]` 决策"critique JSON schema: score + comments + suggestions"是 spec 002 / 003 一致性模式的延伸——每个 LLM-as-Judge 输出都是简洁可序列化 JSON，方便后续节点 parse + schema 校验 + fallback 重试。
- `[Q5]` 决策"counter +1 在 critic_review 入口"是 Constitution Constraint #3 increment discipline 的兑现——所有循环子图（research / write）的 counter 都在节点入口 +1，便于 debug 时从 Postgres checkpoint 单步推断 counter 状态。
- `[Q6]` 决策"emit_final_report 在外层图"打破"write 子图 = 一切 write 相关的节点"的直观，但符合"循环子图只放循环节点，一次性确定性节点在外层"的工程美学。Lesson 0007 跑实验时，emit 失败不会让 write 子图崩（write 子图是循环，外层 emit 是 plain node，错误隔离清晰）。
- spec 004 不新增 Python 依赖（per FR-013）——这是 Constitution Constraint #8 "Library versions" 锁定的副产物：所有依赖已在 spec 001/002/003 中锁定。
- spec 004 不新增 env var（per FR-019）——这是 Constitution Constraint #7 "Env-var hygiene" 锁定的副产物：write 阶段的所有配置走 Langfuse prompt（运行时）+ `DASHSCOPE_*` env var（已存在）。
- spec 004 完成后，Lesson 0007 实验链路：plan-confirm 自动化（auto_confirm=True）→ research 真实跑 → write 真实跑 → emit_final_report 加工 → `final_report` Markdown 输出，可由 Lesson 0007 实验脚本直接 `print` / 写文件 / 评测。