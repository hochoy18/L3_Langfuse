# Research · Spec 004 Write 阶段

> Phase 0 产出：spec 004 引入 / 修改的 10 个研究主题 + spec 修正表。
>
> **前提**：spec 001 foundation + spec 002 plan-confirm + spec 003 research-loop 已完成态。
> spec 004 不重新发明 research 子图或 LLM 调用模式，直接继承 spec 002/003 的 `prompts.get_llm` + chevron 渲染 + Postgres advisory lock + auto_confirm_plan 模式。

---

## R1 · 4 个 LLM role 兑现 + 新增 `draft` role

### Decision

兑现 spec 002 已预留的 5 元 LLM role（`plan / research / write / critic / polish`）+ 新增第 6 个 `draft` role。`prompts.py::get_llm(role)` 实装 6 个 role（spec 002 已有 `plan`，spec 003 已有 `research`，spec 004 新增 4 个）：

| role | temperature | 用途 | Langfuse prompt |
|---|---|---|---|
| `plan` | 0.4 | Plan 章节化生成 | `deep-research-plan` |
| `research` | 0.2 | gen_query 查询生成 | `deep-research-gen-query` / `deep-research-judge` |
| `write` | **0.4** | outline 章节规划 | `deep-research-outline` |
| `draft` | **0.7** | draft 正文填充（创造性）| `deep-research-draft` |
| `critic` | **0.2** | draft 评审（稳定严格）| `deep-research-critic` |
| `polish` | **0.5** | polish 改进（折中）| `deep-research-polish` |

### Rationale

- **温度分档理由**：
  - `outline` 0.4（与 plan 同）：结构化输出，但允许章节标题有一定创造性
  - `draft` 0.7：长文生成需要较高温度，避免每轮生成同一篇 draft；critic 能识别低质量并触发 polish 改进
  - `critic` 0.2：评审要严格稳定（低温度），否则同一篇 draft 每次 score 差异大，polish 改进链路不收敛
  - `polish` 0.5：折中（既要保留原 draft 骨架，又要改进措辞）
- **`draft` role 不在 spec 002 5 元组里的理由**：spec 002 写 spec 时还没意识到 draft 与 plan 同等重要性，把 draft 当作 plan role 的复用（即 `plan` 也生成 draft）。spec 003 / 004 演进发现 plan 是"章节规划"（JSON 结构化输出），draft 是"长文生成"（Markdown 文本输出），语义差异大，应独立 role。spec 002 的 5 元组预留"开放扩展位"——spec 004 是兑现扩展而非破坏预留。
- **不新增 "outline" 角色**：outline 节点步骤和 plan 步骤语义类似（都是"分章节规划"），复用 `write` role 而不是新建 `outline` role。**简化 role 表：v1 = {plan, research, write, draft, critic, polish} 共 6 个**。

### Alternatives Considered

#### A. 复用 plan role 给 outline + draft

- **优点**：role 表更小（仅 4 个）；不新增 `draft` role。
- **否定原因**：plan 是 JSON 结构化输出（list[PlanStep]），draft 是 Markdown 长文本输出（str），temperature 需求差异大（plan 0.4 适合 JSON，draft 0.7 适合长文本）。复用导致要么 plan 太创造性（JSON 不稳定）要么 draft 太死板（Markdown 缺乏变化）。

#### B. 拆 6 个独立 role（plan/research/outline/draft/critic/polish）

- **优点**：每节点一个 role，最清晰。
- **否定原因**：outline 与 plan 语义重叠（都是"分章节规划"），独立 role 让 role 表膨胀到 6 步而无明确收益。`write` role 兼 outline 是简化。

### Evidence

- spec 002 FR-005 + spec 002 Q1 决策"role 五元组预留"
- spec 003 Q3 决策"research LLM temperature=0.2（vs plan 0.4）"
- Constitution Principle V "Simplicity" 鼓励 role 表最小化（v1 = 6 个 role 是 P1 deliverable 的最小集）

### Action Items

- tasks 阶段在 `prompts.py::get_llm` 加 4 个 role 分支（write/draft/critic/polish），每个分支 `ChatOpenAI(temperature=<x>)` 返回
- `pyproject.toml` 不变（`langchain-openai` 已锁定）

