# CONTEXT · Deep Research Agent

> 本文件是 deep research agent 的 **领域词汇表**。只定义"是什么"，不写"怎么做"。
> 实现细节见 `docs/adr/`，代码见 `src/`。

---

## 1. Actors（角色）

| 术语 | 定义 |
|---|---|
| **User** | 提交研究问题、审阅 plan、阅读最终报告的人 |
| **Researcher** | agent 在 research 子图里的"人格"——负责生成查询、做 web search、评估搜索结果 |
| **Editor** | agent 在 write 子图里的"人格"——负责 outline、起草、润色 |
| **Critic** | agent 在 write 子图里的"人格"——负责评审 draft 给出 quality score（与 Editor 是不同人格，由 prompt 区分）|

---

## 2. Entities（实体）

### 2.1 流程输入 / 输出

| 术语 | 定义 |
|---|---|
| **UserQuery** | User 提交的研究问题（一个自然语言 string）|
| **Plan** | agent 对 UserQuery 拆解出的章节化研究方案（结构化 list，**非**自由文本）|
| **PlanStep** | Plan 中的一节——一个研究主题（与最终报告的章节 1:1 对应）|
| **FinalReport** | agent 输出的 Markdown 研究报告（带 `[ref_N]` 行内引用 + `## References` 章节）|

### 2.2 Research 阶段的产物

| 术语 | 定义 |
|---|---|
| **ResearchQuery** | 一次 web 搜索的查询字符串（由 PlanStep 派生）|
| **Source** | 一次 web 搜索的原始结果（包含 url / title / content / score 等字段；**Source 是事实**）|
| **SearchEvaluation** | 对一个 Source 的相关性 / 质量评估（包含 score 0-1 + reason）|
| **ResearchIteration** | 研究循环的一轮迭代（gen_query → Send websearch × 5 → eval_search 一次）|

### 2.3 Write 阶段的产物

| 术语 | 定义 |
|---|---|
| **Outline** | 由 plan + sources 生成的报告大纲（Markdown 结构骨架）|
| **Draft** | 根据 outline + sources 填充的初稿 |
| **Critique** | Critic 对 Draft 的评审意见（包含 quality score 0-1 + 多维度反馈 + 改进建议）|
| **PolishedDraft** | 经过一轮 polish 改进后的 Draft |
| **WriteIteration** | 写作循环的一轮迭代（critic_review → polish 一次）|

### 2.4 关联实体

| 术语 | 定义 |
|---|---|
| **Reference** | FinalReport 中的 `[ref_N]` 行内引用——**指向**一个具体的 Source。Reference 不是独立实体，是 Source 在报告中的视图（id 是 `Source.index`）|

---

## 3. Sub-graphs（子图）

| 术语 | 定义 |
|---|---|
| **OuterGraph** | 顶层图，5 个节点（spec 004 + `emit_final_report`）：`plan` → `confirm_plan` → `research` → `write` → `emit_final_report` |
| **ResearchSubgraph** | 嵌套在 OuterGraph `research` 节点下的子图（gen_query → Send[websearch×5] → eval_search，可循环）|
| **WriteSubgraph** | 嵌套在 OuterGraph `write` 节点下的子图（outline → draft → critic_review ⇄ polish，可循环；终止条件 `critic_score >= 0.8 OR write_iters >= 2`）|
| **emit_final_report_node** | 外层图第 5 节点（**不**在 WriteSubgraph 内，per [Q6]）；把 polished draft + sources 重整为合法 Markdown：行内 `{{cite_N}}` → `[ref_N]` + 末尾 `## References` 章节 |

---

## 4. Domain Operations（领域操作）

