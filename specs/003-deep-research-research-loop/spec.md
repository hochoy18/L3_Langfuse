# Feature Specification: Research 阶段（真实 web search + Send 并发 + ReRank）

**Feature Branch**: `[003-deep-research-research-loop]`

**Created**: 2026-10-19

**Status**: Draft

**Input**: User description: "003-deep-research-research-loop。文档：中文。之前的文档：@specs/README.md @specs/001* @specs/002 @src/apps/deep_research/docs/adr/ @src/apps/deep_research/README.md"

**Source of truth**: 本 spec 只覆盖 Phase 3（research 阶段：真实 DashScope web search + Send API 5 路并发 + DashScope ReRank 二次精排 + research 终止循环）。在 spec 001 foundation 与 spec 002 plan-confirm 之上增量实现。设计意图与领域词汇见 `src/apps/deep_research/CONTEXT.md §2.2`、`§4 Operations` 与 `docs/adr/0001-nested-subgraphs.md` / `0002-send-fanout-five-queries.md` / `0004-dashscope-rerank.md`。

---

## Clarifications

### Session 2026-10-19

- **Q1**: 在 L3_Langfuse 当前部署的 Aliyun MaaS gateway（`token-plan.cn-beijing.maas.aliyuncs.com`）上，`dashscope.Generation.call(enable_search=True)` **无法工作**——MaaS 只代理 `/v1/chat/completions` 与 `/v1/models`，对 `enable_search` 静默返回 `InvalidApiKey`（参见 [[dashscope-mmaas-enable-search-needs-bailian-app]] 项目记忆）。spec 003 的 `DashScopeWebSearch._run` 应该走哪条路径？→ A: 走 Bailian（百炼）"web search 应用"通道，调用 `dashscope.Application.call(APP_ID, ...)`（或直接 `httpx` 调 Bailian `/api/v1/apps/{APP_ID}/completion` 端点），凭证读 `DASHSCOPE_WEB_SEARCH_APP_ID` 与 `DASHSCOPE_WEB_SEARCH_APP_TOKEN`（`.env` 已配）。理由：(a) 这是用户已知能跑通的路径；(b) 不新增外部 SDK 依赖（`dashscope` SDK 已经装好）；(c) `dashscope.Application.call()` 是官方推荐的"调用预置应用"入口，比裸 httpx 更稳定。**实装注意**：不要在 production 路径里同时保留 `Generation.call(enable_search=True)` 兜底——那是 footgun，会让 Lesson 0007 实验看到不一致的成功/失败比。`USE_WEB_SEARCH_APP=true|false` 仅作为 dev/CI 旁路开关（默认 `true`，CI 无 App 凭证时设 `false` 抛清晰错误而非走无效的 MaaS 路径）。

- **Q2**: DashScope ReRank 端点（`dashscope.TextReRank.call(model="gte-rerank-v2")`）是否也被 MaaS gateway 拦截？spec 003 的 `DashScopeReRank._run` 应该用什么凭证？→ A: ReRank 走**纯 DashScope SDK**（`dashscope.TextReRank.call(...)`），凭证用 `DASHSCOPE_API_KEY`（与 chat LLM 共享同一个 key）；这是 `dashscope>=1.14.0` 的独立 SDK 端点，**不**走 OpenAI 兼容 proxy。理由：MaaS 项目记忆中**只**记录了 `enable_search` 被拦截；ReRank 不通过 `enable_search` 通道，按 SDK 默认行为应直接打到 DashScope 主站。Bailian app 不提供 ReRank 服务，所以不能走 App 路径。**降级路径**保留 `USE_RERANK=false` 时跳过 ReRank、走纯 LLM Judge（per ADR-0004）；这是用户已确认的旁路开关，不修改 env var 名。

- **Q3**: `gen_query` 节点应该复用 `prompts.get_llm(role="plan")` 的 LLM，还是用 `prompts.get_llm(role="research")` 新建一个？→ A: 必须新建 `get_llm("research")` 实例（spec 003 实现 FR-009 把现有 stub 替换）。理由：(a) role 语义不同——plan 是"分章节规划"（temperature=0.4，结构化 JSON 输出），research 是"生成检索查询字符串"（应更低 temperature 0.2 以保持查询的多样性/聚焦性）；(b) spec 002 已经预留 role 五元组（`plan / research / write / critic / polish`），spec 003 是兑现 "research" 而不是绕过它；(c) 未来 Langfuse UI 上希望分别看 plan LLM 和 research LLM 的 token/span；(d) 不复用 plan role 才能让 Lesson 0007 实验单独调 `research` temperature 评估查询质量。**实装**：research LLM 用 `temperature=0.2`（vs plan=0.5）、model 与 plan 同一 `DASHSCOPE_MODEL` 默认值。

- **Q4**: `gen_query` 产出的查询条数如果**少于** 5（LLM 抖动），Send fan-out 应该发"实际产出的条数"还是"补齐到 5 条"？→ A: Send 实际产出条数（可变 fan-out 1-5 条）。理由：(a) Send API 接受任意长度 list，无需 padding；(b) 5 是 ADR-0002 给的经验最优值，但**不是硬约束**——硬约束是 `iteration_counts["research"] <= 3` 且 `len(sources) >= 8`；(c) LLM 偶尔产出 3 条说明查询足够聚焦，强行 pad 会污染 Source 池；(d) 如果 LLM 产出 0 条（JSON 解析失败或模型拒答），`gen_query` 重试 1 次（per FR-013），仍 0 条抛 `NotYetImplementedError("gen_query produced 0 queries after retry")`。**实装**：`gen_query` 在重试前先 log `[research] gen_query attempt=N returned M queries` 一行供 ops 监控。

- **Q5**: `USE_RERANK=false` 降级路径下，"LLM Judge 给每个 Source 打分"的 LLM 是同一个 `get_llm("research")`，还是 `get_llm("critic")`？→ A: 用 `get_llm("research")`（复用，**不**新增 role="judge"）。理由：(a) judge 与 gen_query 是同一类任务（短文本生成 + JSON 输出），无角色区分价值；(b) critic role（`temperature` 可能更低）是为 write 子图的 draft 评审设计的（spec 004），用在 5-10 word 的 source relevance 上是杀鸡用牛刀；(c) Langfuse UI 上把 judge span 与 gen_query span 放在同一 research 层级下可读性更好。

