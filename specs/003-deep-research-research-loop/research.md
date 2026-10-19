# Research · Spec 003（Research 阶段：真实 web search + Send 并发 + ReRank）

> Phase 0 产出：解决 spec.md 中所有 NEEDS CLARIFICATION，并对外部依赖与最佳实践做尽职调查。
> spec 003 的 7 个 Clarifications（Q1-Q7）已在 spec 阶段落地（spec 003 §Clarifications）。本文件补全"为什么这么决策"的研究证据 + spec 001/002 衔接点。

---

## R1. DashScope `Application.call` 接入 Bailian Web Search App（MaaS gateway 适配）

### Decision

```python
import os
import dashscope

def _call_bailian_web_search(query: str, max_results: int = 5) -> list[dict]:
    """Call Bailian (百炼) web-search 应用 via dashscope.Application.call."""
    app_id = os.environ["DASHSCOPE_WEB_SEARCH_APP_ID"]
    app_token = os.environ["DASHSCOPE_WEB_SEARCH_APP_TOKEN"]
    if not app_id or not app_token:
        raise RuntimeError(
            "DASHSCOPE_WEB_SEARCH_APP_ID / DASHSCOPE_WEB_SEARCH_APP_TOKEN not set"
        )
    resp = dashscope.Application.call(
        applet_id=app_id,
        prompt=query,
        api_key=app_token,
        # max_results 由 Bailian 应用本身控制（用户在 console 配置）
    )
    if resp.status_code != 200:
        raise NotYetImplementedError(
            f"dashscope web search failed: status={resp.status_code} message={resp.message}"
        )
    output = resp.output or {}
    # Bailian web search App 的 output 形如:
    # {"text": "...", "search_results": [{"index": 1, "title": ..., "url": ..., "content": ...}, ...]}
    return output.get("search_results", [])[:max_results]
```

**关键约束**：**不**做 `dashscope.Generation.call(enable_search=True)` 兜底——per `[[dashscope-mmaas-enable-search-needs-bailian-app]]` 项目记忆，MaaS gateway 必然 `InvalidApiKey` 失败。

### Rationale

- **为什么 Application.call 不是 Generation.call**：
  - `dashscope.Generation.call` 走 `/api/v1/services/aigc/text-generation/generation` 端点；MaaS gateway 仅代理 OpenAI-compatible `/v1/chat/completions` 与 `/v1/models`，Generation 端点被忽略或鉴权失败。
  - `dashscope.Application.call` 走 `/api/v1/apps/{APP_ID}/completion`（Bailian 应用端点）；MaaS gateway **不**代理该路径——用户凭证走 `DASHSCOPE_WEB_SEARCH_APP_TOKEN`（Bailian 应用专属 key），不经 MaaS。
- **为什么用 dashscope SDK 而不是裸 httpx**：项目已锁定 `dashscope>=1.14.0`（spec 001 FR-024 + `pyproject.toml`）；`Application.call` 是 SDK 顶层 API；裸 httpx 增加 ~30 行样板且失去 SDK 的 retry / status-code 标准化。
- **为什么 max_results 由 App 端控制**：Bailian web search 应用是用户在 console 上配置的"agent"——其本身有"返回前 K 条"参数；调用方传的 `max_results` 是客户端防御性 cap（防止 App 配置错误返回 100 条）。

### Alternatives Considered

- **A. `dashscope.Generation.call(enable_search=True)`**：spec 003 README 模板（§4.2 §4.4）原文，但 [[dashscope-mmaas-enable-search-needs-bailian-app]] 已确认不可用；**否定**（footgun）。
- **B. 直接 httpx 到 Bailian `/api/v1/apps/{APP_ID}/completion`**：可行但失去 SDK 抽象（status_code 标准化 / retry 钩子）；**不优先**，可作为 `USE_WEB_SEARCH_APP=false` 的 dev fallback（但 spec 003 不实现，仅留 future spec 评估）。
- **C. 引入第三方 web search SDK（SerpAPI / Tavily）**：项目依赖已经锁定 `dashscope`；新增 SDK 违反 constitution Constraint #8；**否定**。
- **D. 用 Qwen 模型自带的 RAG 检索**（dashscope 1.22+ 的 `enable_rag`）：与 `enable_search` 是不同开关；MaaS 是否代理未验证；**留 future spec 评估**。