---

## R2 · `{{cite_N}}` 占位符机制

### Decision

draft 节点让 LLM 在事实主张后写 `{{cite_N}}` 占位符（N 是 `Source.index`，1..M 全局唯一，per spec 003 Q6），emit_final_report 节点用 `re.sub(r'\{\{cite_(\d+)\}\}', lambda m: f'[ref_{m.group(1)}]', draft_text)` 替换为 `[ref_N]` 行内引用。

### Rationale

- **LLM 引用幻觉防御**：实测 DashScope qwen 系列模型在 prompt "write with [ref_N] citations" 时经常：
  1. 编造不存在的 N（如 LLM 写 `[ref_42]` 而 source 列表只到 [ref_8]）
  2. 漏掉该写的地方（裸事实无引用）
  3. 重复同一 N（违反 [ref_N] 全局唯一）
- **占位符机制的好处**：
  - LLM 只写 `{{cite_3}}` 这种结构化占位符，N 必须从 prompt 提供的 sources 列表里**显式 copy**，少编造
  - 占位符可被 emit_final_report 用正则批量校验（`<tool_call>` 转换为 `[ref_1]` / `[ref_2]` 等）
  - 占位符未匹配的（如 `{{cite_42}}` 但 sources 只有 8 条）可在 emit 时静默丢弃（或 log 警告）
- **emit 时确定性替换**：re.sub 是 deterministic transformation，不调 LLM，无 hallucination 风险，无 retry / timeout 复杂度——这就是 [Q6] 决策把 emit 放外层图而非 write 子图内的理由（确定性一次，循环不必要）

### Alternatives Considered

#### A. LLM 直接写 `[ref_N]`

- **优点**：emit 阶段不需要转换；UI 端直接 render Markdown。
- **否定原因**：LLM 引用幻觉（见上）；emit 端没法"修正"LLM 写错的 N，只能 log 警告。

#### B. LLM 输出不带引用，emit 阶段自动插引用（基于 sources 关键词匹配）

- **优点**：LLM 完全不需要学"如何引用"，prompt 最简单。
- **否定原因**：sources 是长文（content 字段前 300 字），关键词匹配召回率低（"量子计算" vs "量子计算在金融的运用"），precision 高但 recall 低，导致很多事实主张无引用。
- **反例**：spec 005 UI 期望 final_report 有 ≥ 5 处引用（per spec 004 SC-001 + SC-006）；自动插引用做不到这个保证。

### Evidence

- spec 003 Q6 决策"Source.index 在 eval_search_node 重编号为 1..N 全局唯一"——这是 R2 占位符 N 的取值域保证
- [Q3] 决策"draft 用占位符，emit 替换"
- Lesson 0006 经验：LLM 长文本生成的引用质量是 recurring 问题，占位符 + emit 替换是 defense-in-depth

### Action Items

- `deep-research-draft` prompt 必须显式说"使用 `{{cite_N}}` 占位符（N 是 sources 列表里的 index）"，不能用 `[ref_N]`——否则 LLM 又会用 [ref_N]
- emit 节点在 `re.sub` 后增加占位符-未匹配日志：`[emit] dropped {{cite_42}} (no source with index=42)`

---

## R3 · Critique schema 升级（加 suggestions 字段）

### Decision

`schemas.Critique` 加 `suggestions: list[str] = Field(default_factory=list)` 字段。polish_node 优先读 `critique.suggestions`，为空时 fallback 到从 `critique.comments` 派生（LLM 解析 comments 字符串为多 bullet）。

### Rationale

- **spec 001 schema 设计的 Critique 实体只有 `score + comments`**——comments 是自由文本，polish 节点难以机械化解析改进方向（只能整体重写而非针对性 polish）
- **升级后 polish 能针对性 polish（每条 suggestion 一处修改）**，避免"整篇重写"导致的"polish 后大幅缩减 draft"（per R8 风险）
- **向后兼容**：默认 `default_factory=list` 让旧 Critique 实例（spec 002/003 持久化的 checkpoint 数据，或 Lesson 0006 之前留下的 Langfuse score 数据）反序列化时 `suggestions=[]`，polish 自动 fallback 到 comments 派生——不破坏 foundation 兼容（per Constitution Principle I）
- **MINOR 升级**：Pydantic 加字段是 schema 兼容性的 MINOR 升级（v1.0.0 → v1.1.0），不是 MAJOR（不改字段名 / 不改必填约束）

