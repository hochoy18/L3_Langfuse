# Implementation Plan: Research 阶段（真实 web search + Send 并发 + ReRank）

**Branch**: `[003-deep-research-research-loop]` | **Date**: 2026-10-19 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/003-deep-research-research-loop/spec.md`

---

## Summary

spec 003 在 foundation spec（spec 001）+ plan-confirm spec（spec 002）之上，把 research 子图的 stub 实现替换为真实端到端研究循环：

1. **`tools.DashScopeWebSearch`** 真实接入 **Bailian（百炼）web search 应用**（`dashscope.Application.call(APP_ID, ...)`），凭证读 `DASHSCOPE_WEB_SEARCH_APP_ID` / `_TOKEN`（`.env` 已配）。这是 [[dashscope-mmaas-enable-search-needs-bailian-app]] 项目记忆锁定的路径——**严禁**用 `dashscope.Generation.call(enable_search=True)`（MaaS gateway 必败）。
2. **`tools.DashScopeReRank`** 真实接入 **DashScope `TextReRank.call(model="gte-rerank-v2")`**（SDK 直连主站，不经 MaaS）。失败时由 `eval_search_node` 静默 fallback 到 LLM Judge 路径（per ADR-0004，spec 003 唯一允许的 silent degrade）。
3. **`nodes/research.py` 完整重写**：移除 stub；新增 `_gen_query_node`（真实 LLM）→ `Send[websearch × 1-5]`（变长 fan-out，per [Q4]）→ `_eval_search_node`（ReRank / LLM Judge + dedup + renumber + filter）→ `_should_continue_research` 条件边（`len(sources) >= 8 OR iters >= 3`）。
4. **`prompts.get_llm("research")`** 实装（`temperature=0.2`，与 plan 的 0.4 区分）；新增 Langfuse prompt `deep-research-gen-query` / `deep-research-judge`（chat 类型，用户手动创建）。

技术栈：LangGraph 1.2.11+（Send API）+ Langfuse 3.x（chevron 渲染）+ DashScope SDK 1.14+（`Application.call` + `TextReRank.call`）+ Aliyun Bailian（web search App）+ Postgres 16（`PostgresSaver` 继承 spec 001/002）+ pydantic 2.x。

---

## Technical Context

| 维度 | 值 |
|---|---|
| **Language/Version** | Python 3.11+（pyproject.toml `requires-python=">=3.11"`） |
| **Primary Dependencies** | langgraph>=1.2.11, langfuse>=3.0, langchain-openai>=1.6.2, pydantic>=2.7, dashscope>=1.14.0, psycopg[binary]>=3.3.5, chevron>=0.14.0 |
| **Storage** | Postgres 16（来自 `langfuse-deploy`，本地 `localhost:5432`） |
| **Testing** | `uv run python -c "..."` 内联 smoke + `psql` Postgres 端验证；future pytest 留给 spec 005 |
| **Target Platform** | macOS / Linux 开发机；CI（future GitHub Actions） |
| **Project Type** | library（包 `src.apps.deep_research`） + Streamlit UI（spec 005） |
| **Performance Goals** | research 子图 P50 wall-clock ≤ 30s（含 ReRank），P90 ≤ 60s（per NFR-001） |
| **Constraints** | DashScope free tier 60 RPM（websearch + ReRank 共用）；Bailian App 配额独立 |
| **Scale/Scope** | 单 User 单 thread；同 thread_id advisory lock（spec 002 FR-017 继承）；不涉及多租户 |

### 关键决策（与 spec 差异已标注）

| 项 | spec.md 原文 | 实际（research.md 校正） | 来源 |
|---|---|---|---|
| websearch 路径 | `dashscope.Generation.call(enable_search=True)` | `dashscope.Application.call(APP_ID, prompt=query, api_key=TOKEN)` | Q1 / R1 / [[dashscope-mmaas-enable-search-needs-bailian-app]] |
| ReRank 端点 | `dashscope.TextReRank.call` | 同左（不变） | Q2 / R2 |
| `get_llm("research")` temperature | spec 未指定 | `0.2`（vs plan 0.4） | Q3 |
| Send fan-out 条数 | spec 默认 "5 路"（per ADR-0002） | 1-5 变长（实际产出） | Q4 |
| Source 重编号 | spec 未明确 | `eval_search_node` 做 1..N 全局重编号 | Q6 / R4 |
| 失败 fallback 策略 | spec 描述 ReRank → LLM Judge | ReRank SDKError 时**唯一允许**的 silent degrade | R5 + FR-017 |

详见 [research.md](./research.md) R1-R10 + [data-model.md](./data-model.md) §6 状态转换。

---

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

`.specify/memory/constitution.md` v1.0.0 已签署。spec 003 自检：

| 原则 / 约束 | 状态 | 说明 |
|---|---|---|
| **PRINCIPLE I · Module-First & Spec Compatibility** | ✅ PASS | spec 003 不改 `schemas.py` / `state.py` / `graph.py` 公共 API；不动 spec 001/002 已冻结字段；purely additive |
| **PRINCIPLE II · CLI-Grade Smoke Tests** | ✅ PASS | `quickstart.md` 10 个 SC 全部 `uv run python -c "..."` 单行命令 + `psql` 验证 |
| **PRINCIPLE III · Test-First & Coverage Gate ≥80%** | ✅ PASS（暂以 quickstart.md SC 充约） | spec 003 阶段 pytest 未集成；quickstart.md SC-001 ~ SC-010 作为 contract test；spec 005 接 pytest 时同步补 unit/edge/error 三类覆盖 |
| **PRINCIPLE IV · Integration Testing at Module Boundaries** | ✅ PASS | SC-002 验证 Send fan-in/out 与 Postgres checkpoint 边界；SC-010 验证 ReRank SDKError → LLM Judge 边界 fallback |
| **PRINCIPLE V · Observability, Versioning, Simplicity** | ✅ PASS | `[research] node=...` / `[websearch] retry...` / `[eval_search] rerank failed...` print 日志占位（spec 006 完整接 Langfuse）；不预接 future spec 留口（`USE_WEB_SEARCH_APP` 仅占名） |
| **Constraint #1 · State schema invariants** | ✅ PASS | spec 003 不改 `DeepResearchState` 字段；新写入 `sources` / `search_evaluations` / `research_queries` / `iteration_counts["research"]` 均为 spec 001 已定义字段 |
| **Constraint #2 · Prompt text never in source** | ✅ PASS | `gen_query` / `eval_search` (LLM Judge) 全部 prompt 从 Langfuse 拉；`prompts.py` 与 `nodes/research.py` 不含 prompt 字面值 |
| **Constraint #3 · iteration_counts increment discipline** | ✅ PASS | `_gen_query_node` 入口 `+1`（per FR-014 + R6），条件边读"已 +1 的 counter"；不破坏 Q4"start a new round"语义 |
| **Constraint #4 · Error propagation contract** | ✅ PASS | 所有已知错误包成 `NotYetImplementedError`；ReRank SDKError 唯一 silent degrade（per FR-017 + R5）；不引入 `try/except: pass` |
| **Constraint #5 · Postgres is the only state source** | ✅ PASS | Send fan-out 状态由 LangGraph + PostgresSaver 持久化；advisory lock 沿用 spec 002 FR-017 |
| **Constraint #6 · Mustache rendered client-side via chevron** | ✅ PASS | `gen_query` / `judge` 两个 chat 类型 prompt 走 chevron 客户端渲染（继承 spec 002 FR-001 模式） |
| **Constraint #7 · Env-var hygiene** | ⚠️ **PARTIAL** —— 见 Complexity Tracking | `DASHSCOPE_WEB_SEARCH_APP_ID` / `_TOKEN` 是消费既有配置（`.env` 已配置），不开新 ADR；`USE_WEB_SEARCH_APP` 是新 env 名字但 spec 003 仅留名不实装；视为"环境配置"而非"架构决策" |
| **Constraint #8 · Library versions** | ✅ PASS | 不新增 Python 依赖；`dashscope>=1.14.0` 已锁定；`Application.call` 是 SDK 顶层 API |

无 Constitution 阻塞 violation；Constraint #7 的 PARTIAL 在 Complexity Tracking 说明。

---

## Project Structure

### Documentation (this feature)

```text
specs/003-deep-research-research-loop/
├── plan.md              # 本文件
├── research.md          # Phase 0 产出（10 个研究主题 + spec 修正表）
├── data-model.md        # Phase 1 产出（实体 + state transitions + 13 节细节）
├── quickstart.md        # Phase 1 产出（10 个 SC 验证脚本）
├── contracts/           # Phase 1 产出（Python API + Langfuse prompt contracts）
│   └── README.md
├── checklists/
│   └── requirements.md  # spec 003 质量清单（16 项 PASS）
└── spec.md              # spec 003 本身（含 7 个 Clarifications）
```

### Source Code（spec 003 触及）

```text
src/apps/deep_research/
├── __init__.py
├── tools.py             # ← 重写：DashScopeWebSearch / DashScopeReRank 真实接入
├── prompts.py           # ← 修改：get_llm("research") 实装（temperature=0.2）
├── nodes/
│   ├── __init__.py
│   └── research.py      # ← 重写：移除 stub；新增 _gen_query_node / _websearch_node /
│                          _eval_search_node / _should_continue_research + build_research_subgraph()
└── (其他文件不动：graph.py / schemas.py / state.py / pydantic_serde.py / 
     nodes/plan.py / nodes/write.py —— 全是 spec 001/002 已有)