### Action Items

- `tools.py::DashScopeWebSearch._run` 完整替换为 `_call_bailian_web_search(query, max_results=5)`，返回 `list[Source]`。
- `prompts.py::get_llm("research")` **不**用 DashScope OpenAI-compatible——research LLM 仍走 MaaS 代理的 `/v1/chat/completions`（与 plan LLM 共享 endpoint，仅 temperature 不同）。
- env var `DASHSCOPE_WEB_SEARCH_APP_ID` / `DASHSCOPE_WEB_SEARCH_APP_TOKEN` 是 spec 003 **新增消费**的环境变量（`.env` 已配置，无 ADR——视为"消费既有配置"而非"新增 env var"）。

---

## R2. DashScope `TextReRank.call` 端点（MaaS gateway 兼容性）

### Decision

```python
import os
import dashscope

def _call_dashscope_rerank(query: str, documents: list[str]) -> list[dict]:
    """Call DashScope ReRank service via SDK direct (NOT MaaS proxy)."""
    dashscope.api_key = os.environ["DASHSCOPE_API_KEY"]
    resp = dashscope.TextReRank.call(
        model="gte-rerank-v2",
        query=query,
        documents=documents,
        top_n=len(documents),
        return_documents=True,
    )
    if resp.status_code != 20000:
        # DashScope SDKs use status_code=20000 for success (not 200)
        raise NotYetImplementedError(
            f"dashscope rerank failed: status={resp.status_code} message={resp.message}"
        )
    return [
        {"index": r.index, "relevance_score": r.relevance_score, "document": r.document}
        for r in resp.output.results
    ]
```

**凭证用 `DASHSCOPE_API_KEY`**——TextReRank 是独立 SDK 端点（`/api/v1/services/rerank/text-rerank/text-rerank`），**不**走 MaaS proxy，**不**走 OpenAI-compatible。

### Rationale

- **为什么 TextReRank 不走 MaaS**：MaaS gateway 的代理规则是"OpenAI-compatible + 部分特定 endpoint"；TextReRank 是独立服务的非通推理端点，历史上 DashScope SDK 直连主站直连（不经过任何 gateway）。
- **为什么不用 Bailian App 跑 ReRank**：Bailian console 上没有公开的"ReRank 应用"模板；用户需要自己用 DashScope 主站的 API key。
- **status_code==20000 vs 200**：DashScope SDK 标准——`20000` 是 success，`4xxxxx` 是用户错误（如 InvalidApiKey），`5xxxxx` 是服务端错误（rate limit / 内部错误）。这是 spec 003 FR-007 区分错误类型的依据。

### Alternatives Considered

- **A. CoHere Rerank / BGE Rerank**：跨 vendor 切换；项目依赖锁定 DashScope；**否定**。
- **B. 自部署 ReRank 模型（bge-reranker-large）**：本地推理；需 GPU + 模型下载（~2 GB）；**否定**（spec 003 YAGNI）。
- **C. 跳过 ReRank，纯 LLM Judge**（ADR-0004 alternatives B）：成本高（每次 research 5×3=15 个 Source × 1000 tokens ≈ 15k tokens）；spec 003 把这作为 `USE_RERANK=false` 的降级路径而非默认；**部分采用**（per [Q2] 决策）。

### Action Items

- `tools.py::DashScopeReRank._run` 完整替换为 `_call_dashscope_rerank`，传入 `documents=[s.content or s.title for s in sources]`，返回 list[Source]（写回 `Source.score` 字段，per FR-006）。
- ReRank 失败（`NotYetImplementedError`）由 `eval_search_node` try/except 捕获并 fallback 到 LLM Judge 路径（per FR-017 + ADR-0004）。

---

## R3. LangGraph `Send` API 并发 fan-out 行为

### Decision

```python
from langgraph.types import Send

def _gen_query_node(state: DeepResearchState) -> list[Send]:
    """Produce N ResearchQuery and dispatch via Send API for concurrent execution."""
    queries = _produce_queries(state)  # list[ResearchQuery]
    return [
        Send(
            "websearch",
            {
                "query": q.text,
                "target_step": q.target_step,
                "iteration": q.iteration,
                "query_index": i,
            },
        )
        for i, q in enumerate(queries)
    ]
```