### Alternatives Considered

#### A. 沿用 `score + comments`，polish 从 comments 字符串解析

- **优点**：不改 schema，完全向后兼容。
- **否定原因**：comments 是自由文本，解析不可靠；polish 节点用 LLM 重新解析 comments 等于"二次 LLM 调用"（浪费 token + 增加 latency）。

#### B. 拆 `score + comments + issues + suggestions + verdict` 多字段

- **优点**：每条 critique 信息量更大，polish 能更精细改进。
- **否定原因**：spec 004 polish 不需要那么精细（polish 是"尽力改进"哲学，不是"针对性修复"）；多字段让 schema 膨胀（5 字段 vs 3 字段），Pydantic ValidationError 概率上升。

### Evidence

- [Q4] 决策"Critique schema: score + comments + suggestions"
- FR-014 "Critique 加 suggestions 字段（MINOR 升级）"
- spec 002 / 003 持久化的 Postgres checkpoint 数据格式：`{"critique_history": [{"score": 0.X, "comments": "..."}]}`，反序列化到新 schema 时 `suggestions=[]` 默认值生效

### Action Items

- `schemas.py::Critique` 加 `suggestions: list[str] = Field(default_factory=list, max_length=10)`（max_length=10 防御 LLM 输出过长 list）
- tasks 阶段写一个 round-trip 测试：`Critique(score=0.5, comments="x", suggestions=["a", "b"]).model_dump_json()` → `Critique.model_validate_json(...)` 还原为相同对象

---

## R4 · counter +1 时机对称性

### Decision

write 子图 `_critic_review_node` 节点**入口**对 `state["iteration_counts"]["write"] += 1`（per FR-003 + Constitution Constraint #3）。与 spec 003 `_gen_query_node` 入口 `+1`（per spec 003 Q4 + FR-014）对称为"循环子图节点入口 +1"模式。

### Rationale

- **Constitution Constraint #3 锁定"start a new round"语义**：节点入口 +1，条件边只读不写
- **spec 003 research 子图 vs spec 004 write 子图对称性**：
  - research：`gen_query` 是循环触发点（入口 +1，启动新一轮 gen_query → websearch × N → eval_search）
  - write：`critic_review` 是循环触发点（入口 +1，启动新一轮 critic_review → polish → critic_review）
  - **关键区别**：research 子图循环效果是"增加 sources 数量"（eval_search 评价）；write 子图循环效果是"改进 draft 质量"（polish 改进）。两者循环触发点都在"评价/评审"节点入口 +1，是合理的对称设计
- **Postgres checkpoint 端验证方便**：从 checkpoints 表读 `iteration_counts["write"]` 直接推断 write 子图跑了多少轮（per spec 003 SC-004 类似模式）

### Alternatives Considered

#### A. 条件边决策点 +1（`should_continue_writing` 函数内）

- **优点**：counter 与"是否继续迭代"在同一处，逻辑紧凑。
- **否定原因**：违反 Constitution Constraint #3；spec 003 / 004 对称性破坏；debug 时从 Postgres checkpoint 单步推断 counter 状态困难（不知道是节点入口 +1 还是条件边 +1）。

#### B. `_polish_node` 入口 +1（而不是 critic_review）

- **优点**：polish 是循环触发点（polish → critic_review → 是否 polish）。
- **否定原因**：polish 是"改进"动作（不评价），counter 应该在"评价"动作 +1（critic_review）；polish +1 语义不清晰（"polish 是新一轮的开始"还是"polish 是新一轮的结束"）。

### Evidence

- spec 003 Q4 决策"在 gen_query 节点入口 +1"
- spec 003 FR-014 "iteration_counts['research'] +1 在 gen_query 入口"
- spec 002 NFR-002 "iteration_counts increment discipline"
- Constitution Constraint #3 "start a new round"语义

