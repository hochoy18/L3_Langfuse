# Contracts · Spec 003

> Phase 1 产出：spec 003 引入 / 修改的对外契约（函数签名 + 行为约束 + 错误类型 + Langfuse prompt 模板）。

spec 003 不暴露新 HTTP / gRPC / CLI 接口；它是 LangGraph 图的内部节点实现，所以"contract"是指 **Python 函数签名 + 调用契约 + 异常类型 + Langfuse prompt 占位符**。

---

## C1. `src.apps.deep_research.prompts.get_llm`（spec 003 增量）

### Signature（spec 003 FR-010 实装）

```python
def get_llm(
    role: Literal["plan", "research", "write", "critic", "polish"]
) -> ChatOpenAI:
    """Return a ChatOpenAI instance for the given role.

    spec 002 实装 role="plan"（temperature=0.4）。
    spec 003 实装 role="research"（temperature=0.2）。
    其他 role（write/critic/polish）仍抛 NotYetImplementedError（spec 004 才实装）。
    """
```

### Contract（role="research"）

| 字段 | 值 |
|---|---|
| `model` | `os.environ.get("DASHSCOPE_MODEL", "qwen3.7-plus")`（继承 spec 002 默认） |
| `api_key` | `os.environ["DASHSCOPE_API_KEY"]` |
| `base_url` | `os.environ.get("DASHSCOPE_API_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")` |
| `temperature` | `0.2`（per [Q3] decision） |

### 与 spec 002 role="plan" 的差异

| 维度 | `plan`（spec 002） | `research`（spec 003） |
|---|---|---|
| 用途 | 生成章节化 Plan | 生成检索查询 |
| temperature | 0.4 | 0.2 |
| 输出形态 | JSON list of PlanStep | JSON list of "query strings" |
| Langfuse prompt | `deep-research-plan` | `deep-research-gen-query` / `deep-research-judge` |

### Test Cases

```python
# TC-1: role="research" returns valid LLM
llm = get_llm("research")
assert isinstance(llm, ChatOpenAI)
assert llm.model_name == os.environ.get("DASHSCOPE_MODEL", "qwen3.7-plus")
assert llm.temperature == 0.2

# TC-2: DASHSCOPE_API_KEY missing
import os
saved = os.environ.pop("DASHSCOPE_API_KEY", None)
try:
    with pytest.raises(RuntimeError):
        get_llm("research")
finally:
    if saved:
        os.environ["DASHSCOPE_API_KEY"] = saved
```

---

## C2. `src.apps.deep_research.tools.DashScopeWebSearch._run`（spec 003 重写）

### Signature

```python
class DashScopeWebSearch(BaseTool):
    """Web search backed by Bailian (百炼) web-search App via dashscope.Application.call."""

    name: str = "dashscope_web_search"
    description: str = "Search the web via DashScope Bailian web search App."
    max_results: int = Field(default=5, description="Cap on results returned per call.")

    def _run(self, query: str, **kwargs) -> list[Source]:
        """Call Bailian App → parse search_results → list[Source] (index=0 placeholder)."""

    async def _arun(self, query: str, **kwargs) -> list[Source]:
        """Async version: same body as _run, awaited."""
```

### Contract

| 条件 | 行为 |
|---|---|
| `DASHSCOPE_WEB_SEARCH_APP_ID` 缺失 | 抛 `RuntimeError("DASHSCOPE_WEB_SEARCH_APP_ID not set; ... Do NOT fall back to dashscope.Generation.call(enable_search=True) — that path is silently broken on the regional Aliyun MaaS gateway.")`（per FR-004 + Q1 footgun defense） |
| `DASHSCOPE_WEB_SEARCH_APP_TOKEN` 缺失 | 抛 `RuntimeError("DASHSCOPE_WEB_SEARCH_APP_TOKEN not set; set it in .env to call Bailian web search App.")`（per FR-004） |
| `USE_WEB_SEARCH_APP=false` | **read-and-warn**：env 被读但**不**实际切换路径（仍调 Bailian App）；spec 003 仅保留 env name 给 dev/CI 未来用，不实现 bypass 路径（per FR-004 + 实际实装 tools.py `_require_env`） |
| App 返回 HTTP 200 + 2 层 JSON 解析 `resp.output['text']` → `outer['result']['content'][0]['text']` → `inner['pages']` 非空 | 解析为 `list[Source]`，每个 `index=0` 占位（per R4，renumbering 由 `_eval_search_node` 做） |
| App 返回 HTTP 200 + `result.isError=true` OR `pages` 字段空 | 抛 `NotYetImplementedError("dashscope web search returned 0 results for query=<q>")`（per T010 + FR-005） |
| App 返回 HTTP 429 | 指数退避 2 次（base=1s, exp=2x）；仍失败抛 `NotYetImplementedError("dashscope web search rate limited after 2 retries: query=<q>")` |
| App 返回 HTTP 5xx | 同 429 路径 |
| App 返回 HTTP 400/401/403 | 不重试，立即抛 `NotYetImplementedError` |
| 未知异常（KeyError / AttributeError） | 透传（让 Send fan-out 失败，便于 review） |