- **Q6**: `Source.index` 字段在 DashScope 每次返回的 list 里是 1-based 自增（每条 query 独立），但 schema 里 `index` 描述是"Position in the final report's References list (1-based, matches [ref_N])"——这两者不一致时由谁重编号？→ A: `DashScopeWebSearch._run` 接收 DashScope 返回的 `index`（per-query 局部编号）后**立即**改写为 `source_index_offset + i`，其中 `source_index_offset = len(state["sources"])`（在 websearch 节点入口读取——Send 节点收到的是 sub-state，不会拿到完整 sources，所以必须在 Send payload 里附带 offset 或在 websearch 内做基于 thread-local 累加）。最终 ReRank 之后按 score 降序重排再次重编号 `index = 1..N`（全局唯一）。理由：(a) `[ref_N]` 在 FinalReport 中必须全局唯一；(b) 局部 index 不够稳——5 个 query × 3 轮可能有重复的 index=1。**实装注意**：因为 websearch 是 Send 并发，每个 worker 拿到的 state 是 sub-state（不会看到其他 worker 已写入的 sources），所以**先** ReRank 全局聚合，**再**统一重编号（这是 eval_search 的责任而非 websearch 的）。

- **Q7**: research 子图跑到 `sources >= 8 OR iters >= 3` 时，由谁决定循环退出？Send 节点是否需要保留 intermediate sources 列表？→ A: 退出判断在 `eval_search` 之后的条件边 `should_continue_research(state)` 中做（per ADR-0002）；`Send` 不需要保留任何 in-worker 状态——所有 worker 把 `Source` 写到 `state["sources"]`（via `operator.add` reducer——但 spec 001 已澄清 state 不带 reducer，是 overwrite 模式，**这里 websearch 节点必须返回完整 `sources` 列表**：读 `state["sources"]` → 追加新 Source → 返回新 list）。Send fan-out 后 LangGraph 会把所有 worker 的 partial dict 合并到 state，所以"读+追加+返回新 list"是必要 contract。**未在本 spec 决策**：是否需要把 `Send` 改为带 `source_offset` 的 payload 让 worker 自己编号——评估由 plan 阶段再决定，本 spec 采用"统一在 eval_search 重编号"路径。

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 真实 web search 产出 Source 列表（P1）

作为 **deep research agent 的 Researcher**，我能在 research 子图入口被传一份 `state["plan"]` 后，**真实**调 DashScope 完成 N 轮 `gen_query → websearch × N → eval_search`，最终 `state["sources"]` 含 ≥ 8 条有 `score` 字段的 `Source` 实例——证明 DashScope web search 通道已端到端打通。

**Why this priority**：P1，spec 003 的核心交付物。foundation 的 stub 只能产出 2 条 placeholder Source，且没有真实 relevance score；研究循环的"质量-终止"判断（`sources >= 8` 阈值）需要 score 字段才有意义。

**Independent Test**：单次 `python -c "from src.apps.deep_research.graph import run_research; r = run_research({'user_query': '分析 LangGraph 1.2 Send API 在 deep research 中的用法', 'auto_confirm_plan': True}, thread_id='research-iter-001'); print(len(r['sources']), r['sources'][0].score)"` 返回 `>= 8` 且 `r['sources'][0].score` 是 `0.0-1.0` 之间的 float。

**Acceptance Scenarios**：

1. **Given** Langfuse 上存在 `deep-research-gen-query` prompt（label="production"）且 DashScope `DASHSCOPE_API_KEY` / `DASHSCOPE_WEB_SEARCH_APP_ID` / `DASHSCOPE_WEB_SEARCH_APP_TOKEN` 均已配置，**When** User 调 `run_research({'user_query': '<真实研究问题>', 'auto_confirm_plan': True}, thread_id='...')` 跑过 plan + confirm_plan 后进入 research 子图，**Then** `state["sources"]` 在 research 退出时含 **≥ 8** 条 `Source` 实例（不是 stub 的 2 条），每条都有非空 `title` / `url`（HttpUrl）/ `content` / `query_index`，且至少 80% 的 Source 有非 `None` 的 `score`（来自 ReRank，per ADR-0004）。
2. **Given** research 子图跑完一轮 `gen_query → websearch × 5 → eval_search`，**When** 检查 Postgres `checkpoints` 表对应 `thread_id` 的最新 state 快照，**Then** `state["iteration_counts"]["research"] >= 1` 且 `state["research_queries"]` 含 ≥ 5 条 `ResearchQuery`（第一轮产出的 query 列表）。
3. **Given** 同一个 UserQuery，**When** 把 `USE_RERANK=false` 注入环境变量后重跑 SC-001，**Then** 仍返回 `>= 8` 条 Source，但所有 `Source.score` 字段是 `None`，且 `state["search_evaluations"]` 中每条 `SearchEvaluation.evaluator == "llm_judge"`——证明 ReRank 降级路径走通。

---

### User Story 2 - Send API 5 路并发 fan-out（P1）

作为 **Lesson 0007 实验的可观测性需求**，我能从 Postgres `checkpoints` 表看到 research 子图在同一 tick 内**并行**触发了 N 个 websearch 节点（不是串行 5 次）——证明 LangGraph `Send` API 真的在跑并发而不是 5 次顺序调用。

**Why this priority**：P1，ADR-0002 的核心承诺。如果 Send 失败降级为串行，单轮 research latency 从 ~3 秒膨胀到 ~15 秒，3 轮就是 45 秒——Lesson 0007 跑 N 个 query 实验跑不动。

**Independent Test**：日志与 Postgres `checkpoints` 表联合验证——同一 tick 内出现 5 条 `node="websearch"` 的 checkpoint 写入（write timestamp 间隔 < 0.5s），且 `state["sources"]` 一次性含 5 条来自不同 `query_index` 的 Source。

**Acceptance Scenarios**：

1. **Given** `gen_query` 产出 5 条 `ResearchQuery`，**When** Send API fan-out，**Then** LangGraph 在同一 graph step 内**并发**执行 5 次 `websearch` 节点（每个 worker 拿到一份独立的 `Send("websearch", {"query": q, "target_step": i, "iteration": N})` payload），每个 worker 独立调一次 `DashScopeWebSearch._run`。
2. **Given** 5 个 worker 并发执行完成，**When** 观察 Postgres `checkpoints` 表写入顺序，**Then** 5 条 `websearch` 节点的 checkpoint 写入时间戳差 < 0.5 秒（vs 串行的 ~5 秒差），证明并发真的发生了；且 `state["sources"]` 最终长度 ≥ 5（每 worker 至少 1 条 Source）且 `query_index` 0..4 都出现。
3. **Given** 5 个 worker 中有 2 个因 DashScope 抛错而失败（如某个 query 触发了 `InvalidParameter`），**When** Send fan-out 收尾，**Then** **不阻塞**——剩余 3 个 worker 的 Source 仍写入 state，子图继续走到 `eval_search`，`eval_search` 检测到 source 数 < 5 时由 `gen_query` 重试补一轮（per FR-014）；不应因 1/5 worker 失败让整图抛错卡死。

