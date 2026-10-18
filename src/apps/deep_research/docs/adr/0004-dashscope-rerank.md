# ADR-0004 · DashScope ReRank 二次精排（eval_search 评分策略）

| 字段 | 值 |
|---|---|
| Status | Accepted |
| Date | 2026-10-18 |
| Scope | `nodes/research.py::eval_search_node` + `tools.py` |

## Context

DashScope 的 `Generation.call(enable_search=True)` 返回 `search_results`，**每条结果的字段是 `{index, title, url, content?, score?, published_time?}`**——其中：

- `score` 字段**官方文档不保证存在**（仅在第三方文档提及，DashScope 实际不返回 numeric score）
- `index` 是 DashScope 自己的引用排序（隐式相关度，非显式分数）
- `content` 也不总是填充（snippet-only 模式 vs full-content 模式）

`eval_search` 节点需要给每个 Source 打一个 0-1 的 relevance score，用于：
1. 阈值过滤（<0.3 直接丢弃）
2. 给研究循环判断"信息够不够"
3. 未来 Langfuse score 评估 search 质量

## Decision

**采用 DashScope ReRank 二次精排**：调 `dashscope.TextReRank.call(model="gte-rerank-v2")` 对 raw `search_results` 重排，返回 guaranteed score。

```python
# tools.py (示意)
class DashScopeReRank(BaseTool):
    def _run(self, query: str, sources: list[dict]) -> list[dict]:
        resp = TextReRank.call(
            model="gte-rerank-v2",
            query=query,
            documents=[s.get("content", s.get("title", "")) for s in sources],
            top_n=len(sources),
            return_documents=True,
        )
        # 把 rerank score 写回 source.score 字段
        for r, s in zip(resp.output.results, sources):
            s["score"] = r["relevance_score"]
        return sources
```

`eval_search` 节点的工作流：
1. 拿 5 个 websearch 的所有 Source（合并去重）
2. 调 DashScopeReRank → 每个 Source 得到真 score
3. score < 0.3 的 Source 丢弃
4. 剩余 Source 的 LLM Judge（用 `judge_helpfulness` 复用 lesson 0005-02）补充 qualitative reason
5. 写 `sources` + `search_evaluations` 到 state

env var `USE_RERANK=false` 时跳过 ReRank，fallback 到 LLM Judge 评分（节省 ¥几分钱/次）。

## Alternatives Considered

### A. 信任 DashScope index 顺序 + LLM Judge 全评

**否定原因**：
- index 顺序是 DashScope 黑盒排序算法，与 UserQuery 相关度不直接挂钩
- LLM Judge 评 5 个 Source 平均多花 1500 tokens + 2 秒
- score 不可量化（只能定性 reason）

### B. 纯 LLM Judge（无 ReRank）

**否定原因**：token 成本高（每次 research 5 × 3 = 15 个 Source × 1000 tokens ≈ 15k tokens）。如果走实验（Lesson 0007 dataset run），成本爆炸。

### C. 不评估

直接拿 DashScope 前 N 条 Source，靠 research 循环的 `max_iter + min_sources` 兜底。

**否定原因**：
- 无法做阈值过滤（垃圾 Source 进入 sources 列表，污染 Draft）
- 未来 Langfuse 没有 `search-evaluation` score 可打

## Consequences

**正向**：
- 每个 Source 有 guaranteed 0-1 score，可阈值过滤、可定量比较
- `gte-rerank-v2` 单次成本 ¥几分，5 个 Source × N 轮仍可接受
- score 字段可入 FinalReport References 章节（"score: 0.92"），用户直观看到源质量

**负向**：
- 每次 research iteration 多 1 次 API 调用（latency +1-2 秒）
- 依赖 DashScope 第二个模型（ReRank）的稳定性——若服务挂掉 eval_search 节点 raise，由 RetryPolicy 兜底
- `USE_RERANK=false` 降级路径需测试覆盖

**未来触发条件**：如果 DashScope 把 score 字段升级为官方字段（有可能），可去掉 ReRank 调用直接读 score——但当前文档不保证，保留 ReRank。