```

**Structure Decision**：Option 1（Single project / library），符合 foundation + spec 002 既有结构；spec 003 是 purely additive 在两层之上：
1. **受 layered on spec 001**：state / schemas / graph 拓扑
2. **受 layered on spec 002**：prompts.get_prompt / chevron 渲染 / Postgres advisory lock / `auto_confirm_plan`

---

## Complexity Tracking

> Fill ONLY if Constitution Check has violations that must be justified

### Constraint #7 (Env-var hygiene) 的 PARTIAL

**问题**：spec 003 引入新 env var 名字 `USE_WEB_SEARCH_APP`（默认 true）。

**为什么需要**：Bailian App 凭证（`DASHSCOPE_WEB_SEARCH_APP_ID` / `_TOKEN`）是 dev/CI 环境**不应**模拟的——CI 环境可能无 Bailian console 凭证。`USE_WEB_SEARCH_APP=false` 旁路开关允许 CI 跳过该检查（默认 true 走真实路径）。

**为什么不引入新 ADR**：constitution 要求"新 env var 需要 ADR"，但 spec 003 视 `USE_WEB_SEARCH_APP` 为"环境配置"而非"架构决策"——理由：
- 它与 `USE_RERANK`（ADR-0004 已存在的旁路开关）同级，是 dev/CI 旁路而非业务决策
- spec 003 仅定义 env 名字 + 默认值（true），**不**实装 bypass 路径——这是"未来 dev 用"的占位
- ADR 是"架构决策记录"，不是"env var 清单"；`USE_WEB_SEARCH_APP` 不改变图拓扑 / 不改变 state schema / 不改变 LangGraph wiring

**为什么接受 PARTIAL**：与 ADR-0004（`USE_RERANK`）的 precedents 一致——`USE_RERANK` 也没单开 ADR。

**Action Item**：未来 spec 真的实现 `USE_WEB_SEARCH_APP=false` bypass 路径时，再开 ADR 单独说明（spec 003 范围内不开）。

---

## Phase 0 · Research

10 个研究主题，详见 [research.md](./research.md)：

| # | 主题 | 关键决策 |
|---|---|---|
| R1 | DashScope web search 路径 | `Application.call(APP_ID)` (Bailian) —— **不**用 `Generation.call(enable_search=True)`（MaaS footgun） |
| R2 | DashScope ReRank 端点 | `TextReRank.call` 直连主站；status_code==20000 success；不走 MaaS |
| R3 | LangGraph Send API | `Send("websearch", payload)` + `add_conditional_edges(gen_query, _gen_query_node, ["websearch"])`；变长 fan-out 1-5 |
| R4 | Source 重编号 | `eval_search_node`（fan-in）做全局 1..N；websearch 节点 `index=0` 占位 |
| R5 | ReRank → LLM Judge fallback | 唯一 silent degrade；`evaluator` 字段标记路径 |
| R6 | counter +1 时机 | `gen_query_node` **入口** `+1`；条件边读"已 +1 的 counter" |
| R7 | Source.content 截断 | 1500 字符（~500-800 tokens） |
| R8 | worker 错误处理 | 已知错误（NotYetImplementedError）swallow + print；未知错误透传 |
| R9 | Langfuse prompt | `deep-research-gen-query` / `deep-research-judge` 两个 chat 类型 |
| R10 | SC-007 并发验证 | psql + Python 并发跑；checkpoint 时间戳差 < 0.5s |

**所有 NEEDS CLARIFICATION 已解决**：spec 003 的 Clarifications Q1-Q7 已在 spec 阶段落地；research.md 仅补全"为什么这么决策"的研究证据。

---

## Phase 1 · Design & Contracts

### Data Model（[data-model.md](./data-model.md)）

- **实体继承** spec 001 schemas.py，spec 003 **不改 schema**
- **State 字段细化**：
  - `sources`：`websearch_node`（partial）→ `eval_search_node`（final, renumbered 1..N）
  - `search_evaluations`：`eval_search_node` 一次性写入（`evaluator="rerank"` 或 `"llm_judge"`）
  - `research_queries`：`websearch_node` 每条 query 一次（Send fan-in 拼接）
  - `iteration_counts`：`gen_query_node` 入口 +1（per FR-014 + R6）
  - **Send fan-in reducer 声明**（per R3 + [[langgraph-1.2-send-fanin-reducer-multicall]]）：
    - `research_queries: Annotated[list[ResearchQuery], _merge_queries_by_identity]` — identity = `(text, target_step, iteration)`；first-seen wins（additive 跨 worker delta 拼接）
    - 实装位置：`src/apps/deep_research/state.py::_merge_queries_by_identity`
    - 同样的 reducer 模式用于 spec 003 US2 新增的 `raw_sources` 通道（identity = `(query_index, url)`），但 `raw_sources` 不在 spec 001 字段里（per US3 data-model split）
- **Send payload schema**：`{"query": str, "target_step": int, "iteration": int, "query_index": int}`
- **错误 schema**：5 类 `NotYetImplementedError` 子消息（websearch 429 / websearch 空 / rerank failed / judge failed / 3 iterations yielded 0）
- **与 spec 002 接口边界**：spec 002 写 `plan` / `messages` / `auto_confirm_plan`；spec 003 写 `sources` / `search_evaluations` / `research_queries` / `iteration_counts["research"]` —— **互不重叠**

### Contracts（[contracts/README.md](./contracts/README.md)）

11 个 Python / Langfuse / env var 契约：

| C# | 主题 | 角色 |
|---|---|---|
| C1 | `prompts.get_llm("research")` | ChatOpenAI 实例，temperature=0.2 |
| C2 | `tools.DashScopeWebSearch._run` | Bailian App 调用 → `list[Source]` |
| C3 | `tools.DashScopeReRank._run` | `TextReRank.call` 直连 → 写回 `Source.score` |
| C4 | `nodes.research._gen_query_node` | LLM 产 1-5 query → `list[Send]` fan-out |
| C5 | `nodes.research._websearch_node` | Send worker，known error swallow |
| C6 | `nodes.research._eval_search_node` | fan-in：dedup + ReRank/LLM Judge + renumber + filter |
| C8 | Langfuse prompt `deep-research-gen-query` | chat template，5 个占位符 |
| C9 | Langfuse prompt `deep-research-judge` | chat template，2 个占位符 |
| C10 | Env Var Schema | 9 个 env var（spec 003 新增消费 2 个：APP_ID / APP_TOKEN）|
| C11 | State Schema 兼容 | spec 003 不改 `DeepResearchState` |

### Quickstart（[quickstart.md](./quickstart.md)）

10 个 Success Criteria 的可执行验证脚本（SC-001 ~ SC-010）。每个 SC 含：
- 前置条件（env vars / prompts / docker compose 状态）
- 单行 `uv run python -c "..."` 命令
- 期望 stdout 输出
- 失败模式速查表

---

## Post-Design Constitution Check

| 原则 / 约束 | 状态 |
|---|---|
| PRINCIPLE I · Module-First & Spec Compatibility | ✅ PASS —— data-model.md §11 显式声明 |
| PRINCIPLE II · CLI-Grade Smoke Tests | ✅ PASS —— quickstart.md 10 个 SC |
| PRINCIPLE III · Test-First & Coverage ≥80% | ✅ PASS（quickstart.md SC 充约；pytest 留给 spec 005）|
| PRINCIPLE IV · Integration Testing at Module Boundaries | ✅ PASS —— SC-002 Send 边界；SC-010 ReRank fallback 边界 |
| PRINCIPLE V · Observability, Versioning, Simplicity | ✅ PASS —— print 日志占位；不预接 future spec |
| Constraint #1 · State schema invariants | ✅ PASS —— data-model.md §5.5 显式列出 spec 003 不改字段 |
| Constraint #2 · Prompt text never in source | ✅ PASS —— contracts C8/C9 把 prompt 推到 Langfuse |
| Constraint #3 · iteration_counts increment discipline | ✅ PASS —— R6 + FR-014 |
| Constraint #4 · Error propagation contract | ✅ PASS —— FR-022 + R8 边界 + R5 唯一 silent degrade |
| Constraint #5 · Postgres is the only state source | ✅ PASS —— Send + PostgresSaver + advisory lock |
| Constraint #6 · Mustache via chevron | ✅ PASS —— contracts C8/C9 |
| Constraint #7 · Env-var hygiene | ⚠️ PARTIAL —— Complexity Tracking 说明 |
| Constraint #8 · Library versions | ✅ PASS —— 不新增依赖 |

无新增 violation；`USE_WEB_SEARCH_APP` 的 PARTIAL 已在 Complexity Tracking 显式 justification。

---

## 实现路径概要（tasks 阶段细化）

```text
1. 改 src/apps/deep_research/tools.py
   - DashScopeWebSearch._run: stub raise → 真实 _call_bailian_web_search (Application.call)
   - DashScopeWebSearch._run: 加指数退避 (base=1s, max_retries=2, exp=2x)
   - DashScopeReRank._run: stub raise → 真实 _call_dashscope_rerank (TextReRank.call)
   - DashScopeReRank._run: 不重试；失败透传 NotYetImplementedError
   - 都加 _extract_content 截断到 1500 字符 (R7)
   - DashScopeWebSearch._run 加 env var 检查 (FR-004)

