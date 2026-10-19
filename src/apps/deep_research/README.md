# Deep Research Agent

> 基于 LangGraph 1.2+ 的 deep research agent，支持 plan 审阅、研究循环、写作循环，带 Langfuse 可观测性预留。
> Langfuse hook 已设计预留，本阶段不接入。

---

## 📚 必读文档

按顺序读：

1. **[CONTEXT.md](./CONTEXT.md)** — 领域词汇表（9 类术语：Actors / Entities / Sub-graphs / Operations / External Concepts / Policies / 易混术语 / 范围外）
2. **[docs/adr/0001-nested-subgraphs.md](./docs/adr/0001-nested-subgraphs.md)** — 嵌套子图结构
3. **[docs/adr/0002-send-fanout-five-queries.md](./docs/adr/0002-send-fanout-five-queries.md)** — Send API 并发 5 个 websearch
4. **[docs/adr/0003-interrupt-for-plan-confirm.md](./docs/adr/0003-interrupt-for-plan-confirm.md)** — interrupt() 实现 plan 审阅
5. **[docs/adr/0004-dashscope-rerank.md](./docs/adr/0004-dashscope-rerank.md)** — DashScope ReRank 评分策略
6. **[docs/adr/0005-postgres-checkpointer.md](./docs/adr/0005-postgres-checkpointer.md)** — PostgresSaver checkpointer
7. **[docs/adr/0006-streamlit-ui-entry.md](./docs/adr/0006-streamlit-ui-entry.md)** — Streamlit 入口

---

## 🏗️ 模块结构

```
src/apps/deep_research/
├── README.md               ← 你在这里
├── CONTEXT.md              ← 领域词汇表
├── docs/adr/               ← 架构决策记录（6 篇）
├── state.py                ← DeepResearchState TypedDict
├── schemas.py              ← Pydantic models (PlanStep / Source / Critique / SearchEval)
├── graph.py                ← OuterGraph + PostgresSaver + run_research()
├── prompts.py              ← Langfuse prompt factory + LLM factory
├── tools.py                ← DashScopeWebSearch + DashScopeReRank BaseTool
├── nodes/
│   ├── __init__.py         ← re-export
│   ├── plan.py             ← plan_node + confirm_plan_node
│   ├── research.py         ← build_research_subgraph (gen_query / websearch / eval_search)
│   └── write.py            ← build_write_subgraph (outline / draft / critic_review / polish)
├── app.py                  ← Streamlit UI 入口
├── cli.py                  ← CLI 烟雾测试
└── __init__.py
```

---

## 🔁 流程图

```
                ┌──────────────────────────────────────┐
                │       OuterGraph（4 节点）            │
                └──────────────────────────────────────┘
                              ↓
                          plan_node
                              ↓
                       confirm_plan_node
                       (interrupt() 暂停)
                              ↓
              ┌────────── ResearchSubgraph ──────────┐
              │  gen_query → Send[websearch × 5]      │
              │            → eval_search → cond edge │
              └────────────────────────────────────────┘
                              ↓
              ┌────────── WriteSubgraph ─────────────┐
              │  outline → draft → critic_review     │
              │      ↑─────────────polish ←──────┘   │
              └───────────────────────────────────────┘
                              ↓
                          final_report → END
```

---

## ✅ 设计要点速览

| 维度 | 决策 | 见 |
|---|---|---|
| 图结构 | 外层 4 节点 + research/write 子图 | ADR-0001 |
| 研究并发 | Send API fan-out 5 个 websearch | ADR-0002 |
| Plan 审阅 | `interrupt()` + `Command(resume=...)` | ADR-0003 |
| Source 评分 | DashScope ReRank 二次精排 | ADR-0004 |
| 状态持久化 | PostgresSaver（复用 langfuse-deploy） | ADR-0005 |
| 运行入口 | Streamlit 单页 UI | ADR-0006 |

---

## 🚧 当前状态（implementation status）

本 README 写于**设计阶段完成、代码尚未实现**的节点。

实现完成后，会在每个文件头加 ✅ 标记，并在 README 末尾加"实现进度表"。

### spec 002 增量（Plan + Confirm 阶段）

✅ **`src/apps/deep_research/prompts.py`** — `get_prompt` / `get_llm` 真实接入 Langfuse + DashScope
✅ **`src/apps/deep_research/nodes/plan.py`** — `plan_node` 真接 LLM；`confirm_plan_node` 加 edit 分支
✅ **`src/apps/deep_research/graph.py`** — `_acquire_thread_lock` + `thread_id_busy` 错误返回