### Retry 行为

```python
def _run(self, query: str, **kwargs) -> list[Source]:
    last_err = None
    for attempt in range(3):  # 0, 1, 2 (initial + 2 retries)
        try:
            return self._do_query(query)
        except dashscope.common.error.RequestFailure as e:
            # dashscope 1.27.7: RequestFailure exposes `http_code` (NOT `code`).
            # `code` is for App/Bailian-style 20000 envelopes; HTTP path uses http_code.
            http_code = getattr(e, "http_code", None) or getattr(e, "code", None)
            if http_code not in (429, 500, 502, 503) or attempt == 2:
                raise NotYetImplementedError(f"dashscope web search failed: {e}") from e
            time.sleep(1.0 * (2 ** attempt))  # 0s, 1s, 2s
            last_err = e
            print(f"[websearch] retry attempt={attempt + 1} query={query[:50]}")
    raise NotYetImplementedError(f"dashscope web search rate limited after 2 retries: query={query[:50]}")
```

### Side Effects

- 调用 `dashscope.Application.call(app_id=APP_ID, prompt=query, api_key=APP_TOKEN)`（`app_id` 关键字；dashscope 1.27.7 中**没有** `applet_id`）
- `print` retry 日志（spec 006 接 Langfuse span 前占位）
- 不写 Postgres；不写 state

---

## C3. `src.apps.deep_research.tools.DashScopeReRank._run`（spec 003 重写）

### Signature

```python
class DashScopeReRank(BaseTool):
    """ReRank service backed by DashScope gte-rerank-v2 model via SDK direct call."""

    name: str = "dashscope_rerank"
    description: str = "ReRank documents by relevance via gte-rerank-v2."
    top_k: int = Field(default=5, description="Number of top documents to return.")

    def _run(self, query: str, sources: list[Source], **kwargs) -> list[Source]:
        """Call TextReRank → write relevance_score to each Source.score → return.

        Takes `sources: list[Source]` directly (NOT `documents: list[str]`);
        internally converts via `[s.content for s in sources]` before the
        TextReRank.call, then writes scores back via `r.index` back-mapping
        (per [[dashscope-rerank-sdk-quirks]]: SDK does NOT guarantee result
        order, so positional mapping is unsafe).
        """
```

### Contract

| 条件 | 行为 |
|---|---|
| `len(sources) == 0` | 返回原 `sources`（不抛错，per FR-008 边界保护） |
| `len(sources) >= 1` | 内部 `documents = [s.content for s in sources]`；调 `dashscope.TextReRank.call(model="gte-rerank-v2", query=query, documents=documents, top_n=len(documents), return_documents=True)` |
| ReRank 返回 status_code==20000 | 遍历 `resp.output.results`，按 `r.index`（input-doc position，per [[dashscope-rerank-sdk-quirks]]）回填 `sources[r.index].score = float(r.relevance_score)`；返回原 `sources` list（in-place 修改） |
| ReRank 返回 status_code != 20000 | 抛 `NotYetImplementedError("dashscope rerank failed: <code>: <message>")`（per FR-007） |
| `DASHSCOPE_API_KEY` 缺失 或 抛 `dashscope.common.error.DashScopeException` | 包成 `NotYetImplementedError("dashscope rerank failed: <error>")`（per FR-007；注意 1.27.7 异常类在 `dashscope.common.error`，**不是** `dashscope.exceptions`） |

### Side Effects

- 调用 `dashscope.TextReRank.call(...)` 直连 DashScope 主站（不经 MaaS）
- 修改传入 `sources` 列表中每个 `Source` 的 `score` 字段（in-place）
- 不重试（per FR-007 + ADR-0004）
- 不写 Postgres / state

---

## C4. `src.apps.deep_research.nodes.research._gen_query_node`（spec 003 新增）

### Signature