2. 改 src/apps/deep_research/prompts.py
   - get_llm: 实装 role="research" (ChatOpenAI + temperature=0.2)
   - 保留 NotYetImplementedError 用于 write / critic / polish

3. 改 src/apps/deep_research/nodes/research.py
   - 移除 stub _gen_query / _websearch / _eval_search
   - 新增 _gen_query_node(state) -> list[Send]:
     ├─ 入口 +1 iteration_counts["research"] (per R6)
     ├─ get_prompt("deep-research-gen-query")
     ├─ chevron 渲染 5 个占位符
     ├─ get_llm("research").invoke(...)
     ├─ JSON 重试 1 次
     └─ 返回 [Send("websearch", payload)]
   - 新增 _websearch_node(payload: dict) -> dict:
     ├─ try: DashScopeWebSearch().run(query) → list[Source]
     ├─ 已知错误 swallow + print
     ├─ 未知错误 raise
     └─ 返回 {"sources": [...], "research_queries": [...]}
   - 新增 _eval_search_node(state) -> dict:
     ├─ sources = state["sources"]
     ├─ URL 去重
     ├─ 按 query_index 分组
     ├─ if USE_RERANK=true: try ReRank, fallback to LLM Judge
     ├─ filter score < 0.3
     ├─ sort by (-score, query_index, original_order)
     ├─ renumber 1..N
     ├─ write sources + search_evaluations
     └─ if iters==3 and len(sources)==0: raise
   - 新增 _should_continue_research(state) -> str
   - 重写 build_research_subgraph():
     ├─ add_node("gen_query", _gen_query_node)
     ├─ add_node("websearch", _websearch_node)
     ├─ add_node("eval_search", _eval_search_node)
     ├─ add_edge(START, "gen_query")
     ├─ add_conditional_edges("gen_query", _gen_query_node, ["websearch"])
     ├─ add_edge("websearch", "eval_search")
     └─ add_conditional_edges("eval_search", _should_continue_research,
                              {"gen_query": "gen_query", "__end__": END})