---

### User Story 3 - ReRank 二次精排 + LLM Judge 评分（P1）

作为 **Researcher**，我能把 5 个 websearch 合并出的 Source 列表经 `DashScopeReRank` 二次精排，每个 Source 获得一个 0.0-1.0 的 relevance score，< 0.3 的 Source 被丢弃，剩余的写 `state["sources"]` + `state["search_evaluations"]`——证明"评分-过滤-入库"链路是 research 阶段质量的兜底。

**Why this priority**：P1，ADR-0004 的核心承诺。没有 score 字段，Write 子图（spec 004）就拿不到 `[ref_N]` 排序依据，FinalReport 引用质量不可控。

**Independent Test**：`python -c "from src.apps.deep_research.nodes.research import _eval_search_node; ...; print([s.score for s in result['sources']])"` 返回一组 0-1 之间的 float，且 `state["search_evaluations"]` 长度 = `len(state["sources"])`。

**Acceptance Scenarios**：

1. **Given** `USE_RERANK=true`（默认）且 `dashscope.TextReRank.call(model="gte-rerank-v2")` 可达，**When** `eval_search_node` 接收 ≥ 1 条 Source，**Then** 每条 Source 的 `score` 字段被 ReRank 返回值写为 `0.0-1.0` 的 float，`SearchEvaluation.evaluator == "rerank"`，且 `SearchEvaluation.rationale` 是 ReRank 返回的 `relevance_score` 派生的一句话（例如 `f"ReRank score: {score:.2f}"`，不是 LLM Judge 的 qualitative reason）。
2. **Given** ReRank 返回的 `score < 0.3`，**When** `eval_search_node` 写入 state 前，**Then** 该 Source 被**丢弃**（不出现在 `state["sources"]`），对应 `SearchEvaluation` **也**不写入——避免"有 eval 但没 source"的孤儿记录。
3. **Given** 整轮所有 Source ReRank score 都 `< 0.3`（极少见但可能），**When** eval_search_node 写完 state，**Then** `len(state["sources"]) == 0`，`should_continue_research` 条件边判 `iters < 3` 决定继续迭代（不抛错），下一轮 `gen_query` 重试；如果连续 3 轮（`iteration_counts["research"] >= 3`）仍为 0，终止并抛 `NotYetImplementedError("research: 3 iterations yielded 0 sources after ReRank filter")`（per FR-015）。

---

### User Story 4 - Research 终止循环 + 状态推进（P1）

作为 **Lesson 0007 的实验脚本**，我能用 `auto_confirm_plan=True` 单次 `run_research(...)` 让 research 子图跑过 plan + confirm_plan → research 子图内部循环 → 抵达 write 子图入口，**不抛** stub `NotYetImplementedError`——证明 research 子图对外契约正确（sources 字段已填好、iteration_counts["research"] 已填、write 子图可以接力）。

**Why this priority**：P1，research 阶段的可独立验证的端到端入口。spec 004 在此基础上加 write 循环；如果 spec 003 不能抵达 write 入口，spec 004 没法做集成测试。

**Independent Test**：在 spec 003 完成态下，`run_research({'user_query': '...', 'auto_confirm_plan': True}, thread_id='research-loop-001')` 不抛 `NotYetImplementedError`（research stub 的错），而抛 `NotYetImplementedError("get_llm not yet implemented: write")` 或类似的 write stub 错——证明 research 真的"通过"了。

**Acceptance Scenarios**：

1. **Given** `auto_confirm_plan=True` 且所有 Langfuse prompt / DashScope 凭证就位，**When** `run_research({'user_query': '...'})` 跑过 plan + confirm_plan，**Then** research 子图启动，`gen_query` 产第 1 轮 queries → Send fan-out → eval_search → 条件边判断 `len(sources) < 8 AND iters < 3` → 回 `gen_query` 产第 2 轮，循环直到 `sources >= 8` 或 `iters == 3`，然后**进入 write 子图**；write 子图因为是 stub（spec 004 才接），会在 `get_llm("write")` 处抛 `NotYetImplementedError`——这是预期行为。
2. **Given** research 子图退出时 `len(state["sources"]) >= 8`，**When** 检查 Postgres `checkpoints` 表，**Then** `state["iteration_counts"]["research"]` 等于实际跑的轮数（1/2/3），`state["research_queries"]` 含所有轮次的 queries（合并列表，3 轮满跑 = 15 条）。
3. **Given** research 子图运行时间（wall clock），**When** 跑一次 SC-001，**Then** 平均 P50 latency ≤ 30 秒（5 并发 + ReRank + LLM Judge 各 1 次/轮 × 平均 2 轮），P90 ≤ 60 秒；超出由 UI（spec 005）显示"research 进行中…"占位（spec 003 仅保证不阻塞主线程）。

---

### User Story 5 - USE_RERANK 降级路径（P2）

作为 **节约 token 的实验脚本**，我能通过 `USE_RERANK=false` 环境变量让 `eval_search_node` 跳过 ReRank API 调用，直接用同一个 `get_llm("research")` LLM Judge 给 Source 打分——避免 Lesson 0007 跑 N 个 query 时 ReRank 成本爆掉。

**Why this priority**：P2，是 ADR-0004 的"Consequences 负向"里承诺的旁路开关。如果不实装，Lesson 0007 无法做 `USE_RERANK=true/false` 对照实验。

**Independent Test**：`USE_RERANK=false python -c "..."` 跑 SC-001 仍返回 ≥ 8 条 Source，但 `state["search_evaluations"][0].evaluator == "llm_judge"` 且 `Source.score is None`。

**Acceptance Scenarios**：

1. **Given** `USE_RERANK=false`，**When** `eval_search_node` 执行，**Then** **不**调 `DashScopeReRank._run`，改为调 `get_llm("research").invoke(rendered_prompt)`，prompt template 来自 Langfuse `deep-research-judge` prompt（含 `{query}` + 每个 Source 的 `{title}` / `{content}` / `{url}` 占位符），LLM 返回 JSON `[{"source_index": i, "relevance": 0.X, "rationale": "..."}]`；`Source.score` 字段留 `None`（LLM Judge 不写回 Source.score 而写 SearchEvaluation.relevance，避免和 ReRank 字段语义冲突）。
2. **Given** `USE_RERANK=false` 且 LLM Judge 返回的 JSON 解析失败（schema 不匹配），**When** `eval_search_node` 检测到 `len(parsed) != len(state["sources"])`，**Then** 重试 1 次（同 prompt 重新 invoke），仍失败抛 `NotYetImplementedError("llm judge failed after retry: <reason>")`。
3. **Given** `USE_RERANK=true`（默认），**When** 跑 SC-001，**Then** 行为与 US1-3 完全一致——降级路径**不**应影响默认行为。

