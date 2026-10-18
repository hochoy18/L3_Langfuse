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

---

## 🔗 关联资源

- 父项目：[`../../../README.md`](../../../README.md)（L3_Langfuse workspace）
- LangGraph 官方 deep research tutorial：https://langchain-ai.github.io/langgraph/tutorials/deep_research/
- DashScope Web Search 文档：https://docs.bailian.console.aliyun.com/zh/model-studio/web-search
- Langfuse 自部署：[`../../../langfuse-deploy/README.md`](../../../langfuse-deploy/README.md)（PostgresSaver 依赖）