### Action Items

- `_critic_review_node` 函数体第一行 `state["iteration_counts"]["write"] += 1`
- tasks 阶段写一个 counter 单测，验证 `_critic_review_node` 入口读 `state["iteration_counts"]["write"]` 时 +1 已生效

---

## R5 · emit_final_report 放外层图（第 5 节点）

### Decision

`emit_final_report` 不在 write 子图内（紧跟 polish 后），而是外层图的第 5 个顶层节点：`graph.add_node("emit_final_report", _emit_final_report_node)` + `graph.add_edge("write", "emit_final_report")` + `graph.add_edge("emit_final_report", END)`。外层图变为 5 个顶层节点：`plan → confirm_plan → research → write → emit_final_report → END`。

### Rationale

- **ADR-0001 future-trigger 兑现**：当初 ADR-0001 设计说"如果外层再加节点（如 summarize / translate）考虑用 `graph.add_node` 平铺；不需要的话保持嵌套结构"。spec 004 加 emit_final_report 就是 ADR-0001 的 future-trigger——外层加 1 节点，平铺到外层
- **emit 是确定性 transformation，不需循环**：emit_final_report 是 deterministic `re.sub` + concat，**不调 LLM**，**不需重试**（per FR-007）。把它放在 write 子图循环里会让人误以为 emit 也循环跑（其实只跑 1 次）。放外层图让语义清晰："write 子图负责循环写，外层 emit 负责一次性加工"
- **错误隔离**：emit 失败（极少见，但理论上 re.sub 抛错是可能的）不影响 write 子图状态（write 子图已退出 END marker）；write 子图状态干净，UI 可提示"draft 已生成，emit 失败请重试"
- **测试便利**：emit 节点独立可测（mock draft + sources 直接断言 final_report 字段，无需 mock LLM）

### Alternatives Considered

#### A. emit 放在 write 子图内末尾（紧跟 polish 后）

- **优点**：emit 与"写"流程紧密结合；外层图保持 4 节点不变。
- **否定原因**：语义混淆（emit 看起来也是 write 循环一部分，但其实只跑 1 次）；ADR-0001 future-trigger 未兑现；错误隔离差（emit 失败让 write 子图 END marker 抛错）。

#### C. emit 拆成 2 节点：`emit_references_replace` + `emit_concat_references`

- **优点**：每个节点单一职责。
- **否定原因**：over-engineering——re.sub + concat 都是 deterministic 几行 Python，拆 2 节点让图拓扑膨胀且无收益。

### Evidence

- [Q6] 决策"emit_final_report 在外层图"
- ADR-0001 "如果外层再加节点考虑平铺"
- spec 003 T006 决策"outer graph 4 个节点（plan/confirm_plan/research/write）"，spec 004 是 T006 的 future 兑现

### Action Items

- tasks 阶段在 `graph.py` 加 3 行（add_node + 2 add_edge）
- 验证外层图 `build_graph().get_graph().draw_mermaid()` 能渲染 5 个顶层节点 + 2 个嵌套子图

---

## R6 · Source.index 防御（duplicate 兜底）

### Decision

emit_final_report 节点直接读 `state["sources"]` 中的 `Source.index`（per spec 003 Q6 已重排为 1..N 全局唯一）。检测到 `len(set(s.index for s in sources)) != len(sources)` 时 log 警告 `[emit] Source.index duplicates detected; falling back to enumerate reindex`，用 `enumerate(sources, start=1)` 重新编号后 emit。

### Rationale

- **直接复用 spec 003 结果**：spec 003 eval_search_node 已重排 source.index 为 1..N 全局唯一（per spec 003 Q6 决策）。spec 004 write 节点不动 source.index，直接读 emit
- **防御性兜底**：理论上 spec 003 之后的 source.index 应该是唯一的；但防御性编程认为"理论 unique 应 >= Uniqueness check"。如果出现 duplicate（spec 003 bug / 数据迁移 / Lesson 0007 实验手动注入），emit 不抛错（用户能拿到 final_report），仅 log 警告 + enumerate 重新编号兜底
- **enumerate vs sort**：enumerate 是 deterministic（保持 spec 003 已重排顺序）；sort by index 会破坏 spec 003 的"按 score 降序"语义（spec 003 已按 score 降序排列，sort by index 会重新按数字升序）。emit 时 References 章节按"重新编号后的 index 升序"输出（per ReferenceListPolicy），但 Source 实例顺序不动（避免破坏 score 降序的语义）