---

### Edge Cases

- **Bailian App 调用失败（429/5xx）**：`DashScopeWebSearch._run` 调 `dashscope.Application.call(APP_ID, ...)` 时遇到 HTTP 429 / 5xx，MUST 抛 `NotYetImplementedError("dashscope web search rate limited after 2 retries")`——per spec 002 FR-006(d) 的一致性策略（指数退避 base=1s, max_retries=2, exponential=2x）。失败导致该 worker 不写 Source，Send fan-out 不阻塞；eval_search 检测到 source 不足时由 `gen_query` 重试一轮补齐。
- **Bailian App 凭证缺失（`DASHSCOPE_WEB_SEARCH_APP_ID` 为空）**：`tools.py::DashScopeWebSearch._run` MUST 抛 `RuntimeError("DASHSCOPE_WEB_SEARCH_APP_ID not set")`，**不**静默走 `Generation.call(enable_search=True)` 兜底（那是已知 footgun）。`USE_WEB_SEARCH_APP=false` 旁路开关允许 dev/CI 环境跳过该检查（默认 `true`）。
- **ReRank 调用失败（dashscope.TextReRank 抛 SDKError）**：`DashScopeReRank._run` MUST 把 SDKError 透传为 `NotYetImplementedError("dashscope rerank failed: <code>")`——per ADR-0004 "Consequences 负向"明确"若服务挂掉 eval_search 节点 raise"。**与 websearch 不同**：ReRank 失败**不**重试，直接 raise 让 `eval_search` 上层 `try/except` 捕获并自动 fallback 到 LLM Judge 路径（per FR-017 + Q2 决策）——这是 spec 003 唯一允许的"silent degrade"。
- **LLM Judge 返回负数 relevance（如 `-0.1`）或 > 1（如 `1.5`）**：`eval_search_node` 必须做 `0.0 <= r["relevance"] <= 1.0` 范围校验，超出范围的整批 JSON 视为解析失败，走 1 次重试路径。
- **websearch 返回空 Source 列表（App 端点返回 200 但 `output` 为空）**：`DashScopeWebSearch._run` MUST 抛 `NotYetImplementedError("dashscope web search returned 0 results for query=<q>")`——per ADR-0002 "Consequences 负向"明确"5 个 websearch 全部失败时 sources 列表为空——需要 eval_search 检测并触发 fallback"。Send fan-out 单 worker 失败不阻塞；eval_search 检测到 `len(sources) < 5 * iteration` 时由 gen_query 重试。
- **Send fan-out 单 worker 抛错 vs 整体抛错**：LangGraph Send 的语义是"任一 worker 抛错 → 整体图 step 失败"；spec 003 要求 `websearch` 节点对"已知错误"（per FR-013 列出的 NotYetImplementedError 子类）**返回空 dict**（不抛错），让 worker 自然结束；对"未知错误"（如 KeyError、AttributeError）让其透传让 Send 失败 → 整图失败，便于 review 时发现 bug。
- **同一 Source URL 跨多轮重复出现**：ReRank 后 `eval_search_node` 必须按 `Source.url` 去重（保留 score 最高的版本），重复 URL 的低 score Source 不写 state——避免 FinalReport References 章节出现同一来源两次。
- **research 子图 `iteration_counts["research"]` 由谁 +1**：per spec 002 NFR-002 + foundation Q4 澄清"counter 由 graph.py 入口初始化，节点不动"——**但** spec 003 必须打破这个原则让 research 子图能迭代。具体：spec 003 在 `gen_query` 节点**入口**读 `state["iteration_counts"]["research"]`，先 `+1` 再返回——这样条件边 `should_continue_research` 读到的 counter 是已更新的值，符合 Q4"start a new round"语义。
- **`USE_RERANK` / `USE_WEB_SEARCH_APP` env var 缺失**：两个 env var 都用 `os.environ.get("USE_RERANK", "true").lower() != "false"` 模式判断（默认 true）；缺省行为 = 启用 ReRank + 启用 WebSearch App 通道，**不**抛错。生产部署应显式注入 `USE_RERANK=true` 防止 dev 环境的 false 值泄露到 prod（lesson 0006 同款防御）。

---

## Requirements *(mandatory)*

### Functional Requirements

#### DashScope WebSearch 工具（核心：US1, US2）

- **FR-001**：`tools.py::DashScopeWebSearch._run(query: str, **kwargs) -> list[Source]` MUST 真实接入 Bailian web search 应用：调 `dashscope.Application.call(applet_id=APP_ID, prompt=query, api_key=APP_TOKEN)`（参考 [Q1] 决策），凭证从环境变量 `DASHSCOPE_WEB_SEARCH_APP_ID` / `DASHSCOPE_WEB_SEARCH_APP_TOKEN` 读取（`.env` 已配置）。**严禁**使用 `dashscope.Generation.call(enable_search=True)` 兜底——per [[dashscope-mmaas-enable-search-needs-bailian-app]] 项目记忆，该路径在 MaaS gateway 上必然失败。
- **FR-002**：`DashScopeWebSearch._run` MUST 把 Bailian App 返回的 JSON 转换为 `list[Source]`，每个 `Source` 含 `index: int`（per-query 局部编号 1..N，per [Q6]）、`title: str`、`url: HttpUrl`（Pydantic 校验自动 cast）、`content: str`（snippet 或前 500 字）、`query_index: int`（从 Send payload 传入）。`Source.score` 初始为 `None`（per [Q6] 决策，由 eval_search_node 统一在 ReRank 后赋值）。
- **FR-003**：`DashScopeWebSearch._run` MUST 对 HTTP 429 / 5xx 做指数退避（base=1s, max_retries=2, exponential=2x），仍失败抛 `NotYetImplementedError("dashscope web search rate limited after 2 retries: query=<q>")`。其他 HTTP 错误（400 / 401 / 403）不重试直接透传 SDKError。retry 计数写入日志（`print(f"[websearch] retry attempt={N} query=<q>")`）——spec 006 接 Langfuse hook 前临时用 print 占位（per spec 002 FR-006 一致性策略）。
- **FR-004**：`DashScopeWebSearch._run` MUST 在 `DASHSCOPE_WEB_SEARCH_APP_ID` / `DASHSCOPE_WEB_SEARCH_APP_TOKEN` 缺失时抛 `RuntimeError("DASHSCOPE_WEB_SEARCH_APP_ID not set" / "_TOKEN not set")`，**不**降级到 `Generation.call(enable_search=True)`。`USE_WEB_SEARCH_APP=false` 环境变量（默认 `true`）允许 dev/CI 环境跳过该检查——但跳过路径仍走 Bailian App 调用（仅跳过凭证缺失的 error），不切换到不同 SDK。
- **FR-005**：`DashScopeWebSearch._run` MUST 在 App 返回空 `output`（200 但无结果）时抛 `NotYetImplementedError("dashscope web search returned 0 results for query=<q>")`——per ADR-0002 + [Edge Cases · websearch 返回空] 一致性，让 Send fan-out 单 worker 失败不阻塞。