```python
from langgraph.types import Send

def _gen_query_node(state: DeepResearchState) -> list[Send]:
    """Generate 1-5 ResearchQuery for current iteration; dispatch via fan-out."""
```

### Contract

| 步骤 | 行为 |
|---|---|
| 1 | `state["iteration_counts"]["research"] += 1`（per R6 + FR-014，**入口**而非出口） |
| 2 | 调 `prompts.get_prompt("deep-research-gen-query", label="production")` |
| 3 | chevron 渲染 `user_query` / `plan_section_title` / `plan_section_description` / `iteration` / `target_step` |
| 4 | 调 `prompts.get_llm("research").invoke(rendered_messages)` |
| 5 | 解析 LLM JSON 为 `list[ResearchQuery]`（每个含 `text` / `target_step` / `iteration`） |
| 6 | 返回 `[Send("websearch", {"query": q.text, "target_step": q.target_step, "iteration": q.iteration, "query_index": i}) for i, q in enumerate(queries)]` |
| 失败：JSON 解析失败 | 重试 1 次（同 prompt 重 invoke）；仍失败抛 `NotYetImplementedError("gen_query produced 0 queries after retry")` |
| 失败：HTTP 429/5xx | 指数退避 2 次（继承 spec 002 FR-006(d)）；仍失败抛 `NotYetImplementedError("dashscope rate limited after 2 retries")` |
| 失败：HTTP 400/401 | 不重试，立即抛 `NotYetImplementedError` |
| 失败：`len(produced) == 0` after retry | 抛 `NotYetImplementedError("gen_query produced 0 queries after retry")`（per FR-009 + [Edge Case]） |

### Side Effects

- 修改 `state["iteration_counts"]["research"]`（+1）
- 不写 `state["sources"]` / `state["search_evaluations"]`（由 websearch / eval_search 节点负责）
- print `[research] node=gen_query iteration=<N>` 一行（per FR-021）

---

## C5. `src.apps.deep_research.nodes.research._websearch_node`（spec 003 新增）

### Signature

```python
def _websearch_node(payload: dict) -> dict:
    """Send worker: call DashScopeWebSearch with payload['query']; return partial sources."""
```

### Contract

| 条件 | 行为 |
|---|---|
| 正常 | 调 `DashScopeWebSearch().run(query=payload["query"])`；为每条 Source 写 `query_index=payload["query_index"]`；返回 `{"sources": [Source, ...], "research_queries": [ResearchQuery]}` |
| 已知错误（NotYetImplementedError from websearch） | print `[websearch] known error swallowed: <e>`；返回 `{"sources": [], "research_queries": [ResearchQuery]}`（per FR-013 + R8） |
| 未知错误（其他 Exception） | 透传，让 Send fan-out 失败（per FR-022） |
| payload 缺 `query` | 抛 `KeyError("payload missing 'query'")`（透传） |

### Send Payload Schema

```python
Send(
    "websearch",
    {
        "query": str,            # 搜索查询字符串
        "target_step": int,      # 0..len(plan)-1
        "iteration": int,        # 1..3
        "query_index": int,      # 0..len(queries)-1
    },
)
```

### Side Effects

- print `[research] node=websearch query=<q[:50]>` 一行（per FR-021）
- 写 partial `state["sources"]`（LangGraph Send fan-in 拼接）
- 写 partial `state["research_queries"]`

---

## C6. `src.apps.deep_research.nodes.research._eval_search_node`（spec 003 新增）

### Signature

```python
def _eval_search_node(state: DeepResearchState) -> dict:
    """Fan-in: dedupe by url; ReRank or LLM Judge; renumber; filter < 0.3."""
```

### Contract

| 步骤 | 行为 |
|---|---|
| 1 | `sources = state["sources"]`（LangGraph 拼接后的 merged list） |
| 2 | URL 去重：相同 url 保留首条（保留后分数最高的） |
| 3 | 按 `query_index` 分组：`{q: [s for s in sources if s.query_index == q] for q in set(...)}` |
| 4a（USE_RERANK=true，默认） | 调 `DashScopeReRank().run(query=q.text, documents=[s.content or s.title for s in group])`；写回 `s.score`；**失败** → fallback 4b |
| 4b（USE_RERANK=false 或 4a 失败） | 调 `prompts.get_prompt("deep-research-judge")` + `prompts.get_llm("research")`；解析 JSON；写 `search_evaluations`（`evaluator="llm_judge"`） |
| 5 | 过滤 score < 0.3 的 Source（仅 4a 路径过滤；4b 路径保留所有） |
| 6 | 按 score 降序稳定排序，重编号 `index = 1..N` |
| 7 | 写 `state["sources"]`（renumbered）+ `state["search_evaluations"]` |
| 失败：LLM Judge JSON 解析失败 | 重试 1 次（同 prompt 重 invoke）；仍失败抛 `NotYetImplementedError("llm judge failed after retry: <reason>")` |
| 失败：ReRank SDKError | **fallback 到 LLM Judge 路径**（per FR-017 + R5 + ADR-0004）——这是 spec 003 唯一允许的 silent degrade |
| 失败：连续 3 轮 sources=0 after filter | 抛 `NotYetImplementedError("research: 3 iterations yielded 0 sources after ReRank filter")`（per FR-015） |