**前置 1 步（手动）**：在 Langfuse UI 创建 `deep-research-plan` prompt（label=production）。

| 字段 | 值 |
|---|---|
| Name | `deep-research-plan` |
| Type | `text` |
| Labels | `production` |
| Prompt | 见 [`specs/002-deep-research-plan-confirm/contracts/README.md §C6`](../../specs/002-deep-research-plan-confirm/contracts/README.md) |

**必须含的 Mustache 占位符**（chevron 语法，**不要**用 `{{#if}}`，**用** `{{#user_feedback}}`）：

| 占位符 | 必填 | 用途 |
|---|---|---|
| `{{user_query}}` | ✅ | 用户研究问题 |
| `{{max_steps}}` | ✅ | 计划步数上限（hardcoded 7） |
| `{{#user_feedback}}...{{/user_feedback}}` | ❌ | edit 模式下的用户反馈（空值时不渲染该段） |

> 为什么不直接用 `prompt.compile()`：Langfuse 4.x `text` 类型的 `compile()` 只做 `{{var}}` 替换，不解析 `{{#if}}` 段。spec 002 改用 chevron 在客户端渲染完整 Mustache 模板。

详细 spec 见 [`specs/002-deep-research-plan-confirm/spec.md`](../../specs/002-deep-research-plan-confirm/spec.md)。

---

### spec 003 增量（Research 阶段：真实 web search + Send 并发 + ReRank + LLM Judge 降级）

✅ **`src/apps/deep_research/prompts.py`** — `get_llm("research")` 实装（`temperature=0.2`，vs `plan` 的 0.4；与 spec 002 plan LLM 共用 DashScope 凭证）
✅ **`src/apps/deep_research/tools.py`** — `DashScopeWebSearch._run` 真实接 Bailian（百炼）web search App（`dashscope.Application.call(app_id=APP_ID, prompt=query, api_key=APP_TOKEN)`，2 层 JSON 解析 `resp.output['text']` → `result.content[0]['text']` → `pages[]`，每条 `Source(index=0, title, url, content=snippet, query_index=...)`）+ HTTP 429/5xx 指数退避 + 0 results / 凭证缺失显式抛错；`DashScopeReRank._run` 真实接 `dashscope.TextReRank.call(model="gte-rerank-v2")` + 用 `r.index` 字段（input-doc 位置）做 safe back-mapping（SDK 不保证 score-desc 返回顺序，positional 会写错 source）
✅ **`src/apps/deep_research/nodes/research.py`** — 移除 stub；新增 `_gen_query_node` / `_gen_query_routes` / `_websearch_node` / `_eval_search_node` / `_llm_judge_sources` / `_should_continue_research` + 重写 `build_research_subgraph()` 接入 Send API 1-5 路 fan-out + 条件边循环 + 终止循环（`len(sources) >= 8 OR iters >= 3 → END`）+ ReRank 失败 per-group fallback 到 LLM Judge
✅ **`src/apps/deep_research/state.py`** — 新增 12 字段：增加 `raw_sources: Annotated[list[Source], _merge_sources_by_identity]`（Send fan-in reducer 目标）+ `sources: list[Source]` 改为 plain overwrite（`_eval_search_node` 写 renumbered 最终输出；spec 004 write_subgraph 消费）。**为什么需要这个 split**：Send fan-in 必须有 reducer 处理 1-5 worker 并发写（plain list 会抛 `InvalidUpdateError`），但 `eval_search` 重编号 0→1..N 触发 reducer 的 first/right-wins 都不能既保留 US2 的 20 sources 又清除 9 个 filtered sources（详见 `state.py` 模块 docstring 段 "Why raw_sources exists"）。Split 让 `raw_sources` 走 reducer 处理 fan-in，`sources` 走 plain overwrite 处理 renumbering —— 两边都符合 spec 001 Q2 意图

**前置 2 步（手动 + .env）**：

1. **在 Langfuse UI 创建 2 个 chat prompt（label=production）**：

| Name | Type | 必填占位符 | 见 |
|---|---|---|---|
| `deep-research-gen-query-v2` ⚠️ | `chat` | `{{user_query}}` / `{{plan_section_title}}` / `{{plan_section_description}}` / `{{iteration}}` / `{{target_step}}` | [contracts §C8](../../specs/003-deep-research-research-loop/contracts/README.md) |
| `deep-research-judge` | `chat` | `{{user_query}}` / `{{sources_json}}` | [contracts §C9](../../specs/003-deep-research-research-loop/contracts/README.md) |