| 术语 | 输入 | 输出 |
|---|---|---|
| **generatePlan** | UserQuery | Plan |
| **confirmPlan** | Plan + User 反馈 | (新 Plan) 或 "approve" 信号 |
| **generateQueries** | PlanStep | list[ResearchQuery]（默认 5 条）|
| **webSearch** | ResearchQuery | list[Source]（含 DashScope 返回的 index）|
| **evaluateSearch** | list[Source] + UserQuery | list[SearchEvaluation] + 筛选后的 Source |
| **generateOutline** | Plan + list[Source] | Outline ✅ (spec 004 实装，h2 章节数 == len(plan)） |
| **generateDraft** | Outline + list[Source] | Draft ✅ (spec 004 实装，draft 含 `{{cite_N}}` 占位符；retry 1× on placeholder_count=0）|
| **reviewCritic** | Draft | Critique（带 quality score）✅ (spec 004 实装，JSON schema 含 `score` / `comments` / `suggestions` / `iteration`）|
| **polishDraft** | Draft + Critique | PolishedDraft ✅ (spec 004 实装；"尽力改进"哲学——空 draft / >50% 缩减**不抛错**）|
| **emitReport** | (final Draft + list[Source]) | FinalReport（带 `[ref_N]` 引用）✅ (spec 004 实装，行内 `{{cite_N}}` → `[ref_N]` + `## References` + Source.index 重复静默跳过）|

---

## 5. External Concepts（外部概念 — 仅命名，不实现）

| 术语 | 定义 |
|---|---|
| **DashScopeWebSearch** | 阿里云百炼 DashScope 提供的"搜索增强生成"能力（`Generation.call(enable_search=True)`）。**不是**独立 API，是 Generation 的开关 |
| **DashScopeReRank** | DashScope 提供的精排服务（`TextReRank.call`），返回 guaranteed relevance score |
| **LangGraphState** | LangGraph 的 TypedDict 状态容器（包含本 agent 的所有实体字段）|
| **LangGraphSend** | LangGraph 的 fan-out 原语（一个节点动态产出多个并发 worker）|
| **PlanConfirmation** | 由 `interrupt()` 触发的暂停状态——等待 User 决策 |

---

## 6. Policies（领域规则 — 不写实现）

| 术语 | 定义 |
|---|---|
| **ResearchTerminationPolicy** | ResearchSubgraph 的停止规则：`len(sources) >= 8` OR `research_iters >= 3` |
| **WriteTerminationPolicy** | WriteSubgraph 的停止规则：`critic_quality >= 0.8` OR `write_iters >= 2` |
| **CitationPolicy** | FinalReport 中每个事实主张后跟 `[ref_N]`，引用编号 N 与对应 Source 的 DashScope `index` 对齐 |
| **ReferenceListPolicy** | FinalReport 末尾 `## References` 章节列出所有被引用的 Source（含 title / url / score）|

---

## 7. 关键术语澄清（avoid 混淆）

| 易混术语 | 区别 |
|---|---|
| **Source vs Reference** | Source = 原始搜索结果（事实）。Reference = FinalReport 中的 `[ref_N]` 引用（指向 Source 的视图）。**Reference 是 Source 的子集视图，不是独立实体** |
| **Plan vs Outline** | Plan = 研究**方案**（章节 + 主题 + 搜索方向）。outline = 报告**大纲**（章节 + 标题层级 + 论述点）。Plan 在 research 之前；outline 在 research 之后 |
| **Draft vs FinalReport** | Draft = write 子图的中间产物（无引用）。FinalReport = 经 emitReport 加工后带 `[ref_N]` 引用的最终输出 |
| **Critique vs SearchEvaluation** | 都是 LLM-as-Judge 输出，但 **Critique 评的是 Draft**（写得好不好），**SearchEvaluation 评的是 Source**（搜得准不准）。两者数据结构和用途都不同 |
| **ResearchIteration vs WriteIteration** | ResearchIteration 是一次 `gen_query → websearch → eval_search`。WriteIteration 是一次 `critic_review → polish`。**两者计数独立**，分别用 `iteration_counts["research"]` 和 `iteration_counts["write"]` |

---

## 8. 范围外（Out of Scope）

- 多模态输入（图、PDF 解析）
- 多语言报告（当前默认中文输出，但未做语言切换机制）
- 流式输出（current iteration 一次性返回 FinalReport）
- 实时协作（多人同时编辑同一研究）
- 长时任务队列（>10 分钟的研究任务跑不了一轮 interrupt → resume 链路）

---

## 9. 关联文档

- 设计决策（why）：`docs/adr/0001` ~ `0006`
- 实现：`src/apps/deep_research/`
- Langfuse 可观测性预留：见 `docs/adr/` 中各文件的"Consequences"章节