### Side Effects

- print `[research] node=eval_search iteration=<N> sources_so_far=<M>` 一行
- print `[eval_search] rerank failed, falling back to LLM judge: <reason>`（仅 fallback 时）
- 写 `state["sources"]`（final, renumbered）
- 写 `state["search_evaluations"]`（final）

---

## C8. Langfuse Prompt `deep-research-gen-query`

### Name

`deep-research-gen-query`

### Type

`chat`（system + user message 区分；spec 003 R9 决策）

### Labels

`["production"]`

### Placeholders（用户消息）

| 占位符 | 类型 | 来源 | 必填 |
|---|---|---|---|
| `{{user_query}}` | str | `state["user_query"]` | ✅ |
| `{{plan_section_title}}` | str | 当前 iteration 对应的 `plan[i].title` | ✅ |
| `{{plan_section_description}}` | str | `plan[i].description`（None → ""） | ❌ |
| `{{iteration}}` | int | `state["iteration_counts"]["research"]` | ✅ |
| `{{target_step}}` | int | `plan` 索引 | ✅ |

### Mustache 模板（用户消息）

```mustache
You are a research query generator. Produce 1-5 diverse, focused web search queries
that help gather information about a specific section of a research plan.

User's overall research question:
{{user_query}}

The section of the plan this iteration covers:
{{plan_section_title}}
Description: {{plan_section_description}}

This is iteration #{{iteration}} of up to 3 iterations total.
Target step index: {{target_step}}

Output a JSON array (only the array, no markdown fences, no commentary):
[{"text": "<search query 1>", "target_step": {{target_step}}}, ...]

Rules:
- 1 to 5 queries (don't pad; if 2 focused queries suffice, output 2)
- Each query is a complete search engine query (3-8 words)
- Queries should be diverse (different angles on the topic)
- No fabricated URLs or facts — only search query strings
- target_step must equal {{target_step}} in every element
```

### System Message（Langfuse UI 上 system message 字段）

```
You are an expert research query generator for an autonomous research agent.
Your output is consumed by JSON parsers, so it must be valid JSON only.
```

---

## C9. Langfuse Prompt `deep-research-judge`

### Name

`deep-research-judge`

### Type

`chat`

### Labels

`["production"]`

### Placeholders（用户消息）

| 占位符 | 类型 | 来源 | 必填 |
|---|---|---|---|
| `{{user_query}}` | str | `state["user_query"]` | ✅ |
| `{{sources_json}}` | str | `[s.model_dump(mode="json", include={"index", "title", "url", "content"}) for s in sources]` | ✅ |

### Mustache 模板（用户消息）

```mustache
You are a relevance judge for web search results. Given the user's research question
and a set of search results, rate each result's relevance 0.0-1.0.

User's research question:
{{user_query}}

Search results (JSON array; index is the original Source.index before renumbering):
{{sources_json}}

For each source, output a JSON object with:
- source_index: matches the index in the input array
- relevance: float 0.0-1.0 (0 = irrelevant, 1 = perfect match)
- rationale: one sentence explaining the score

Output a JSON array (only the array, no markdown fences):
[{"source_index": 0, "relevance": 0.X, "rationale": "..."}, ...]

Rules:
- relevance MUST be in [0.0, 1.0]
- rationale MUST be ≤ 200 chars
- Output exactly N elements where N = len(sources_json)
```

### System Message

```
You are a strict relevance judge. Output ONLY valid JSON. No commentary.
```

---

## C10. Env Var Schema（spec 003 新增消费）