> ⚠️ `deep-research-gen-query` 旧版（text v3，spec 002 时代遗留）已 REST API 软删除，但 Langfuse 服务端保留 `latest` label 的 ghost version 导致同名无法换 chat type；spec 003 实装用 `-v2` 后缀新名。

2. **`.env` 新增 / 确认 4 个 env var**：

| env var | 必填 | 用途 | spec 003 行为 |
|---|---|---|---|
| `DASHSCOPE_WEB_SEARCH_APP_ID` | ✅ | Bailian App ID（web search） | 缺失时 `_run` 抛 `RuntimeError("DASHSCOPE_WEB_SEARCH_APP_ID not set; ... Do NOT fall back to Generation.call(enable_search=True) ...")`（per Q1 footgun 防御） |
| `DASHSCOPE_WEB_SEARCH_APP_TOKEN` | ✅ | Bailian App Token | 缺失时同样抛 `RuntimeError("DASHSCOPE_WEB_SEARCH_APP_TOKEN not set; ...")` |
| `USE_RERANK` | ❌ | `false` 时 `_eval_search_node` 跳过 ReRank 走 LLM Judge（per ADR-0004 + US5 T022） | 默认 `true`；设 `false` 后所有 group 走 LLM Judge，`Source.score` 保持 `None`，跳过 `< 0.3` 过滤 |
| `USE_WEB_SEARCH_APP` | ❌ | dev/CI 旁路预留（spec 003 仅留名不实装 bypass per FR-004 + C2） | 当前实装是 read-and-warn（不抛错，但提示；不绕过 Bailian App 调用） |

**`USE_RERANK` 降级语义（两条独立路径）**：

- **显式旁路**：`USE_RERANK=false` → 所有 group 走 LLM Judge（per FR-017）
- **隐式 silent degrade**：ReRank 抛 `NotYetImplementedError`（如 401 InvalidApiKey） → 仅失败的 group fallback 到 LLM Judge，成功的 group 保留 ReRank 路径 + `evaluator` 字段逐组决定（per FR-016 + R5 + ADR-0004）

**为什么 web search 走 Bailian App 而不是 `Generation.call(enable_search=True)`**：

- L3_Langfuse 部署的 Aliyun MaaS gateway（`token-plan.cn-beijing.maas.aliyuncs.com`）**只代理** `/v1/chat/completions` + `/v1/models`，对 `enable_search=True` 静默返回 `InvalidApiKey`（per [[dashscope-mmaas-enable-search-needs-bailian-app]] 项目记忆）
- 唯一稳定路径：`dashscope.Application.call(app_id=Bailian_App_ID, prompt=query, api_key=Bailian_App_TOKEN)`
- ⚠️ **不要**在任何 production 路径上写 `Generation.call(enable_search=True)` 兜底——会让 Lesson 0007 实验看到不一致的成功/失败比

**ReRank 部署级限制（已知）**：

- L3_Langfuse 部署的 MaaS gateway（`token-plan.cn-beijing.maas.aliyuncs.com`）**不代理** ReRank endpoint（`gte-rerank-v2` 不在 `/compatible-mode/v1/models` 19 个模型里），`dashscope.TextReRank.call()` 默认走主站 `dashscope.aliyuncs.com` 需 non-STS DashScope API key
- 当前 `DASHSCOPE_API_KEY` 是 STS token（`sk-sp-` 前缀），主站会 401
- **真 ReRank SC-003 验证需要用 non-STS DashScope API key**；本机 spec 003 实装 SC-003 用 mock-based 验证（T018），SC-010 真端到端已 PASS 4 group 全部 ReRank 401 → LLM Judge fallback 156s 完成

详细 spec 见 [`specs/003-deep-research-research-loop/spec.md`](../../specs/003-deep-research-research-loop/spec.md)。

---

## 🔗 关联资源

- 父项目：[`../../../README.md`](../../../README.md)（L3_Langfuse workspace）
- LangGraph 官方 deep research tutorial：https://langchain-ai.github.io/langgraph/tutorials/deep_research/
- DashScope Web Search 文档：https://docs.bailian.console.aliyun.com/zh/model-studio/web-search
- Langfuse 自部署：[`../../../langfuse-deploy/README.md`](../../../langfuse-deploy/README.md)（PostgresSaver 依赖）