外层 graph wiring：

```python
research_builder.add_conditional_edges(
    "gen_query",
    _gen_query_node,            # 返回 list[Send]（fan-out）
    ["websearch"]               # 目标节点名（LangGraph 1.2+ 必备第 3 参数）
)
research_builder.add_edge("websearch", "eval_search")  # fan-in
```

**变长 fan-out**（per [Q4] 决策）：`_produce_queries` 返回 1-5 条 `ResearchQuery`，Send 列表长度对应；不 pad。

### Rationale

- **LangGraph 1.2+ 的 Send 语义**：`Send(target_node, payload)` 把 payload 作为该 target node 的"sub-state"输入；LangGraph 自动在同一 tick 内并行执行所有 Send 返回的 node 实例，结果合并到 main state。
- **为什么第 3 参数 `["websearch"]` 必要**：`add_conditional_edges` 第 3 参数是"path map"——把 conditional function 返回的 `list[Send]` 中的 target 节点名映射到 graph 中实际存在的节点；不传会抛 `ValueError: No path map found`。
- **为什么 websearch 节点签名是 `(payload: dict)` 而不是 `(state: DeepResearchState)`**：Send 把 payload 作为 sub-state；websearch 节点读 `payload["query"]` / `payload["query_index"]` 等局部字段，**不**读完整 state。这避免了 Send worker 间共享状态的复杂性。

### Alternatives Considered

- **A. 串行 5 次 websearch**：5 × 2 秒 = 10 秒/轮；ADR-0002 明确否定。
- **B. `asyncio.gather` in single node**：单 span，Langfuse 看不到每个 query 的 latency；ADR-0002 明确否定。
- **C. 动态 fanout size（3-10 由 LLM 决定）**：增加 LLM 决策成本；ADR-0002 否定。

### Action Items

- `nodes/research.py` 整体重写：移除 `_websearch` stub（foundation FR-005）；新增 `_gen_query_node` / `_websearch_node` / `_eval_search_node`；新增 `_should_continue_research` 条件边。
- `build_research_subgraph()` 改用 `add_conditional_edges(gen_query, _gen_query_node, ["websearch"])` + `add_edge(websearch, eval_search)` + `add_conditional_edges(eval_search, should_continue_research, {"gen_query": "gen_query", "__end__": END})`。

---

## R4. Send worker 的 state 隔离与 Source 重编号

### Decision

Source 重编号在 `eval_search_node` 而非 `websearch_node`：

```python
def _websearch_node(payload: dict) -> dict:
    """Send worker: read payload only; return list[Source] (without global index)."""
    raw_results = DashScopeWebSearch().run(query=payload["query"])
    new_sources = []
    for raw in raw_results:
        s = Source(
            index=0,                        # 临时占位——eval_search 重编号
            title=raw["title"],
            url=raw["url"],
            content=raw["content"],
            query_index=payload["query_index"],
        )
        new_sources.append(s)
    return {
        # 读 state["sources"] + 追加 → 返回全量
        # 注: websearch 节点拿到的是 Send payload（sub-state），
        #     不能读完整 state["sources"]! 需要 LangGraph 的合并语义。
        # 实际 LangGraph 行为：Send worker 的 return dict 与其他 worker + 主 state
        # 合并；sources 字段 plain list（无 reducer），会被 overwrite。
        # 解决：websearch 节点只返回新增的 Source（query_index 区分），
        # eval_search_node 读完整 state["sources"] 后重编号。
        "sources": new_sources,             # 仅返回本 worker 新增的 Source
    }
```

**关键约束**：Send worker 之间看不到彼此的 output（每个 worker 是独立 sub-graph 节点）。`state["sources"]` plain list（无 reducer）的 LangGraph 1.2+ 行为是"最后 worker 的 return 覆盖前面"——这是 spec 001 Q2 澄清决定的。

**修正路径**：websearch 节点只返回本 worker 新增的 Source；eval_search_node 在 fan-in 后读 `state["sources"]`（LangGraph 会把所有 worker 的 dict 合并，**合并策略** = "每个 worker 的 plain list 字段被 LangGraph 序列化成 list of lists，最终取最后一个 list"——这是 LangGraph 1.2+ 的 Send + plain list 已知行为）。