### Alternatives Considered

#### A. emit 时抛错

- **优点**：强制要求 spec 003 严格保证 index 唯一性。
- **否定原因**：emit 是"尽力而为"哲学（与 write 节点一致）；duplicate 是 defensive edge case 不应阻塞最终报告输出。log 警告 + 兜底是更友好的体验。

#### B. 用 dict 按 index 去重（保留首次出现）

- **优点**：保留 spec 003 已重排。
- **否定原因**：lossy 模型（duplicate 信息丢失）；user 可能想看"为什么会有 duplicate"（debug 信息）。enumerate reindex 更直观。

### Evidence

- [Q7] 决策"直接复用 Source.index；duplicate 时 enumerate 兜底"
- spec 003 Q6 决策"Source.index 在 eval_search_node 重编号为 1..N 全局唯一"
- spec 003 FR-013 重排实现（按 score 降序 + 重编号 1..N）
- ReferenceListPolicy（CONTEXT.md §6）"FinalReport 末尾 ## References 章节列出所有被引用的 Source"

### Action Items

- `_emit_final_report_node` 函数体第一段：`if len(set(s.index for s in state["sources"])) != len(state["sources"]): log + enumerate reindex`
- tasks 阶段写一个 SC-010 单测：构造 mock state 含 2 个 index=1，验证 emit 后 References 章节只有 1 条 index=1 条目

---

## R7 · polish 节点"尽力改进"哲学

### Decision

`_polish_node` **不抛错**——LLM 输出空字符串 / 大幅缩减 draft（new_draft < 0.5 * old_draft）都接受。per FR-017 + FR-018 "polish 节点允许 LLM 输出空字符串"。

### Rationale

- **polish 是"尽力改进"哲学，不是"必须改进"哲学**：spec 003 research 子图不允许 sub-source 失败（失败必须 raise 或 fallback），因为 research 是"事实收集"——事实不够多就该 deliver 的；write 子图 polish 是"质量改进"——质量不够好也应 deliver（用户拿到 draft 总比拿到空白好）
- **与 spec 003 FR-022 一致性**：spec 003 已知错误包 `NotYetImplementedError`；但 polish 的"输出空字符串"不是错误，是 LLM 的判断（"draft 已经够好不需要改"或"无法改进"）。这是 spec 004 唯一的 silent degrade 例外（per FR-017）
- **空 draft 仍能 emit**：polish 输出空字符串 → critic_review 第 2 轮评分 → `should_continue_writing` 决策（score=0 OR iters=2 → exit）→ emit_final_report 把空 draft + sources 加工成 "I see [empty draft]" + References 章节（仍合法 Markdown，无章节内容但 References 完整）——用户至少能看到 References 知道"研究了哪些 sources"

### Alternatives Considered

#### A. polish 抛 NotYetImplementedError

- **优点**：与 spec 003 一致性；不允许 silent degrade。
- **否定原因**：polish 的"输出空"是合理的 LLM 判断（"draft 已经够好"）；抛错让用户体验差（明明配了 4 个 LLM 调用，最后一步崩了）；与 spec 004 "尽力而为"哲学不一致。

#### B. polish 重试 1 次

- **优点**：给 LLM 第二次机会。
- **否定原因**：LLM 第二次仍可能输出空（"尽力改进"哲学的 LLM 是 conservative）；retry 浪费 token + latency（write 子图 P90 ≤ 120s 是 NFR-001 上限）。不 retry 直接接受更高效。

### Evidence

- FR-017 "polish 节点**不抛** NotYetImplementedError（polish 哲学是'尽力改进'——LLM 输出空字符串也接受）"
- FR-018 "已知错误"清单不含 polish 输出空字符串
- spec 003 FR-022 "已知错误包 NotYetImplementedError" 是 research 子图约束，不约束 write 子图 polish