#### DashScope ReRank 工具（核心：US3, US5）

- **FR-006**：`tools.py::DashScopeReRank._run(query: str, sources: list[Source], **kwargs) -> list[Source]` MUST 真实接入 DashScope ReRank：`dashscope.TextReRank.call(model="gte-rerank-v2", query=query, documents=[s.content or s.title for s in sources], top_n=len(sources), return_documents=True)`，凭证用 `DASHSCOPE_API_KEY`（per [Q2] 决策——ReRank 是独立 SDK 端点不通过 MaaS 代理）。返回结果 MUST 把每个 `Source.score` 字段重写为 ReRank 的 `relevance_score`（float，0.0-1.0），返回**原 list 的引用**（不创建新 list）。
- **FR-007**：`DashScopeReRank._run` MUST 在 `dashscope.TextReRank.call` 抛 SDKError 时把异常包成 `NotYetImplementedError("dashscope rerank failed: <code>")` 并**直接抛出**——per [Edge Cases · ReRank 调用失败] + ADR-0004 一致性策略，由上层 `eval_search_node` try/except 捕获并 fallback 到 LLM Judge 路径（per FR-017）。**不重试 ReRank**（避免 latency 翻倍）。
- **FR-008**：`DashScopeReRank._run` MUST 校验 `len(documents) >= 1`，空 list 时返回原 list（不抛错，因为 ReRank 对空 list 无意义）——这是 `eval_search_node` 边界条件保护，避免不必要的 ReRank API hit。

#### Research 子图节点（核心：US2, US4）

- **FR-009**：`nodes/research.py` MUST 新增 `_gen_query_node(state) -> list[Send]` 真实实现：调 `prompts.get_prompt("deep-research-gen-query", label="production")` 拉 chat template，渲染（chevron 客户端，per spec 002 FR-001 一致性）传入 `user_query`、`plan`（join step titles）、`iteration`（从 `state["iteration_counts"]["research"]` 读），调 `prompts.get_llm("research").invoke(...)` 拿到 JSON 列表，解析为 `list[ResearchQuery]`（每个含 `text: str` + `target_step: int` + `iteration: int`），返回 `[Send("websearch", {"query": q.text, "target_step": q.target_step, "iteration": q.iteration, "query_index": i}) for i, q in enumerate(queries)]`。
- **FR-010**：`prompts.py::get_llm("research")` MUST 返回 `ChatOpenAI` 实例（per [Q3] 决策）：与 `get_llm("plan")` 共用 `DASHSCOPE_API_KEY` / `DASHSCOPE_API_BASE_URL` / `DASHSCOPE_MODEL` env var，但 `temperature=0.2`（vs plan 的 0.4 以保持查询多样性）。其他 role（`write` / `critic` / `polish`）保持 stub 抛 `NotYetImplementedError`——spec 004 才实装。
- **FR-011**：`nodes/research.py` MUST 新增 `_websearch_node(payload: dict) -> dict` 真实实现：从 payload 读 `query` / `query_index` / `iteration`，调 `DashScopeWebSearch._run(query)` 拿到 `list[Source]`，把每个 Source 的 `query_index` 字段写为 payload 的 `query_index`，返回 `{"sources": sources, "research_queries": [ResearchQuery(text=query, target_step=payload["target_step"], iteration=payload["iteration"])]}`。注意：`sources` 是**全量** list（读 `state["sources"]` + 追加），不是 delta——per state schema Q2 澄清（无 `operator.add` reducer）。
- **FR-012**：`nodes/research.py` MUST 把 `_websearch_node` 接入 Send fan-out：`_gen_query_node` 返回 `list[Send]` 后 LangGraph 会在同一 tick 并发执行；外层 `build_research_subgraph` 调用 `research_builder.add_conditional_edges("gen_query", _gen_query_node, ["websearch"])`（Send 路径）+ `research_builder.add_edge("websearch", "eval_search")`（fan-in）。
- **FR-013**：`nodes/research.py` MUST 新增 `_eval_search_node(state) -> dict` 真实实现：合并所有 websearch 产出的 Source（已经在 `state["sources"]`），按 `url` 去重（保留 score 最高的版本，per [Edge Cases · 同一 Source URL 跨多轮重复]），按 `query_index` 分组 ReRank（每组独立调一次 `DashScopeReRank._run`，query 用该 query_index 对应的 ResearchQuery.text），过滤 score < 0.3 的 Source，写 `state["sources"]`（去重+过滤后的全量 list）+ `state["search_evaluations"]`（每个保留的 Source 一条 SearchEvaluation，`evaluator="rerank"`）；最后按 score 降序重排并重编号 `index = 1..N`（per [Q6] 决策）。
- **FR-014**：`nodes/research.py` MUST 实现 `_should_continue_research(state) -> str` 条件边函数：返回 `"gen_query"`（继续迭代）当 `len(state["sources"]) < 8 AND state["iteration_counts"]["research"] < 3`；返回 `"_end_research"`（退出子图到 write）当 `len(state["sources"]) >= 8 OR state["iteration_counts"]["research"] >= 3`。在 `gen_query` 节点**入口**对 `iteration_counts["research"]` 做 `+1`（per [Edge Cases · counter 由谁 +1] + Q4 决策"start a new round"语义）。
- **FR-015**：`eval_search_node` MUST 在连续 3 轮（`iteration_counts["research"] >= 3`）且 `len(sources) == 0` 时抛 `NotYetImplementedError("research: 3 iterations yielded 0 sources after ReRank filter")`，**不**静默退出（避免"什么都没搜到就交付空报告"的 footgun）。抛出后外层 `run_research` 不 catch，由 UI（spec 005）显示"研究失败"提示。
- **FR-016**：`eval_search_node` MUST 对 JSON 解析失败（LLM Judge 路径）/ ReRank SDKError 做 `try/except` 处理：(a) ReRank SDKError → fallback 到 LLM Judge 路径（per FR-017）；(b) LLM Judge JSON 解析失败 → 重试 1 次（同 prompt 重 invoke），仍失败抛 `NotYetImplementedError("llm judge failed after retry: <reason>")`。retry 计数写入日志（`print(f"[eval_search] judge retry attempt={N}")`）。
- **FR-017**：`eval_search_node` MUST 在 `USE_RERANK=false` 时跳过 `DashScopeReRank._run`，改为：(a) 调 `prompts.get_prompt("deep-research-judge", label="production")` 拉 LLM Judge prompt（chat template）；(b) 渲染（chevron）传入 `user_query` + 每个 Source 的 `title + url + content`；(c) 调 `prompts.get_llm("research").invoke(...)`（per Q3 + Q5 决策，复用 research role）拿到 JSON `[{"source_index": i, "relevance": 0.X, "rationale": "..."}]`；(d) 把每个 `Source.score` 字段留 `None`，写 `state["search_evaluations"]` 时 `relevance` = LLM Judge 的 relevance，`evaluator = "llm_judge"`，`rationale` = LLM Judge 的 rationale 字符串。`USE_RERANK=false` 同时 MUST 跳过"按 score 过滤 Source"步骤——所有 Source 都保留（因为 LLM Judge 的 quality 与 ReRank 不在同一刻度）。
- **FR-018**：`build_research_subgraph()` MUST 重新组织节点连接：(a) `START → gen_query`；(b) `gen_query → Send("websearch") → eval_search`（Send 路径）；(c) `eval_search → should_continue_research` 条件边；(d) `should_continue_research("gen_query")` 回 `_gen_query_node` 或 `should_continue_research(END)` 退出子图；(e) `END → END`（LangGraph 子图边界 marker）。**移除** foundation spec FR-005 里的 stub `websearch` 与 `eval_search` 节点（替换为新实现，不保留 stub 双轨）。