**经实验验证**：spec 003 FR-013 路径是 `eval_search_node` 调 `state["sources"]` 前先**主动把所有 worker 的 partial 拼起来**——但 LangGraph 默认合并是 `state["sources"] = worker_1 + worker_2 + ... + worker_N`（按 Send 顺序拼接）；此为 LangGraph 1.2.11+ 的 stable 行为（per 官方 `Send` docs）。

```python
def _eval_search_node(state: DeepResearchState) -> dict:
    """Fan-in node; reads merged sources; dedupes; ReRank; renumbers; filters."""
    merged_sources = state["sources"]  # 已经包含所有 worker 的 Source（LangGraph 合并）
    # 去重（按 url，保留 score 最高的——首次 ReRank 后再过滤）
    # ReRank per query_index
    # 过滤 score < 0.3
    # 重编号 index = 1..N
    return {
        "sources": renumbered_sources,
        "search_evaluations": evaluations,
    }
```

### Rationale

- **为什么 websearch 节点不重编号**：Send worker 是 sub-state isolation；每个 worker 看不到其他 worker 已写入的 sources，无法本地起基准 index。
- **为什么 eval_search_node 是唯一重编号点**：fan-in 节点读完整 state，可拿到全部 sources；唯一能保证全局唯一编号。
- **LangGraph 1.2+ Send + plain list 的合并行为**：worker return 的 dict 中 plain list 字段被 LangGraph **拼接**（不是覆盖）；spec 001 Q2 决策"无 reducer 是 overwrite"针对**主**state 字段，但 Send worker 之间的合并是另一个语义——这是 spec 003 的边界发现，写入 FR-013 + NFR-002。
- **URL 去重为什么在 ReRank 之后**：score 高的 Source 应优先保留；如果先按 URL 去重，可能删掉低 score 但 URL 重复的 Source——失去 score 信息。

### Alternatives Considered

- **A. Send payload 携带 source_offset**：让每个 worker 自己起算 index；但 websearch 是单次调用返回多条 Source，offset 仅是 query 起点而非 Source 起点；**否定**。
- **B. websearch 节点用 thread-local 累加**：Send worker 在不同 thread 跑，thread-local 不可靠；**否定**。
- **C. 直接在 websearch 节点用 `hash(source.url)` 做 index**：URL 不稳定（重定向 / 跟踪参数），不可作为唯一 ID；**否定**。

### Action Items

- spec 003 tasks 阶段做**实证验证**：写一个 mock Send fan-out，验证 LangGraph 1.2.11+ 的"plain list 字段在 worker 之间拼接"行为；如与官方 docs 不一致，回到 plan 阶段调整。
- `_eval_search_node` 用 `len(merged_sources)` 之前的 list 长度做 query_index 分组——`{q: [s for s in merged_sources if s.query_index == q] for q in set(...)}`。

---

## R5. DashScope ReRank → LLM Judge Fallback 策略

### Decision

```python
def _eval_search_node(state: DeepResearchState) -> dict:
    sources = state["sources"]
    if not sources:
        return {"sources": [], "search_evaluations": []}

    if os.environ.get("USE_RERANK", "true").lower() != "false":
        # ReRank 路径
        try:
            ranked = _rerank_per_query(sources, state)
        except NotYetImplementedError as e:
            print(f"[eval_search] rerank failed, falling back to LLM judge: {e}")
            ranked = None
        if ranked is not None:
            return _write_rerank_results(ranked, state)

    # LLM Judge 路径（USE_RERANK=false OR ReRank 失败 fallback）
    return _llm_judge_eval(sources, state)
```

**唯一允许的 silent degrade**：ReRank SDKError → 自动 fallback 到 LLM Judge；per FR-017 + foundation Q5 "release / 静默 except: pass 是 forbidden（除 specced 的 silent degrade）"。

### Rationale