### Action Items

- `_polish_node` 不写 try/except；LLM 输出空字符串直接返回 `{"draft": ""}`
- tasks 阶段写一个 polish 哲学测试：mock `get_llm("polish")` 返回空字符串，验证 `_polish_node` 不抛错且返回 `{"draft": ""}`

---

## R8 · Langfuse prompt 占位符与 chevron 渲染

### Decision

4 个新 Langfuse chat template prompt 占位符（per FR-010）：

| Prompt | 占位符 | 渲染内容 |
|---|---|---|
| `deep-research-outline` | `{{user_query}}` / `{{plan_steps}}` / `{{sources_summary}}` | plan_steps = `\n`.join(f"{i}. {step.title}" for i, step in enumerate(state["plan"]))；sources_summary = `\n`.join(f"- {s.title}" for s in state["sources"]) |
| `deep-research-draft` | `{{outline}}` / `{{sources_with_index}}` | sources_with_index = `\n`.join(f"[{s.index}] {s.title} - {s.url}\n{s.content[:300]}" for s in state["sources"]) |
| `deep-research-critic` | `{{outline}}` / `{{draft}}` / `{{sources_summary}}` | draft 关键字段 |
| `deep-research-polish` | `{{outline}}` / `{{draft}}` / `{{critique_comments}}` / `{{critique_suggestions}}` | critique_comments/suggestions = state["critique_history"][-1] |

所有占位符都是 str（join 字符串或单字段），**不需要** chevron 的 `{{#section}}` 段语法（per spec 002 FR-001 + Constraint #6 一致性）。

### Rationale

- **str-only 占位符**：spec 002 已确立"plan_steps 是 join 字符串而非 list"模式（避免 chevron 渲染错误），spec 004 一致性沿用
- **chevron 客户端渲染**：Langfuse 4.x `text.compile()` 不解析 `{{#var}}` 段，必须客户端 chevron 渲染（per spec 002 FR-001 + [[langfuse-4-text-compile-no-mustache-sections]]）
- **占位符数量控制**：每个 prompt 占位符 ≤ 4 个，避免 chevron 渲染时变量名容易拼错（"{{critic_comments}}" vs "{{critique_comments}}"——1 字符之差）
- **不读内容截断**：sources_with_index 每条 source content 截断到 300 字（避免 LLM context 爆炸，per spec 003 FR-001 类似模式）

### Alternatives Considered

#### A. 让 Langfuse 渲染（不 chevron 客户端）

- **优点**：不用写 chevron 代码；prompt 模板可以更复杂（用 `{{#section}}` 段）。
- **否定原因**：Langfuse 4.x `text.compile()` 仅 `{{var}}` 替换，不解析段；spec 002 已确立 chevron 客户端；spec 004 一致性沿用。

#### B. 把占位符拆成 list（让 chevron 段语法）

- **优点**：prompt 模板可以"for each source"循环渲染。
- **否定原因**：over-engineering——LLM 能理解 join 字符串（"Sources:\n- s1\n- s2\n- s3"），不需要 chevron 段语法。`{{var}}` 替换足够。

### Evidence

- spec 002 FR-001 "Mustache rendered client-side via chevron"
- spec 002 FR-019 "必须使用 chevron 兼容的 Mustache 语法"
- FR-010 "4 个 Langfuse prompt 占位符表"
- [[langfuse-4-text-compile-no-mustache-sections]] 项目记忆

### Action Items

- tasks 阶段在 Langfuse UI 创建 4 个 prompt（手动操作）
- 验证 chevron 渲染：`chevron.render(template, {"user_query": "test", "plan_steps": "1. step1\n2. step2", "sources_summary": "- s1\n- s2"})` 返回正确字符串

---

## R9 · chevron 渲染一致性（join 字符串 vs list）

### Decision

所有传给 chevron 的变量都是 join 字符串或单字段（str），**不传 list / dict**。`prompts.get_prompt(name).compile(**kwargs)` 返回 `prompt`,可迭代值是单字符串而非嵌套结构。

### Rationale