#### Langfuse Prompt 创建前置（手动操作）

- **FR-019**：spec 003 在 Langfuse UI 上需**预先**创建 2 个 chat template prompt（label="production"），内容见 `specs/003-deep-research-research-loop/contracts/README.md`：
  | Prompt name | 用途 | 必填占位符 |
  |---|---|---|
  | `deep-research-gen-query` | gen_query 节点 LLM 输入 | `{{user_query}}` / `{{plan_steps}}` / `{{iteration}}` |
  | `deep-research-judge` | USE_RERANK=false 时的 LLM Judge prompt | `{{user_query}}` / `{{sources_json}}` |
  - 必须使用 chevron 兼容的 Mustache 语法（`{{var}}` 替换；不需要 `{{#section}}` 段——plan_steps 与 sources_json 都是 join 字符串而非 list，可直接用 `{{var}}` 展开）。
- **FR-020**：`deep-research-gen-query` 与 `deep-research-judge` prompt 不在 Python 源码中以字符串字面量形式出现（per spec 002 NFR-003 一致性）——便于 Lesson 0006 在 Langfuse UI 上修改 prompt 不需重发版。

#### 错误处理与日志

- **FR-021**：所有 research 节点 MUST 在入口打印一行 `[research] node=<name> iteration=<N> sources_so_far=<M>` 一行日志（per spec 002 章节 V0 一致性）——便于 Lesson 0007 实验 ops 监控 research 阶段进度。spec 006 接 Langfuse hook 后替换为 `langfuse.update_current_span(...)`。
- **FR-022**：所有"已知错误"（websearch 429/5xx/空结果/ReRank SDKError/JSON 解析失败/0 查询产出等）MUST 包成 `NotYetImplementedError` 并抛出；`release` / 静默 `except: pass` 是 forbidden（per foundation spec Q5 + constitution PRINCIPLE IV）。"未知错误"（如 KeyError）让其透传让 Send fan-out 失败，便于 review 时 review bug。

#### 配置与依赖

- **FR-023**：`prompts.py::get_llm("research")` 与 `tools.py::DashScopeWebSearch/DashScopeReRank` MUST 从环境变量读取新凭证：`DASHSCOPE_WEB_SEARCH_APP_ID` / `DASHSCOPE_WEB_SEARCH_APP_TOKEN`（websearch 必需）；`USE_WEB_SEARCH_APP`（默认 `true`，仅 dev/CI 旁路）；`USE_RERANK`（默认 `true`，沿用 ADR-0004）。所有现有 env var（`DASHSCOPE_API_KEY` / `DASHSCOPE_MODEL` / `DASHSCOPE_API_BASE_URL` / `LANGFUSE_*` / `LANGGRAPH_CHECKPOINT_DB`）不变。
- **FR-024**：spec 003 不新增 Python 依赖（`dashscope>=1.14.0` 已在 `pyproject.toml` 锁定）；`Application.call` 是 `dashscope` SDK 内置方法，无需额外 install。

### Key Entities *(include if feature involves data)*

- **ResearchQuery**（已在 `schemas.py`）：单条查询由 `gen_query` LLM 产出，包含 `text: str` / `target_step: int` / `iteration: int`。一个 research iteration 产出多条 ResearchQuery（per Q4 决策 1-5 条）。
- **Source**（已在 `schemas.py`）：单条 web search 结果，包含 `index: int`（per [Q6] 决策：在 eval_search_node 重编号为 1..N 全局唯一）/ `title` / `url` / `content` / `score: float | None`（ReRank 后由 eval_search_node 写值；USE_RERANK=false 时保持 None）/ `query_index: int`（Send payload 传入）。
- **SearchEvaluation**（已在 `schemas.py`）：对一条 Source 的评分，包含 `source_index: int` / `relevance: float` / `rationale: str` / `evaluator: Literal["rerank", "llm_judge"]`。ReRank 路径 `evaluator="rerank"`，LLM Judge 路径 `evaluator="llm_judge"`。
- **ResearchIteration**（state counter）：`state["iteration_counts"]["research"]` 计 research 子图跑了多少轮，由 `gen_query` 节点入口 `+1`（per [Q4] 决策 + foundation Q4 澄清"start a new round"语义）。

### Non-Functional Requirements