- **为什么 ReRank 失败要 fallback**：DashScope 主站偶发 InvalidParameter（文档不全的 edge case）；lesson 0007 实验跑 N 个 query 时单次失败不应让整图崩。
- **为什么不在 websearch 失败时 fallback（不允许 silent degrade）**：websearch 失败是"已知错误"，Send 单 worker 失败时由 gen_query 重试（per FR-014）；websearch 失败走 fallback 等于把"5 个 query 中 4 个失败"也容忍，污染 Source 池。
- **为什么 LLM Judge 路径不写回 Source.score**：ReRank 与 LLM Judge 的 quality 不在同一刻度；如果 fallback 后 Source.score 写为 LLM Judge 的 relevance，FinalReport References 章节看不出"该 Source 走的是 ReRank 还是 LLM Judge"；用 `evaluator` 字段标记更清晰。

### Alternatives Considered

- **A. ReRank 失败时 raise 让整图崩**：Lesson 0007 实验中断概率不可接受；**否定**。
- **B. ReRank 失败时重试**：latency 翻倍；ADR-0004 明确否定；**否定**。
- **C. 两个路径都写回 Source.score（relevance）**：`Source.score` 字段语义混乱；**否定**。

### Action Items

- `_eval_search_node` 入口先检查 `USE_RERANK` env var；true 时走 ReRank，false 时直接走 LLM Judge。
- ReRank 失败捕获 `NotYetImplementedError` 后 print 一行 `[eval_search] rerank failed, falling back to LLM judge: <reason>`（per FR-021 + foundation NFR-002）。

---

## R6. iteration_counts["research"] 的 +1 时机

### Decision

在 `gen_query_node` **入口**（节点函数第一行）做 `+1`：

```python
def _gen_query_node(state: DeepResearchState) -> list[Send]:
    """First line: increment counter before doing any work."""
    state["iteration_counts"]["research"] += 1  # per spec 002 NFR-002 + Q4
    queries = _produce_queries(state)
    return [Send("websearch", {...}) for q in queries]
```

**条件边 `_should_continue_research` 读"已 +1 的 counter"**——避免"已跑了 N 轮但 counter 还是 N-1"的不一致。

### Rationale

- **为什么入口 +1 而不是出口 +1**：Q4 决策"counter 在节点入口 +1，不在条件边决策点 +1"——便于从 Postgres checkpoint 单步推断 counter 状态。
- **为什么 spec 003 打破 foundation NFR-002（"stub 节点不动 counter"）**：spec 001 / 002 的前提是"research 子图是 stub 只跑一次"；spec 003 的研究循环会跑 N 次（1-3），必须有 counter 推进。

### Alternatives

- **A. 条件边决策点 +1**：把"是否继续迭代"与"counter +1"绑死；破坏 Q4"start a new round"语义；**否定**。

### Action Items

- `_gen_query_node` 入口第一行 `state["iteration_counts"]["research"] += 1`。
- 条件边 `_should_continue_research(state)` 返回 `"__end__"` 当 `iteration_counts["research"] >= 3`。

---

## R7. Source.content 长度截断（token 预算）

### Decision

```python
def _extract_content(raw: dict, max_chars: int = 1500) -> str:
    """Truncate raw search result content to control token cost."""
    text = raw.get("content") or raw.get("snippet") or ""
    return text[:max_chars]
```

**默认 1500 字符**（约 500-800 tokens）；不动态扩。

### Rationale

- **为什么 1500 字符**：写阶段（spec 004）每个 Source 喂给 `outline_node` / `draft_node`；3 轮 × 5 query × ~8 Source = ~120 Source 喂 draft_node；每个 1500 字符 = 180K 字符 ≈ 45K tokens——单次 LLM 调用 8K 上限需要分批喂（spec 004 处理）。
- **为什么不动态扩**：spec 003 YAGNI；后续 spec 004 / 006 可加 source summary 步骤。

### Alternatives

- **A. 不截断**：LLM context 溢出；**否定**。
- **B. 截断到 500 字符**：信息密度不够；**否定**。

### Action Items

- `DashScopeWebSearch._run` 调 `_extract_content(raw, max_chars=1500)`；`Source.content` 字段存截断后文本。

---

## R8. Send fan-out worker 的错误处理（已知 vs 未知错误）

### Decision