- **Chevron 段语法限制**：Langfuse 4.x `compile()` 不解析 `{{#var}}` 段，所以不能传 list 给 `{{#sources}}...{{/sources}}` 循环（per [R8] + Constraint #6）
- **Join 字符串简化**：所有 list（plan steps / sources / critiques）通过 Python `"\n".join(...)` 转为字符串，LLM 能解析自然语言列表
- **LLM 友好**：join 字符串比 list-of-dict 更易解析（LLM 训练数据里 join 字符串占比远高于 list-of-dict 字符串）

### Alternatives Considered

#### A. 传 list-of-dict 给 chevron（让 LLM 自己解析）

- **优点**：prompt 模板可以更结构化（"for each source: {{title}} {{content}}"）。
- **否定原因**：chevron 段语法不被 Langfuse 4.x 支持；list-of-dict 字符串化后比 join 字符串更长（context 占用大）。

### Evidence

- spec 002 FR-014 "Mustache 渲染：list 段不传 list，传 join 字符串"
- spec 002 NFR-007 + Constraint #6

### Action Items

- 4 个 prompt 的 prompt body 模板里**不**使用 `{{#var}}` 段语法；只用 `{{var}}` 单替换
- chevron 客户端 `prompts.get_prompt(name, label=...).compile(var1=join_str1, var2=join_str2, ...)` 调用

---

## R10 · end-to-end 验证路径

### Decision

10 个 Success Criteria 通过 `uv run python -c "..."` 单行命令 + Postgres `checkpoints` 表查询联合验证（per spec 003 quickstart.md SC-001 ~ SC-010 模式一致）。

### Rationale

- **CLI smoke pattern**：Constitution Principle II "CLI-Grade Smoke Tests" 要求每个模块有 `python -c "..."` 可执行的 smoke；spec 004 quickstart.md 沿用
- **Postgres 端验证**：所有 SC 涉及 state 字段验证（sources / outline / draft / critique_history / iteration_counts / final_report），可从 Postgres `checkpoints` 表读最新 state 断言——避免 mock LLM 的 fidelity 问题
- **避免过早接测试框架**：spec 005 才有 pytest + coverage ≥ 80% 强制；spec 004 阶段 quickstart.md SC 是 contract test，pytest 留给 spec 005

### Alternatives Considered

#### A. spec 004 直接上 pytest

- **优点**：覆盖率高；CI 可机械化。
- **否定原因**：spec 005 才接 pytest（per Constitution Principle III "Coverage gate runs on every PR (added when spec 005 lands pytest)"），spec 004 阶段 pytest 未集成；过早引入让 spec 004 工作量膨胀（pytest fixture + conftest + CI 改造）。

### Evidence

- Constitution Principle II "CLI-Grade Smoke Tests"
- spec 003 quickstart.md 10 个 SC 模式
- spec 003 SC-002 / SC-004 已有 Postgres 端验证 pattern

### Action Items

- quickstart.md 10 个 SC 脚本，每个含 `前置条件` + `单行命令` + `期望 stdout` + `失败模式`
- SC-008 全链路端到端 smoke 是 spec 004 的"完成态守门员"

---

## spec 修正表（spec.md vs research.md）

spec 004 阶段无 spec 修正——所有 Clarifications Q1-Q7 已在 spec 阶段落地，research.md 仅补全"为什么这么决策"的研究证据。详见 [data-model.md §11.5](data-model.md) 状态字段对照表 + [contracts/README.md §C1-C11](contracts/README.md) 契约表。

---

## 总结

spec 004 是 deep research agent **最后一段"做实事"**的 spec。前 3 个 spec 准备基础设施（graph 骨架 / plan 审阅 / research 真实跑），spec 004 完成 write 后 agent 已经可以端到端产出 `final_report` Markdown。

技术栈增量：4 个 LLM role（write/draft/critic/polish）+ 1 个 schema 字段（Critique.suggestions）+ 1 个外层图节点（emit_final_report）+ 4 个 Langfuse prompt。所有增量都符合 Constitution Principle I（purely additive，无破坏性变更）+ Constraint #1-8（无新 env var / 无新依赖）。

预计工作量：3-4 小时（不含 Langfuse UI 手动创建 4 个 prompt 的等待时间）。