| env var | 默认 | 必需 | 用途 | spec 003 角色 |
|---|---|---|---|---|
| `DASHSCOPE_API_KEY` | （必填） | ✅ | DashScope 主站 + ReRank 凭证 | 继承 spec 002 |
| `DASHSCOPE_API_BASE_URL` | `https://dashscope.aliyuncs.com/compatible-mode/v1` | ❌ | DashScope OpenAI-compatible endpoint | 继承 spec 002 |
| `DASHSCOPE_MODEL` | `qwen3.7-plus`（per spec 002 实装） | ❌ | LLM model name | 继承 spec 002 |
| `DASHSCOPE_WEB_SEARCH_APP_ID` | （必填） | ✅ | Bailian App ID（websearch） | **spec 003 新消费** |
| `DASHSCOPE_WEB_SEARCH_APP_TOKEN` | （必填） | ✅ | Bailian App Token（websearch） | **spec 003 新消费** |
| `USE_RERANK` | `true` | ❌ | 跳过 ReRank 走 LLM Judge | 继承 ADR-0004 |
| `USE_WEB_SEARCH_APP` | `true` | ❌ | dev/CI bypass | spec 003 新增旁路 |
| `LANGGRAPH_CHECKPOINT_DB` | `postgresql://postgres:postgres@localhost:5432/postgres` | ❌ | Postgres checkpointer | 继承 foundation |
| `LANGFUSE_HOST` / `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` | （必填） | ✅ | Langfuse client | 继承 spec 002 |

**新增 env var 说明**：`USE_WEB_SEARCH_APP` 是 spec 003 新增的旁路开关（per FR-004）；spec 003 仅定义 env 名字 + 默认值（true），**不**实现 bypass 路径——这是 dev/CI 未来用。constitution Constraint #7 要求"新 env var 需要 ADR"——但本 spec 视 `USE_WEB_SEARCH_APP` 为"环境配置"而非"架构决策"（与 `USE_RERANK` 同级），不开新 ADR，纳入本节作为 spec-level 契约。

`DASHSCOPE_WEB_SEARCH_APP_ID` / `_TOKEN` 不是 spec 003 新增 env var（`.env` 已配置，per [[dashscope-mmaas-enable-search-needs-bailian-app]]），是 spec 003 **首次消费**既有配置——无需新 ADR。

---

## C11. State Schema Contract（与 spec 001/002 兼容）

spec 003 不修改 `DeepResearchState` 字段——继承 spec 001 FR-003 定义。

spec 003 仅改变以下字段的**写入者**：

| 字段 | spec 001 写入者 | spec 002 写入者 | spec 003 写入者 |
|---|---|---|---|
| `plan` | stub plan_node | real plan_node（LLM） | 不写 |
| `messages` | （无） | confirm_plan_node（HumanMessage） | 不写 |
| `auto_confirm_plan` | run_research 入口 | 同 | 同 |
| `iteration_counts` | run_research 入口（初始化） | 不动 | `_gen_query_node`（+1 research） |
| `research_queries` | stub（空） | 不写 | `_websearch_node`（每条 query） |
| `sources` | stub（2 条占位） | 不写 | `_websearch_node`（partial）+ `_eval_search_node`（final, renumbered） |
| `search_evaluations` | stub（2 条占位） | 不写 | `_eval_search_node` |
| `outline` / `draft` / `critique_history` / `final_report` | stub | 不写 | spec 004 接手 |

下游消费（spec 004）无需知道 sources 是 stub 还是真实产出——Pydantic schema 不变。

### Send fan-in reducer 声明（per F4 convergence finding）

LangGraph 1.2.x 的 Send fan-in 不会一次合并 worker delta，而是按 worker 完成顺序逐个调用 reducer N 次（per [[langgraph-1.2-send-fanin-reducer-multicall]]）。spec 003 为以下字段显式声明 reducer，**不能**依赖默认的 `LastValue` 或 `operator.add`：

| 字段 | reducer | identity 字段 | 位置 |
|---|---|---|---|
| `research_queries` | `_merge_queries_by_identity` | `(text, target_step, iteration)` | `state.py::82-99` |
| `raw_sources` | `_merge_sources_by_identity` | `(query_index, url)`（**不**含 `index`，因 `index` 会被 renumber 改写） | `state.py::46-79` |

- `raw_sources` 是 spec 003 US3 新增的 12th 字段（per US3 data-model split：Send fan-in 写到 `raw_sources`，`_eval_search_node` 读 `raw_sources` → 写最终 `sources` 覆盖）。它在 spec 001/002 字段表之外，但在 US3 acceptance 内。
- `research_queries` 的 reducer 是 spec 003 实装时新写的（未在 plan Phase 1 data-model 章节显式声明，per F4 finding）；此处补齐以保持 plan + state.py + 实际行为三处一致。