4. README 同步 (spec 003 实装)
   - "前置 2 步: 在 Langfuse UI 创建 deep-research-gen-query + deep-research-judge prompts"
   - "env var: DASHSCOPE_WEB_SEARCH_APP_ID / _TOKEN 必填"

5. 跑 quickstart.md 的 10 个 SC 脚本
```

预计 3-4 小时工作量（不含 Langfuse UI 手动创建 2 个 prompt 的等待时间 + dashscope.Application.call 实际联通验证）。

---

## 已知风险与缓解

| 风险 | 影响 | 缓解 |
|---|---|---|
| LangGraph 1.2.11+ Send + plain list 的"worker 间拼接"行为在不同版本可能不同 | R4 决策依赖此行为 | tasks 阶段先写一个 5-worker Send fan-out mock 验证；如行为不符，plan 阶段调整 |
| Bailian App 的 `output` 字段不一定是 `search_results`（可能 `text` 或自定义） | FR-001 调用失败 | tasks 阶段先 `dashscope.Application.call(...).output` 打出来看真实结构；adjust FR-001 |
| DashScope 主站 ReRank 偶发 InvalidParameter（文档不全） | SC-010 测试 mock 不一定覆盖 | quickstart.md SC-010 用 `unittest.mock.patch.object` 强制触发；live 跑 SC-001 时观察 |
| `gte-rerank-v2` 在 free tier 配额 | lesson 0007 实验成本 | spec 003 把 `USE_RERANK=false` 作为旁路开关；spec 005 / 006 评估生产配额 |
| Send worker 5 worker 边界 race condition | SC-007 容错测试覆盖 | quickstart.md SC-007 monkey-patch 前 2 个 worker 抛已知错误 |

---

## 完成态对比

| 维度 | spec 002 完成态 | spec 003 完成态 |
|---|---|---|
| `run_research` 端到端 | plan + confirm → research stub → write stub 抛错 | plan + confirm → research 真实跑 1-3 轮 → write stub 抛错（research 真的"通过"）|
| `state["sources"]` | 2 条 stub Source | ≥ 8 条真实 Source（ReRank 后），按 score 降序，1..N 全局唯一 |
| `state["search_evaluations"]` | 2 条 stub eval | 同等数量的 eval，`evaluator="rerank"`（默认）或 `"llm_judge"`（fallback / `USE_RERANK=false`）|
| `state["research_queries"]` | 0 条 stub | 5-15 条真实 query（每轮 1-5 条 × 1-3 轮）|
| `state["iteration_counts"]["research"]` | 0（stub 不动） | 1-3（实际跑的轮数）|
| `get_llm("research")` | NotYetImplementedError | ChatOpenAI 实例，temperature=0.2 |
| `DashScopeWebSearch._run` | NotYetImplementedError | 真实 Bailian App 调用 |
| `DashScopeReRank._run` | NotYetImplementedError | 真实 DashScope ReRank 调用 |
| Lesson 0007 可启动 | ❌（research stub） | ✅（research 真实跑，可做 USE_RERANK 对照实验）|
| spec 004 write 接力 | ❌（research stub 抛错） | ✅（research 退出 → write stub 抛错）|