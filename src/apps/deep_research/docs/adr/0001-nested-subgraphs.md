# ADR-0001 · 嵌套子图结构（OuterGraph 4 节点 + ResearchSubgraph + WriteSubgraph）

| 字段 | 值 |
|---|---|
| Status | Accepted |
| Date | 2026-10-18 |
| Scope | `src/apps/deep_research/` 模块结构 |

## Context

Deep research agent 至少包含 9 个工作步骤（plan / confirm_plan / gen_query / websearch / eval_search / outline / draft / critic_review / polish）。需要决定如何在 LangGraph 中组织这些节点：

1. **全部平铺为顶层节点**：图里 9 个节点直接相连，条件边控循环
2. **嵌套子图**：顶层只暴露 4 个语义节点（plan / confirm_plan / research / write），research 和 write 各封一个嵌套 StateGraph
4. **单一 ReAct 节点**：用 `create_react_agent` 一个节点包办，工具描述每个子步骤

## Decision

**采用方案 2**：外层图 4 个节点（`plan` → `confirm_plan` → `research` → `write`），其中 `research` 和 `write` 各是一个 `StateGraph` 嵌套子图。

文件落地：

```
nodes/
├── plan.py       # plan_node + confirm_plan_node
├── research.py   # build_research_subgraph()
└── write.py      # build_write_subgraph()
```

每个子图通过 `graph.add_node("research", build_research_subgraph().compile())` 嵌入外层。

## Alternatives Considered

### A. 平铺 9 个顶层节点

```
plan → confirm_plan → gen_query ⇄ eval_search → outline → draft ⇄ critic_review → END
                          │                                       │
                          └── (websearch 嵌入 gen_query 内)         └── (polish 嵌入 critic_review 内)
```

**否定原因**：条件边长链难维护；research 子图和 write 子图各自的循环控制混在一个图里，读图成本高。

### C. 单一 ReAct 节点

```python
agent = create_react_agent(
    llm,
    tools=[
        generate_plan_tool,
        websearch_tool,
        generate_outline_tool,
        ...
    ]
)
```

**否定原因**：每个工具的输入输出协议不一致（websearch 返回 list，generate_plan 返回 Plan 结构体）；LLM 调度复杂步骤时容易丢步骤；未来 Langfuse 只能看到一个 LLM 决策 span，看不到子步骤。

## Consequences

**正向**：
- 未来 Langfuse UI 上看到清晰的层级树：`deep-research → research → websearch` / `write → critic_review`
- 每个子步骤（gen_query / websearch / eval_search / outline / draft / critic_review / polish）都是独立 span，可单独打 score
- 子图内部状态独立于外层，便于单元测试
- `graph.py` 只关心 4 个节点的连线，子图复杂度被隔离

**负向**：
- 多一层文件（`nodes/research.py` / `nodes/write.py`），新人需要理解子图边界
- 子图与外层 state 字段共享时容易混淆 reducer（用 `Annotated[..., operator.add]` 强制显式声明累积字段）

**未来触发条件**：如果外层再加节点（如 `summarize` / `translate`），考虑用 `graph.add_node` 平铺；不需要的话保持嵌套结构。