- **NFR-001**：research 子图单次运行 P50 wall-clock latency ≤ 30 秒（含 ReRank）、P90 ≤ 60 秒（用户场景假设 5 条 query/轮，平均 2 轮）。若超过 60 秒，UI（spec 005）应显示"research 进行中…"占位（spec 003 仅保证不阻塞主线程）。
- **NFR-002**：`iteration_counts["research"]` 的 increment discipline 严格遵循 Q4 决策"在节点入口 `+1`，不在条件边决策点 `+1`"——便于 future spec 调试时从 Postgres checkpoint 单步推断 counter 状态。
- **NFR-003**：所有 prompt 文本字面值 MUST 从 Langfse 拉取（per spec 002 NFR-003 一致性）。`prompts.py` 不写 prompt body 字面值。
- **NFR-004**：`tools.py` 在 Langfuse / DashScope 不可达时 MUST NOT 静默 fallback——失败抛真实异常，让 research 节点 try/except 捕获并转换为 `NotYetImplementedError` 上抛（per foundation NFR + constitution PRINCIPLE IV）。

---

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**：在 Langfuse UI 提前创建 `deep-research-gen-query` 与 `deep-research-judge` 两个 chat template prompt（label="production"），凭证 `DASHSCOPE_API_KEY` / `DASHSCOPE_WEB_SEARCH_APP_ID` / `DASHSCOPE_WEB_SEARCH_APP_TOKEN` / `LANGFUSE_*` 均配置。运行 `python -c "from src.apps.deep_research.graph import run_research; r = run_research({'user_query': '分析 LangGraph 1.2 Send API 在 deep research 中的用法', 'auto_confirm_plan': True}, thread_id='sc-001'); print(len(r['sources']), sum(1 for s in r['sources'] if s.score is not None))"`，stdout 输出 ≥ `8` 且 ≥ 6（≥ 80% Source 有 score）。
- **SC-002**：Send fan-out 并发验证——运行 SC-001 后查 Postgres `SELECT node, checkpoint_id, (checkpoint->>'ts')::timestamptz FROM checkpoints WHERE thread_id='sc-001' AND node='websearch' ORDER BY checkpoint_id`，前 5 条记录的 timestamp 间隔 < 0.5 秒（vs 串行的 ~5 秒差）。
- **SC-003**：ReRank 评分验证——运行 SC-001 后 `python -c "from src.apps.deep_research.graph import run_research; r = run_research({'user_query': '...', 'auto_confirm_plan': True}, thread_id='sc-003'); print([s.score for s in r['sources']])"` 返回 ≥ 8 个 `0.0-1.0` 之间的 float，且 `r['search_evaluations'][0].evaluator == 'rerank'`。
- **SC-004**：研究循环终止验证——运行 SC-001 后查 Postgres `SELECT iteration_counts->'research' FROM checkpoints WHERE thread_id='sc-001' ORDER BY checkpoint_id DESC LIMIT 1` 返回 1-3 之间的整数，且 `SELECT jsonb_array_length(research_queries) FROM checkpoints WHERE thread_id='sc-001' ORDER BY checkpoint_id DESC LIMIT 1` 返回 ≥ 5（第一轮 ≥ 5 条 query；总条数 = 该值 × `iteration_counts["research"]`，满 3 轮 = 15 条）。
- **SC-005**：`USE_RERANK=false` 降级验证——设置 `USE_RERANK=false` 后重跑 SC-001，`r['sources']` 长度 ≥ 8 但所有 `Source.score is None`，且 `r['search_evaluations'][0].evaluator == 'llm_judge'`。
- **SC-006**：抵达 write 子图入口验证——`run_research({'user_query': '...', 'auto_confirm_plan': True}, thread_id='sc-006')` 在 research 子图退出后**不**抛 `NotYetImplementedError("DashScopeWebSearch not yet implemented...")` 或 `NotYetImplementedError("DashScopeReRank not yet implemented...")` 这类 stub 错，而是抛 `NotYetImplementedError("get_llm not yet implemented: write")`（write stub 错，spec 004 才接）——证明 research 真的"通过"了。
- **SC-007**：Send fan-out 单 worker 失败不阻塞验证——`run_research({'user_query': '...', 'auto_confirm_plan': True}, thread_id='sc-007')` 在中途某 websearch worker 因空结果抛 `NotYetImplementedError("dashscope web search returned 0 results...")` 时，剩余 worker 的 Source 仍写入 state，子图继续跑到 eval_search，eval_search 检测到 source 不足时由 `gen_query` 重试——最终 `len(r['sources']) >= 8`（可能跑了 3 轮才达标）。验证手段：构造一个会触发空结果的 query（如 `_` 单独字符）跑 SC-007 不抛"research failed"错。
- **SC-008**：empty fallback 验证——`run_research({'user_query': '...', 'auto_confirm_plan': True}, thread_id='sc-008')` 在连续 3 轮 ReRank 后 `len(sources) == 0` 时抛 `NotYetImplementedError("research: 3 iterations yielded 0 sources after ReRank filter")`，Postgres `checkpoints` 表保留 `iteration_counts['research'] == 3` 的最终状态——证明 US3 Edge Case "整轮 source 为 0" 不被静默吞掉。
- **SC-009**：Bailian App 凭证缺失验证——把 `DASHSCOPE_WEB_SEARCH_APP_ID` 从 env 移除后跑 SC-001，`run_research` 在 websearch 节点抛 `RuntimeError("DASHSCOPE_WEB_SEARCH_APP_ID not set")`（**不**走 `Generation.call(enable_search=True)` 兜底），证明 [Q1] 决策的 footgun 防御真的生效。
- **SC-010**：ReRank SDKError → LLM Judge fallback 验证——构造一个 mock 让 `DashScopeReRank._run` 抛 `NotYetImplementedError("dashscope rerank failed: InvalidApiKey")`（模拟 SDKError），跑 SC-001 后 `r['search_evaluations'][0].evaluator == 'llm_judge'`、`r['sources'][0].score is None`，且不抛错（fallback 静默生效）。

---

## Assumptions