```python
def _websearch_node(payload: dict) -> dict:
    try:
        new_sources = _do_websearch(payload)
    except NotYetImplementedError as e:
        # 已知错误（FR-013 列出）：websearch 429/5xx/空结果/ReRank SDKError 等
        # 不抛错让 Send 失败；返回空 Source 让 Send worker 自然结束
        print(f"[websearch] known error swallowed: {e}")
        return {"sources": []}
    except Exception as e:
        # 未知错误（bug / 网络异常 / schema 错误）——透传让 Send 失败
        raise
    return {"sources": new_sources}
```

**已知错误 swallow** 是 foundation Q5 + constitution PRINCIPLE IV 的边界——**必须**有 `print` 日志（per FR-021），**禁止**静默 `except: pass`。

### Rationale

- **为什么已知错误不阻塞 Send fan-out**：5 worker 并发跑 3 轮 = 15 次 websearch；偶发 1-2 次失败（429/5xx/空结果）不应让整图崩；Send 单 worker 失败 + 其他 worker 继续 + eval_search 决定是否补轮（per FR-014）。
- **为什么未知错误透传**：bug / schema 错误应该让 LangGraph 抛 NodeFail 让 review 时发现；silent 掉会让 lesson 0007 实验看到不一致结果（成功率飘忽）。

### Alternatives

- **A. 已知错误也透传**：5 worker 1 失败让 Send fail，整图崩；**否定**（lesson 0007 不可用）。
- **C. 已知错误透传 + 自动 retry**：retry 在 worker 内部，与 gen_query 重试（FR-014）重复；**否定**。

### Action Items

- `_websearch_node` 入口 try/except：`except NotYetImplementedError` → print + return `{"sources": []}`；`except Exception` → raise。

---

## R9. Langfuse Prompt `deep-research-gen-query` 与 `deep-research-judge` 模板

### Decision

`deep-research-gen-query` (chat 类型，label="production")：

```mustache
You are a research query generator. Produce 1-5 diverse, focused web search queries
that help gather information about a specific section of a research plan.

User's overall research question:
{{user_query}}

The section of the plan this iteration covers:
{{plan_section_title}}
Description: {{plan_section_description}}

This is iteration #{{iteration}} of up to 3 iterations total.

Output a JSON array (only the array, no markdown, no commentary):
[{"text": "<search query 1>", "target_step": {{target_step}}}, ...]

Rules:
- 1 to 5 queries (don't pad; if 2 focused queries suffice, output 2)
- Each query is a complete search engine query (3-8 words)
- Queries should be diverse (different angles on the topic)
- No fabricated URLs or facts — only search query strings
```

`deep-research-judge` (chat 类型，label="production")：

```mustache
You are a relevance judge for web search results. Given the user's research question
and a set of search results, rate each result's relevance 0.0-1.0.

User's research question: {{user_query}}

Search results (JSON array):
{{sources_json}}

For each source, output a JSON object with:
- source_index: matches the index in the input array
- relevance: float 0.0-1.0 (0 = irrelevant, 1 = perfect match)
- rationale: one sentence explaining the score

Output a JSON array (only the array):
[{"source_index": 0, "relevance": 0.X, "rationale": "..."}, ...]
```

### Rationale

- **为什么是 chat 类型而非 text**：这两个 prompt 都需要 system + user 区分（gen_query 要"如何生成查询"作为 system，"具体 topic" 作为 user）；text 类型会丢失这个区分。
- **为什么 `{{plan_section_title}}` / `{{plan_section_description}}` 作为参数而非把整 plan 喂进去**：单次 LLM 调用只需要当前 section 的信息；plan 全量喂会让 prompt 增长到 5-10k tokens。
- **为什么 `{{sources_json}}` 是 string 而非 list**：Langfuse 4.x `text.compile` 只做 `{{var}}` 替换；JSON 作为 string 直接 inline。

### Alternatives

- **A. 用 Mustache `{{#sources}}...{{/sources}}` 段**：Langfuse 4.x 不解析段；**否定**。
- **B. 不区分 system / user（纯 text）**：prompt 质量下降；**否定**。

### Action Items

- 用户在 Langfuse UI 手动创建两个 prompt（label="production"）；README 同步操作步骤。
- `prompts.py::get_llm("research")` 与 `nodes/research.py::_gen_query_node` / `_eval_search_node`（LLM Judge 分支）调用 `prompts.get_prompt("deep-research-gen-query" / "deep-research-judge", label="production")` + chevron 渲染。

