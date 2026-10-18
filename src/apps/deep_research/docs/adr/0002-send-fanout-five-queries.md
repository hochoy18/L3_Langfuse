# ADR-0002 · Send API 并发 5 个 websearch（research 子图 fan-out）

| 字段 | 值 |
|---|---|
| Status | Accepted |
| Date | 2026-10-18 |
| Scope | `nodes/research.py` |

## Context

research 子图的 `gen_query` 节点一次性产出 N 个 `ResearchQuery`，需要决定如何调用 N 次 `websearch` 工具。

约束：
- DashScope rate limit 15 RPS（超限静默 skip，无 error 抛出——footgun）
- 每个 websearch 调用 1-3 秒，单 ResearchIteration 串行 5 次 = 5-15 秒
- 未来 Langfuse 评估希望看到**每个 query 独立 span**（不是 5 个合并的 batch span）

## Decision

**采用 LangGraph `Send` API fan-out 5 个并发 worker，每个 worker 独立调一次 `websearch`，最后 fan-in 到 `eval_search`**。

```python
# nodes/research.py (示意)
def gen_query(state) -> list[Send]:
    queries = llm.invoke(...)  # 产出 5 个 query
    return [
        Send("websearch", {"query": q, "iteration": state["iteration_counts"]["research"]})
        for q in queries
    ]

# graph.py
research_builder.add_conditional_edges(
    "gen_query",
    gen_query,                    # 返回 list[Send]
    ["websearch"]                 # target node name
)
research_builder.add_edge("websearch", "eval_search")  # fan-in
```

并发数固定为 **5**（不是 3 也不是 10）。原因：
- 3 太少：单轮 sources 不足 8 阈值，需要更多轮
- 10 太多：单轮 token 成本高、rate limit 接近、DashScope batch 返回质量下降
- 5 是 DashScope agent_max 策略推荐 batch 大小

## Alternatives Considered

### A. 串行 websearch

```python
for q in queries:
    sources = websearch_tool(q)
```

**否定原因**：5 个 query × 2 秒 = 10 秒/research iteration；3 轮 = 30 秒仅 research 阶段。

### B. `asyncio.gather` 在 gen_query 节点内部并发

```python
async def gen_query(state):
    sources = await asyncio.gather(*[websearch_tool.ainvoke(q) for q in queries])
    return {"sources": sources}
```

**否定原因**：
- 5 个 websearch 全部塞进**一个 span**（Langfuse 看不到每个独立 query 的 latency / token）
- asyncio.gather 与 LangGraph Send 都能并发，但 Send 是 LangGraph 一等公民，与 checkpointer / interrupt 集成更好
- 加 `asyncio.Semaphore` 也无法精确控并发边界

### C. 动态 batch size

`fanout_size` 由 LLM 根据 query 复杂度决定（3-10）。

**否定原因**：增加 LLM 决策成本，且 5 是经验最优值；动态 batch 的 latency 节省不足以抵消复杂度。

## Consequences

**正向**：
- 5 个 websearch span 在 Langfuse UI 上是 sibling（不是父子），独立可打分
- DashScope rate limit 友好：5 并发 + 平均 2 秒/次 = 2.5 RPS，远低于 15 RPS 上限
- 子图节点边界清晰（`gen_query → websearch × 5 → eval_search`）
- 未来改成 `Send("websearch", {...})` 调 N 个不同 tool（不只 websearch）只需改 `gen_query` 内部

**负向**：
- 5 个 websearch 全部失败时（rate limit / network），`sources` 列表为空——需要 `eval_search` 检测并触发 fallback（已在 `ResearchTerminationPolicy` 中处理）
- Send API 是 LangGraph 较新特性（0.2+），升级 LangGraph 时需关注兼容性

**未来触发条件**：如果需要更细粒度的 batch 控制（如 per-Pod cap），改用 `Send` + semaphore 组合（未来 ADR）。