- Langfuse 服务在 `http://localhost:3000` 可达（来自 `langfuse-deploy/docker-compose.yml`），且 `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` 已在 `.env` 配置（Lesson 0006 同步创建）。
- DashScope 主站凭证 `DASHSCOPE_API_KEY` 已配置（用于 chat LLM + ReRank）；Bailian App 凭证 `DASHSCOPE_WEB_SEARCH_APP_ID` / `DASHSCOPE_WEB_SEARCH_APP_TOKEN` 已配置（用于 web search）——`.env` 已就位（参见 [[dashscope-mmaas-enable-search-needs-bailian-app]]）。
- `deep-research-gen-query` 与 `deep-research-judge` 两个 Langfuse prompt 由开发者**手动创建**（chat 类型，label="production"，含必需占位符）——本 spec 不写创建脚本，假设用户跟随 README 操作；占位符约定见 FR-019。
- spec 003 完成态下 write 子图仍为 stub（foundation FR-005 保留），所以 SC-006 中"抵达 write 入口"以"图走到 write 子图 stub 并抛 NotYetImplementedError(get_llm 'write')"为准，不以"图真的跑完 write"为准。
- Postgres 来自 `langfuse-deploy`（`localhost:5432`，DB=`postgres`，user/pass=`postgres`），复用 foundation spec 已验证的连接串与 advisory lock 机制（per spec 002 FR-017）。
- Send API 在 LangGraph 1.2.11+ 可用（已在 `pyproject.toml` 锁定）——`Send("websearch", {...})` 是 stable API，不需要额外 import 别名。
- User 接受 `temperature=0.2` 的 research LLM（vs spec 002 的 plan LLM `temperature=0.4`）作为查询生成的默认值——若未来 Lesson 0007 实验发现 0.2 太保守可调到 0.3，但本 spec 锁定 0.2 以保证 SC 可重复。
- ReRank 模型固定 `gte-rerank-v2`（per ADR-0004），不引入其他 ReRank 提供商（CoHere / BGE 等）作为对比实验——这是 future spec 范畴。
- `Source.score` 字段在 ReRank 失败 fallback 到 LLM Judge 时保持 `None`（per FR-017 决策）——避免 FinalReport References 章节出现 "score: None" 的视觉污染；用户知道该 Source 走的是 LLM Judge 而非 ReRank，看 `evaluator` 字段即可。

---

## Out of Scope

- Write 子图（outline / draft / critic / polish，spec 004）
- Streamlit UI 与 session_state 接线（spec 005）
- Langfuse `@observe` hook + TraceMetadata（spec 006）
- Plan quality / Search quality / ReRank quality 的 Langfuse score 打分（Lesson 0007 dataset experiment，本 spec 不埋点）
- 动态 ReRank 提供商切换 / 多 ReRank 模型 ensemble（本 spec 锁定 gte-rerank-v2）
- Source 按内容长度 / 时间新鲜度的二次排序（除 score 降序外无其他排序维度）
- 多模态 web search（图、PDF 搜索结果）——本 spec 仅 text search
- Rate limit 自动退避策略的统一抽象（spec 002 FR-006(d) 已锁定 1s/2x/2 retries，本 spec 一致性沿用，**不**抽 utility）

---

## Dependencies

- spec 001（foundation）：包骨架、schemas（Source / ResearchQuery / SearchEvaluation 已 frozen）、state（iteration_counts 初始化在 graph.py 入口）、graph 拓扑、PostgresSaver——本 spec 全部继承。
- spec 002（plan-confirm）：prompts.get_prompt / get_llm("plan") / chevron 渲染 / thread_id advisory lock——本 spec 的 "research" LLM role 与 gen-query / judge prompt 沿用 chevron 客户端渲染模式（per spec 002 FR-001 一致性）。
- `langfuse-deploy` Postgres（`localhost:5432`）：SC-002 / SC-004 的 Postgres 验证基础。
- Langfuse 3.x Python SDK（已在 `pyproject.toml` 锁定）：`get_prompt` / `compile` API 沿用 spec 002 实装。
- DashScope Python SDK（`dashscope>=1.14.0`）：`Generation.call` + `TextReRank.call` + `Application.call`（**新增使用**，spec 003 FR-001）。Aliyun 官方 SDK 不需额外 install。
- Aliyun Bailian 控制台 web search 应用：APP_ID 与 APP_TOKEN 由用户在 Bailian console 手动创建（`https://bailian.console.aliyun.com/`），与 Langfuse prompt 一样属于"前置手动操作"（per FR-019 一致性）。
- LangGraph 1.2.11+（已在 `pyproject.toml` 锁定）：`Send` API + 条件边 + `add_conditional_edges(target_node, edge_fn, {mapping})` 是 Send 路径支持语法。
- ADR-0001（nested sub-graphs）：research 是嵌套子图（节点边界 + 状态隔离 per [Q7] 决策）。
- ADR-0002（Send fan-out 5 queries）：Send API fan-out 路径，5 路并发。
- ADR-0004（DashScope ReRank）：ReRank 评分策略 + `USE_RERANK` 降级。

---

## Notes

- spec 003 是 deep research agent **真正"做研究"**的 spec——前两个 spec 都在准备基础设施（graph 骨架 / plan 审阅）。spec 003 后，agent 已经可以独立产出 ≥ 8 条有评分的 Source，spec 004 在此基础上做写作循环。
- 本 spec 把 [Q1] 的"是否走 Bailian App 路径"决策**前置**到 Clarifications 而非 Edge Cases——因为这是架构层决策（影响 tools.py 主路径），不是边界条件。如果未来 DashScope 把 `enable_search` 升级为 MaaS 兼容能力，可重新评估（开新 ADR）。
- Send fan-out 的"单 worker 失败不阻塞"由 [Edge Cases · Send fan-out 单 worker 抛错 vs 整体抛错] 处理：**已知错误** → 返回空 dict 不抛错；**未知错误** → 透传让 Send 失败。这条边界对 Lesson 0007 实验稳定性至关重要——5 个 query 跑 3 轮 = 15 次 websearch 调用，偶发 1-2 次失败不应让整图崩。
- Source 重编号策略（[Q6] + FR-013）在 eval_search_node 而非 websearch node 做——因为 websearch 是 Send worker 拿不到完整 sources 列表（sub-state 只含 Send payload），eval_search 是 fan-in 节点能拿到完整 state。这是 Send 模型的固有约束。
- spec 003 不引入新的 env var（per FR-023 + constitution Constraint #7）——`USE_RERANK` 是 ADR-0004 已有的（不是新增），`DASHSCOPE_WEB_SEARCH_APP_ID` / `_TOKEN` 是 `.env` 已有的（不是新增），`USE_WEB_SEARCH_APP` 是新增的**旁路开关**（默认 true）——按 constitution 应开 ADR；但 spec 003 视其为"环境配置"而非"架构决策"，不开新 ADR，纳入 `specs/003-deep-research-research-loop/contracts/README.md` 的 Env Var 章节作为 spec-level 决策。
- `dashscope.Application.call(APP_ID, ...)` 是 Alibaba DashScope SDK 1.14+ 新增的"调用预置应用"入口——区别于 `Generation.call`（对话生成）与 `TextReRank.call`（ReRank）；三者都是 `dashscope` 包顶层导出，无需额外 import 别名。
- spec 003 完成后，Lesson 0007 实验链路：plan-confirm 自动化（auto_confirm=True）→ research 真实跑 → write stub 抛错（spec 004 之前）→ 由 Lesson 0007 实验脚本捕获 write 错并标记 experiment = "research-only"——这是 spec 003 完成态的中间验证形态。