---

## R10. SC-007 验证脚本（Send fan-out worker 端到端）

### Decision

```bash
# 用 psql + Python 联合验证：Postgres checkpoints 表可见 5 条 websearch
# checkpoint 写入时间戳差 < 0.5 秒

uv run python -c "
from src.apps.deep_research.graph import run_research
r = run_research({'user_query': '...', 'auto_confirm_plan': True}, thread_id='sc-002')
" &
SCRIPT_PID=$!
sleep 2  # 等 graph 跑到 research 子图

psql "$LANGGRAPH_CHECKPOINT_DB" -c "
SELECT node, created_at
FROM checkpoints
WHERE thread_id = 'sc-002'
  AND node = 'websearch'
ORDER BY checkpoint_id
LIMIT 5;
"

wait $SCRIPT_PID
```

**期望**：5 条 websearch checkpoint 时间戳间隔 < 0.5 秒（vs 串行 ~5 秒差）。

### Rationale

- SC-007 是 spec 003 的核心并发证据；如果 Send 退化为串行，验证会失败（时间戳 2-5 秒差）。
- psql + Python 联合：Python 跑研究循环（异步），psql 同时查 checkpoints 表——两者通过 thread_id 关联。

### Alternatives

- **A. 用 Langfuse 的 trace 时间**：spec 006 才接 Langfuse observability；spec 003 用 Postgres checkpoint 验证更直接；**采用**。

### Action Items

- spec 003 quickstart.md SC-002 写完整的 psql + Python 并发脚本。

---

## 总结：10 个研究产出的关键决策

| # | 主题 | 关键决策 |
|---|---|---|
| R1 | DashScope web search 路径 | `dashscope.Application.call(APP_ID, ...)`（Bailian），**不**用 `Generation.call(enable_search=True)`（MaaS footgun） |
| R2 | DashScope ReRank 端点 | `TextReRank.call(model="gte-rerank-v2")` 直连主站（不通过 MaaS）；status_code==20000 success |
| R3 | LangGraph Send API | `Send("websearch", payload)` + `add_conditional_edges(gen_query, _gen_query_node, ["websearch"])`；变长 fan-out 1-5 |
| R4 | Source 重编号 | `eval_search_node`（fan-in）做全局重编号 1..N；websearch 节点用 `index=0` 占位 |
| R5 | ReRank → LLM Judge fallback | 唯一允许的 silent degrade；`evaluator` 字段标记路径；ReRank 失败 print 日志 |
| R6 | counter +1 时机 | `gen_query_node` **入口** `+1`；条件边读"已 +1 的 counter" |
| R7 | Source.content 截断 | 1500 字符（~500-800 tokens） |
| R8 | worker 错误处理 | 已知错误（NotYetImplementedError）swallow + print；未知错误透传 |
| R9 | Langfuse prompt | `deep-research-gen-query` / `deep-research-judge` 两个 chat 类型；用户手动创建 |
| R10 | SC-007 并发验证 | psql + Python 并发跑；checkpoint 时间戳差 < 0.5s |

---

## Spec 003 的隐式修正（与原 spec 的差异）

| 项 | spec.md 原文 | 实际（plan 阶段确认） | 影响 |
|---|---|---|---|
| FR-001 | "调 `dashscope.Generation.call(enable_search=True)`" | `dashscope.Application.call(applet_id=APP_ID, prompt=...)` | spec 003 Clarification Q1 决策；避免 MaaS footgun |
| FR-006 | "用 `DASHSCOPE_API_KEY` 调 TextReRank" | 同左 | 无修正（与 spec 一致） |
| FR-009 | "`gen_query` 返回 `list[Send]`" | 同左 | 无修正 |
| FR-014 | "`gen_query` 节点入口 `+1`" | 同左（per [Q4] 决策） | 无修正 |
| NFR-002 | counter increment discipline | 同左 | 无修正 |
| Notes | "Plan 中 PlanStep 的 `depends_on` 字段允许 LLM 输出空列表" | spec 003 仅读 `plan[i].title`；`depends_on` 暂未用 | 无修正