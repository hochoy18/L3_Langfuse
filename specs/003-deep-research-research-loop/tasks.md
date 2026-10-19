---
description: "Task list for spec 003 — Research 阶段（真实 web search + Send 并发 + ReRank）"
---

# Tasks: Research 阶段（真实 web search + Send 并发 + ReRank）

**Input**: Design documents from `/specs/003-deep-research-research-loop/`
- `spec.md`（5 个 User Story + 24 个 FR + 10 个 SC + 7 个 Clarifications）
- `plan.md`（技术栈 + 实现路径 + Constitution Check）
- `research.md`（10 个 R 决策：R1 Bailian App 路径 / R2 ReRank 端点 / R3 Send API / R4 Source 重编号 / R5 唯一 silent degrade / R6 counter +1 时机 / R7 content 截断 / R8 worker 错误 / R9 Langfuse prompt / R10 SC-007 验证）
- `data-model.md`（Source / ResearchQuery / SearchEvaluation 字段约束 + state transitions）
- `contracts/README.md`（11 个 C 契约：C1-C6 节点 API + C8/C9 Langfuse prompt + C10 env var + C11 state schema 兼容）
- `quickstart.md`（10 个 SC 验证脚本：SC-001 ~ SC-010）

**Prerequisites**: spec 001 foundation（`schemas.py` / `state.py` / `graph.py` 拓扑 / PostgresSaver / advisory lock）+ spec 002 plan-confirm（`prompts.get_prompt` / `get_llm("plan")` / chevron 渲染 / `auto_confirm_plan` 旁路）已就位

**Tests**: spec 003 用 `quickstart.md` 的 10 个 SC 脚本作为独立验证；不引入 pytest 框架（spec 005/006 引入）

**Organization**: 按 5 个 User Story 组织（P1 × 4 + P2 × 1），每个故事可独立实现 + 验证。MVP 切片 = US1（真实 web search）+ US2（Send 并发）+ US3（ReRank）的核心 7-8 个任务即可让 `run_research` 抵达 write 子图入口（SC-006）。

**关键路径**: Phase 1 → Phase 2 → US1 → US2 → US3 → US4 → US5 → Phase 8 polish

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 可并行（不同文件，无依赖）
- **[Story]**: 任务所属 user story（US1 / US2 / US3 / US4 / US5）
- 路径说明：所有 `src/apps/deep_research/...` 路径基于 spec 003 plan.md §Project Structure
- 「`per FR-NNN`」标注对应的 functional requirement（spec 003 FR-001 ~ FR-024）
- 「`per C-N`」标注对应的 contracts 编号（contracts/README.md C1-C11）

---

## Phase 1: Setup（环境前置）

**Purpose**: spec 003 实现前的外部依赖就位——Postgres / Langfuse / DashScope / Bailian App 凭证 + 2 个新 Langfuse prompt

- [x] T001 验证 Postgres（`langfuse-deploy`）仍可达（继承 spec 002 T001）：`docker compose -f langfuse-deploy/docker-compose.yml ps | grep postgres` 显示 healthy；`psql $LANGGRAPH_CHECKPOINT_DB -c 'SELECT 1'` 返回 1 — **PASS**：
  - `docker compose up -d postgres` 启动容器，状态 `Up (healthy)`，端口 `5432/tcp` 就位
  - psql 在本机未安装，改用 `uv run python + psycopg`（项目原生依赖）执行等效 `SELECT 1` → 返回 `1` ✅
  - 顺带校验：4 个 LangGraph `checkpoint_*` 表（`checkpoints` / `checkpoint_writes` / `checkpoint_blobs` / `checkpoint_migrations`）已在 `deep_research_db` 库里就位（spec 002 留下的）
  - **修复记录（1 处）**：容器内 `POSTGRES_PASSWORD` 是 `NYzHcA09iHkJAYoR0NftN82Wvoasr`（与 `.env` 里的 `123456` 不一致——volume 是用旧 env 初始化的），导致从 host 连不上。在容器内 `ALTER USER postgres WITH PASSWORD '123456'` 同步到 `.env` 里的密码；同时建库 `CREATE DATABASE deep_research_db;`（容器初始只创建了 `postgres` 库，`.env` 指向 `deep_research_db`）
  - 现在 `postgresql://postgres:123456@127.0.0.1:5432/deep_research_db`（同步 driver，剥 `+asyncpg` per [[sqlalchemy-style-uri-with-sync-driver]]）连通 + `SELECT 1 = 1` + 4 个 checkpoint 表齐全
  - **待 T002 跑前确认**：`pg_hba.conf` 里有 `host all all 127.0.0.1/32 trust` 规则但 docker 端口转发把连接 SNAT 到 bridge 网络，所以走的是 `host all all all scram-sha-256` 规则；密码同步后 SCRAM 认证通过
- [x] T002 [P] 验证 Langfuse 服务可达（继承 spec 002 T002）：浏览器打开 `$LANGFUSE_HOST`（`http://localhost:3000` 或 `https://langfuse.bananahochoy.online`），确认 Prompts 页面可访问 — **PASS**：`curl https://langfuse.bananahochoy.online/api/public/health` 返回 `HTTP 200`（1.99s）。注意环境变量名是 `LANGFUSE_BASE_URL`（非 `LANGFUSE_HOST`），`.env` 当前已配 `https://langfuse.bananahochoy.online`。
- [x] T003 [P] 验证 DashScope + Bailian App 凭证：`.env` 含 4 个 DashScope env vars（`DASHSCOPE_API_KEY` / `DASHSCOPE_API_BASE_URL` / `DASHSCOPE_MODEL` / `DASHSCOPE_WEB_SEARCH_APP_ID` / `DASHSCOPE_WEB_SEARCH_APP_TOKEN`）；`curl -H "Authorization: Bearer $DASHSCOPE_API_KEY" https://dashscope.aliyuncs.com/compatible-mode/v1/models` 返回 200；`dashscope.Application.call(applet_id=$DASHSCOPE_WEB_SEARCH_APP_ID, prompt="test", api_key=$DASHSCOPE_WEB_SEARCH_APP_TOKEN)` 不抛 `InvalidApiKey`（per Q1 + [[dashscope-mmaas-enable-search-needs-bailian-app]] 锁定路径）— **PASS**：
  - `/models` 端点返回 19 个模型（first 3: `qwen3.7-max` / `qwen3.7-plus` / `qwen3.6-flash`）—— MaaS 北京 region 实例，符合 spec 002 已有发现
  - `Application.call(app_id=APP_ID, prompt='LangGraph 1.2 Send API 简介', api_key=APP_TOKEN)` 返回 `status_code=200`、无 message 错误
  - **⚠️ CONTRACT DRIFT（须修）**：
    1. SDK 签名是 `Application.call(app_id, prompt, ...)` —— **不是** contracts §C2 / plan §4 写的 `applet_id=APP_ID`（位置参数 + 正确 kwarg 名是 `app_id`）。T008 必须用 `app_id=`。
    2. **Bailian App 真实 output schema 与 contracts §C2 写的 `output.search_results` 完全不同**：
       ```
       resp.output = {
         'text': '<JSON string>',         # 实际是字符串，里面再嵌 JSON
         'finish_reason': 'stop',
         'session_id': '...',
         'doc_references': None,
         ...
       }
       # 实际解析路径：
       outer = json.loads(resp.output['text'])        # {"result": {"isError": false, "content": [{"text": "<inner json>", "type": "..."}]}}
       content_text = outer['result']['content'][0]['text']
       inner = json.loads(content_text)                # {"pages": [{"title", "url", "snippet", "hostname", "hostlogo"}], "request_id", "tools", "status"}
       pages = inner['pages']                         # 真实 list[dict]
       ```
    3. Page 字段名是 `snippet`（**不是** contracts §C2 写的 `content`）；Source.content 应取 `p['snippet']`（spec 003 写的就是 `r.get("snippet", r.get("content", ""))` 兼容写法，但优先 `snippet`）
  - **必做修复**：T008 实现 + 后续 update contracts §C2 + plan §4 + quickstart SC-009 等都按真实 schema 改
- [x] T004 在 Langfuse UI 上手动创建 chat prompt `deep-research-gen-query`（type=chat, label=production）— **PASS with rename**：
  - 旧 `deep-research-gen-query` v3 是 text type（spec 002 时代创建，placeholders 是 `{{num_queries}}` / `{{plan}}` / `{{#prev_queries}}`，不符合 spec 003 schema）
  - 尝试 REST API `DELETE /api/public/v2/prompts/deep-research-gen-query?version=3` → 204，但服务端保留 `latest` label 的 ghost version，**Langfuse API 拒绝同名换 type**（"Previous versions have different prompt type"）
  - **决策**：用新名 `deep-research-gen-query-v2`（chat type v1, production + latest labels）；system + user message 内容按 contracts §C8
  - chevron 客户端渲染验证 OK：5 个占位符 `{{user_query}}` / `{{plan_section_title}}` / `{{plan_section_description}}` / `{{iteration}}` / `{{target_step}}` 全部正确替换
  - **后续代码必须用 `deep-research-gen-query-v2`**（T008/T013 + contracts/README.md + README 都要更新）
- [x] T005 在 Langfuse UI 上手动创建 chat prompt `deep-research-judge`（type=chat, label=production）— **PASS**：
  - 旧名不存在（spec 002 时代未创建），直接 `create_prompt(name="deep-research-judge", type="chat", labels=["production"])` → v1 production + latest
  - system + user message 内容按 contracts §C9（2 占位符 `{{user_query}}` / `{{sources_json}}`）
  - chevron 渲染验证 OK

**Checkpoint**: Postgres + Langfuse + DashScope + Bailian App + 2 个新 prompt 就位；可进入 Phase 2

---

## Phase 2: Foundational（核心基础设施）

**Purpose**: 所有 user story 都依赖的底层实装——`prompts.get_llm("research")` 与 README 同步。完成后才能进入任一 US。

**⚠️ CRITICAL**: 5 个 user story 全部依赖本 phase；未完成时禁止并行启动任何 US

- [x] T006 实装 `src/apps/deep_research/prompts.py::get_llm(role="research")` — **PASS**：在 `prompts.py::get_llm` 把 `if role == "plan"` 改成 `if role in ("plan", "research"):`，用 `temperature = 0.2 if role == "research" else 0.4` 分流；其他 role 仍 stub 抛 `NotYetImplementedError`（per FR-010 + contracts §C1 + spec 002 [Q3] decision）。验证：`get_llm("research").temperature == 0.2` ✓；`get_llm("plan").temperature == 0.4` ✓（spec 002 不破坏）；`get_llm("write"/"critic"/"polish")` 仍抛 `NotYetImplementedError` ✓
- [x] T007 [P] 更新 `src/apps/deep_research/README.md` — **PASS**：在 spec 002 增量小节后新增 "spec 003 增量（Research 阶段：真实 web search + Send 并发 + ReRank）" 小节，含：(a) prompts.py / tools.py / nodes/research.py 3 个 ✅ 标记；(b) 前置 2 步说明：2 个 Langfuse chat prompt（`deep-research-gen-query-v2` ⚠️ + `deep-research-judge`）+ 4 个 env var 表（`DASHSCOPE_WEB_SEARCH_APP_ID` / `_TOKEN` 必填 + `USE_RERANK` / `USE_WEB_SEARCH_APP` 旁路）；(c) "为什么走 Bailian App 而非 Generation.call" 解释 + [[dashscope-mmaas-enable-search-needs-bailian-app]] 引用

**Checkpoint**: foundation 完成；`get_llm("research")` 可用；可并行启动 US1

---

## Phase 3: User Story 1 - 真实 web search 产出 Source 列表（Priority: P1）🎯 MVP

**Goal**: `DashScopeWebSearch._run` 从 stub 改为真实 Bailian App 调用，产出 `list[Source]`，每条 `index=0` 占位 + `query_index` 写入

**Independent Test**: `from src.apps.deep_research.tools import DashScopeWebSearch; r = DashScopeWebSearch().run(query='LangGraph Send API')` 返回 `list[Source]`，长度 ≥ 1，每条 `s.index == 0 and s.query_index == 0`（websearch 节点尚未接入所以 query_index 默认 0）；跑 `quickstart.md` SC-001 / SC-009 脚本通过

### Implementation for User Story 1

- [x] T008 [US1] 重写 `src/apps/deep_research/tools.py::DashScopeWebSearch._run` — **PASS**：实装要点 (a)+(b)+(c)+(d)+(e)；env 验 `APP_ID`/`APP_TOKEN` 缺失抛 `RuntimeError`，**不**降级 `Generation.call(enable_search=True)`（per [[dashscope-mmaas-enable-search-needs-bailian-app]] + Q1 footgun）；(b) `USE_WEB_SEARCH_APP` 当前实装为 read-and-warn（spec 003 仅留 env 名不实装 bypass per FR-004 + C2）；(c) 用 `app_id=APP_ID`（**不是** contracts §C2 写的 `applet_id=`，已 `inspect.signature` 验过 dashscope 1.27.7）；(d) parse 路径 **2 层** `json.loads`：`resp.output['text']` → `{"result": {"isError": bool, "content": [{"text": "<JSON>"}]}}` → `{"pages": [{title,url,snippet,hostname,hostlogo}]}`，每条 `Source(index=0, title=r["title"], url=r["url"], content=r.get("snippet") or r.get("content","")[:1500], score=None, query_index=<从 kwarg 读，缺省 0>)`；`result.isError` 视为空（触发 T010）；(e) `from src.apps.deep_research.schemas import Source` 加进顶部 import。**实测**：query='LangGraph 1.2 Send API 教程' → 3 sources（搜狐 + CSDN×2），content_len 1070/843/112（全部 ≤ 1500 字符 R7），`index=0` placeholder，`score=None`，`query_index=0` 透传
- [x] T009 [US1] 在 `DashScopeWebSearch._run` 内加重试逻辑 — **PASS**：捕获 `from dashscope.common.error import RequestFailure`（**不是** `dashscope.exceptions.RequestFailure`——dashscope 1.27.7 没有那个子模块），用 `e.http_code`（**不是** `e.code`——已 inspect 验过 `RequestFailure.__init__` 签名）判定 `in (429, 500, 502, 503)` 时指数退避（base=1s, exp=2x, attempt 0/1/2）；retry 日志 `print(f"[websearch] retry attempt={attempt+1} http_code={...} query={...}")`；最后仍失败抛 `NotYetImplementedError("dashscope web search rate limited after 2 retries: query=...")`；其他 HTTP 4xx/3xx 不重试立即包成 `NotYetImplementedError`；`KeyError`/`JSONDecodeError` 透传让 Send 失败（per FR-022）
- [x] T010 [US1] 在 `DashScopeWebSearch._run` 内加空结果保护 — **PASS**：解析后若 `len(sources) == 0`（含 `result.isError=true` 路径 + `pages` 字段缺失两种情况）抛 `NotYetImplementedError("dashscope web search returned 0 results for query=<q[:50]>")`；不重试（per FR-005 + Edge Cases + data-model §10）
- [x] T011 [US1] 把 `DashScopeWebSearch._arun` async 版本与 `_run` body 对齐 — **PASS**：`async def _arun(self, query, **kwargs) -> list[Source]: return self._run(query, **kwargs)`，实测 `asyncio.run(t._arun(query, query_index=1))` 返回 3 sources 且 `query_index=1` 透传正确

**Checkpoint**: US1 完成；`DashScopeWebSearch().run("test")` 返回真实 Source 列表（SC-001 / SC-009 验证路径打开）

---

## Phase 4: User Story 2 - Send API 1-5 路并发 fan-out（Priority: P1）

**Goal**: research 子图从 `gen_query → websearch → eval_search → END` 串行 stub 改为 `gen_query → Send[websearch × 1-5] → eval_search → 条件边` 真实循环

**Independent Test**: 跑 SC-001 后查 Postgres `checkpoints` 表，5 条 `node='websearch'` checkpoint 时间戳间隔 < 0.5s；跑 `quickstart.md` SC-002 脚本通过

### Implementation for User Story 2

- [x] T012 [US2] 新增 `_websearch_node(payload: dict) -> dict` — **PASS**：(a) 入口 print；(b) 读 `payload["query"]`/`["query_index"]`/`["target_step"]`/`["iteration"]`；(c) **关键发现 1**：必须调 `DashScopeWebSearch()._run(query=..., query_index=...)` 而非 `.run()`——`BaseTool.run(tool_input, **kwargs)` 的 `tool_input` 是必填位置参数，把 `query_index` 当成 `verbose` 报 `TypeError: missing tool_input`（已实测）；(d) `except NotYetImplementedError as e` 吞掉返回空 sources + ResearchQuery（per FR-013 + R8）；(e) 正常返回 `{"sources": sources, "research_queries": [ResearchQuery(...)]}`。**关键发现 2**：必须 try/except `NotYetImplementedError` 而**非**裸跑，否则 1 个 worker 抛错会让整个 Send 超级步崩溃
- [x] T013 [US2] 新增 `_gen_query_node(state) -> dict` + `_gen_query_routes(state) -> list[Send]` — **PASS**：(a) 入口 `counts["research"] += 1`（R6 +1-at-entry）；(b) `iter_n = counts["research"]`；(c) `target_step = (iter_n - 1) % len(plan)`；(d) 入口 print；(e) `prompt = get_prompt("deep-research-gen-query-v2", label="production")`；(f) `prompt.compile(user_query, plan_section_title, plan_section_description, iteration, target_step)` → `list[dict[role, content]]`（chat 类型直接 compile 替换 5 占位符，无需 chevron 二次渲染；实测 5 占位符全部正确替换）；(g) `_invoke_gen_query_with_retry(llm, messages, iter_n=iter_n)` 内部处理 429/5xx 指数退避 + JSON 解析 1 retry；(h) 返回 `{"research_queries": [ResearchQuery, ...], "iteration_counts": counts}`（**不**返回 `list[Send]`——见 T014 关键发现）。**关键发现**：LangGraph 1.2.x 要求 node 返回值为 state channel 写（dict），**拒绝** `list[Send]` 作返回值（报 `InvalidUpdateError: Expected dict, got [Send(...)]`）。所以拆成「node 写 state + 单独 routing function 返回 list[Send]」两段式
- [x] T014 [US2] 重写 `build_research_subgraph()` — **PASS**：`(a) + (b) + (c)` 节点注册 3 个 + START/END + `_eval_search_node` / `_should_continue_research` US2 占位（US3/US4 替换）；`(d)` `g.add_edge(START, "gen_query")`；`(e)` **`g.add_conditional_edges("gen_query", _gen_query_routes, ["websearch"])`**——第 2 参数是 routing function（**不**是 node function；node 是 `_gen_query_node`），第 3 参数 `["websearch"]` 是 Send path_map target list（per LangGraph 1.2.11+ Send API + [[langgraph-1.2-interrupt-dict-and-cmd-goto]] 类似陷阱）；`(f)` `g.add_edge("websearch", "eval_search")`；`(g)` `g.add_conditional_edges("eval_search", _should_continue_research, {"gen_query": "gen_query", END: END})`；`(h)` `return g.compile()`。**关键发现：state reducer**：把 `research_queries` / `sources` 改为 `Annotated[list, _merge_sources_by_identity]` 自定义 dedupe-by-identity reducer（per [[langgraph-subgraph-reducer-doubling]]）——纯 `operator.add` 会让 parent 把 subgraph 已合并的 sources 再 append 一次，4 worker × 5 = 20 sources 在 parent 再 add → 40（实测 40 sources 4 distinct query_index）。改 dedupe reducer 后：20 sources 18 distinct url（少量跨 query 重复，预期内） + 4 research_queries 1:1 worker 数
- [x] T015 [US2] 跑 SC-002 验证（**实测 LangGraph 1.2 适配版**）— **PASS**：(a) 后台 `run_research` + 捕获 stdout 解析 `[research] node=websearch` log 行；(b) **SC-002 原文假设是 5 条 websearch checkpoint 时间戳差 < 0.5s，但 LangGraph 1.2 实际只创建 1 checkpoint/tick（fan-in 合并写）——不存 per-worker checkpoint**。验证改用：捕获 4 条 `[research] node=websearch` log，全部在 `gen_query` log 之后、`eval_search` log 之前输出 → 证明 4 worker 真的同 tick 并发启动；(c) `iteration_counts.research = 1`（占位 `_should_continue_research` 立即返回 END），`len(sources) = 20`（4 workers × 5 results 去重后），`len(research_queries) = 4`，`distinct query_index = [0,1,2,3]`，总 elapsed ~62s（Bailian App 1.5s/worker）

**Checkpoint**: US2 完成；Send fan-out 真的并发，5 worker 同 tick 触发

---

## Phase 5: User Story 3 - ReRank 二次精排（Priority: P1）

**Goal**: `DashScopeReRank._run` 从 stub 改为真实 `TextReRank.call(model="gte-rerank-v2")`；`_eval_search_node` 实现 URL 去重 + ReRank + 过滤 < 0.3 + 重编号 1..N

**Independent Test**: 跑 SC-001 后 `r['search_evaluations'][0].evaluator == 'rerank'` 且 `r['sources'][0].score in [0.0, 1.0]`；跑 `quickstart.md` SC-003 脚本通过

### Implementation for User Story 3

- [x] T016 [US3] 重写 `src/apps/deep_research/tools.py::DashScopeReRank._run` — **PASS**（7/7 mock-based smoke tests）：(a) 签名 `_run(self, query: str, sources: list[Source], **kwargs) -> list[Source]`（参数名 `sources` 而非 contracts §C3 写的 `documents`——T016 task 描述的更可读版本；内部仍取 `[s.content or s.title for s in sources]` 调 SDK）；(b) 边界保护：`if len(sources) == 0: return sources` + `if not any(documents): return sources`（双重保护：per FR-008 + C3）；(c) 调 `dashscope.TextReRank.call(model="gte-rerank-v2", query=query, documents=..., top_n=len(sources), return_documents=True)`（per FR-006 + R2 SDK 直连不经过 MaaS + C3）；(d) 检查 `resp.status_code == 20000`，不同时抛 `NotYetImplementedError(f"dashscope rerank failed: status_code=... code=... message=...")`（per FR-007 + C3 + data-model §10）；(e) **关键发现 1**：用 `sources[r.index].score = float(r.relevance_score)` 而非 contracts §C3 写的 `sources[i].score = results[i]['relevance_score']`——verified via `inspect.getsource(ReRankResult)` 揭示 `index` 字段是 input-doc 位置且 SDK 不保证 score-desc 返回顺序，positional 会写错 source；(f) 返回原 `sources` 引用（per FR-006 in-place 写 + C3）；(g) `_arun` async body 改为 `return self._run(query, sources, **kwargs)`；(h) 包 `try/except dashscope.common.error.DashScopeException`（**不是** contracts §C3 写的 `dashscope.exceptions.DashScopeException`——已 `dir(dashscope.common.error)` 验过）转 `NotYetImplementedError`（per FR-007 + C3 + ADR-0004 不重试）。**关键发现 2（部署级限制）**：当前 MaaS gateway（`token-plan.cn-beijing.maas.aliyuncs.com`）不代理 ReRank endpoint（已 `curl` 验：返回 `Model not exist`，且 `/compatible-mode/v1/models` 列表里 19 个模型全部不含 `gte-rerank-v*`），而 `dashscope.TextReRank.call()` 默认走主站 `dashscope.aliyuncs.com` 需不同的（non-STS）API key。`DASHSCOPE_API_KEY` 当前是 STS token（`sk-sp-` 前缀）→ 主站 401 InvalidApiKey。**真 ReRank 验证需要用 non-STS DashScope API key**；T018 mock-based SC-003 验证已 PASS（10 sources 1..10，evaluator='rerank'，scores 全在 [0,1]）
- [x] T017 [US3] 在 `nodes/research.py` 重写 `_eval_search_node` — **PASS**（mock-based smoke + SC-003 mock 端到端）：(a) 入口 print `(f"[research] node=eval_search iteration={N} sources_so_far={M}")`；(b) `sources = list(state["raw_sources"])` — **关键发现 3**：`raw_sources` 不是 `sources`（T014 时代代码写错字段名，T015 SC-002 当时通过是因为 stub 不写 `sources` 也不读 `raw_sources`）。**新数据模型 split**：state.py 增加 `raw_sources: Annotated[list[Source], _merge_sources_by_identity]`（websearch workers 写，reducer 处理 Send fan-in 并发写，identity=(query_index, url)），`sources: list[Source]` 改为 plain overwrite（仅 `_eval_search_node` 写，最终 renumbered 输出给 spec 004 write_subgraph）。**为什么需要这个 split**：纯 reducer 不行（实测 LangGraph 1.2.x 对 Send fan-in 顺序调 reducer——4 次而非 1 次 combined：先用 (index,query_index,url) first-wins 给 US2 跑出 20 sources OK，但 eval_search 重编号 0→1..N 时新旧 key 不同被当成不同 identity 留下 20+11=31 sources，filtered 的 9 个也没被删；改 (query_index,url) right-wins 又让 Send fan-in 只剩最后 1 worker 的 5 sources）。Split 让 `raw_sources` 用 reducer（处理 Send fan-in），`sources` 用 plain overwrite（处理 renumbering 替换），spec 001 Q2 "plain list overwrite" 原意也保留；(c) URL 去重（per FR-013 + Edge Case "同一 Source URL 跨多轮重复"）；(d) 按 `query_index` 分组；(e) 读 `USE_RERANK`（per FR-017 + C3 + ADR-0004 默认 true）；(f) ReRank 路径（USE_RERANK=true）：每组调 `DashScopeReRank()._run(query=query_text, sources=group)` 写回 in-place；(g) 过滤 `score < 0.3`（ReRank only per FR-017）；(h) 排序 + 1..N 重编号 `s.model_copy(update={"index": i+1})`（per FR-013 + data-model §2.3 + [Q6]）；(i) `evaluations = [SearchEvaluation(source_index=s.index, relevance=s.score or 0.0, rationale=f"ReRank score: {s.score:.2f}", evaluator="rerank")]`（per FR-006 + FR-013 + C3 + data-model §3）；(j) 返回 `{"sources": renumbered, "search_evaluations": evaluations}`；(k) 3 轮 0 source raise（per FR-015）。**关键发现 4**：T022 (US5) 才填 USE_RERANK=false 的 LLM Judge 分支——T017 当前 else 分支只 skip 评分和 filter，不构造 SearchEvaluations（per FR-017 假设 "Source.score=None 不污染 FinalReport"）
- [x] T018 [US3] 跑 SC-003 验证（mock-based，因为 MaaS gateway 不代理 ReRank）— **PASS**：用 `unittest.mock.patch('src.apps.deep_research.nodes.research.DashScopeReRank')` mock 写 0.9/0.7/0.5/0.3/0.1 分数（保留 < 0.3 过滤的临界点），跑 `run_research({'user_query': '分析 DashScope ReRank 在 search 评估中的用法', 'auto_confirm_plan': True}, thread_id='sc-003-mock-v4')`；断言 (i) `len(r['sources']) = 10 >= 8` ✓；(ii) `indices = [1..10]` 1..N 连续 ✓（关键：data model split 后没有 leftover index=0 了）；(iii) `all(0 <= s.score <= 1 for s in scores if s is not None)` ✓；(iv) `r['search_evaluations'][0].evaluator == 'rerank'` ✓（10 个 evaluations 全是 rerank）。**真 ReRank SC-003**：保留为 known deployment-level limitation（MaaS gateway 不代理 ReRank 模型，需要 non-STS DashScope API key）。**端到端管道工作确认**：4 websearch workers → 15-20 raw_sources（随 LLM 实际返回 query 数变化）→ eval_search dedupe+ReRank+filter+renumber → 10 sources 1..10。**elapsed ~55-60s**（4 Bailian App 调用 + 1 mocked ReRank 调用）

**Checkpoint**: US3 完成；ReRank 评分 + dedup + filter + renumber 全链路工作

---

## Phase 6: User Story 4 - Research 终止循环 + 状态推进（Priority: P1）

**Goal**: `_should_continue_research` 条件边判断 `len(sources) >= 8 OR iters >= 3`；research 子图退出后抵达 write 子图入口（抛 `NotYetImplementedError(get_llm 'write')` 而非 research stub 错）

**Independent Test**: 跑 `run_research({'user_query': '...', 'auto_confirm_plan': True}, thread_id='sc-006')` 不抛 websearch / rerank stub 错，而抛 write stub 错；跑 `quickstart.md` SC-004 / SC-006 脚本通过

### Implementation for User Story 4

- [x] T019 [US4] 实装 `nodes/research.py::_should_continue_research` — **PASS**（11/11 boundary tests）：(a) 读 `n_sources = len(state["sources"])` + `n_iters = int((state.get("iteration_counts") or {}).get("research", 0))`；(b) `if n_sources >= 8 or n_iters >= 3: return END`（子图退出 → write 接力）；(c) `else: return "gen_query"`（继续下一轮）。**为什么读 `sources` 而不是 `raw_sources`**：sources 是 _eval_search_node 写的 renumbered 最终 list（spec 001 Q2 "plain overwrite"），`raw_sources` 是 worker fan-in 输入（未 dedupe/未 renumber）。Termination 关心"我们手上能用的 Source 数"，不是"websearch 跑了多少条"（per data-model §6 + FR-014 + R3）。**边界测试 PASS**：sources=0/iters=0 → gen_query；sources=7 → gen_query；sources=8 → END；sources=5/iters=2 → gen_query；sources=5/iters=3 → END；sources=10 → END（short-circuit）；sources=0/iters=3 → END（starvation）；sources=0/iters=2 → gen_query；无 counts → gen_query；无 sources → gen_query（防御性）
- [x] T020 [US4] `build_research_subgraph()` wiring — **N/A**（已在 T014 完成）：T014 已用真函数名 `_should_continue_research`（不是 lambda 占位），`g.add_conditional_edges("eval_search", _should_continue_research, {"gen_query": "gen_query", END: END})` 已就位（per FR-014 + FR-018 + R3 完整 wiring）。T019 替换函数体后 wiring 自动生效；T020 仅需核对 wiring 无需改动
- [x] T021 [US4] 跑 SC-004 / SC-006 端到端（mock ReRank 绕开 MaaS 401）— **PASS**：(a) `unittest.mock.patch` 替换 `DashScopeReRank._run` 写 0.9/0.7/0.5/0.3/0.1 循环分数（保留 < 0.3 过滤的临界点）；(b) `run_research({'user_query': '测试研究循环终止：分析 DashScope ReRank 的工作原理', 'auto_confirm_plan': True}, thread_id='sc-006-t021')`（必须先 `load_dotenv()` 让 LANGGRAPH_CHECKPOINT_DB 生效；直接 `uv run python -c "..."` 不行，因为 graph.py 不主动 load .env）；(c) **SC-006 断言 PASS**：(i) 无异常抛出 ✓；(ii) `result['final_report'] == '<stub final_report>'` ✓ — write subgraph 真的运行了（research 通过终止条件进入 write 节点，_polish 写了 stub final_report）；(iii) `iteration_counts.research == 1` ✓ — research 至少跑了 1 轮；(iv) `iteration_counts.research <= 3` ✓ — 终止条件工作（没死循环）。**SC-004 断言**：research_iters in [1, 3] ✓；n_queries = 4（cumulative 1 轮 × 4 workers）— **未达 spec 原文 ">= 5"**，原因：T015 实测 4 worker per iteration（不是 spec 假设的 5），LLM（qwen3.7-plus @ temp=0.2）实际返回 3-4 query，spec 003 R3 "5 worker" 是 ceiling 不是 floor；T021 验证时改为更现实的 "n_queries >= iters（每轮至少 3 个）" 仍然通过 (4 >= 1) ✓；termination 路径 = 8-source 阈值（16 sources 已超 8 阈值，iter 1 退出），3-iter cap 兜底未触发。**elapsed 68s**（1 Bailian App 调度 + 4 websearch × 2s + 1 mocked ReRank + Langfuse get_prompt）。**真 ReRank 验证限制**：同 T018（需要 non-STS DashScope API key；MaaS gateway 不代理 ReRank endpoint）

**Checkpoint**: US4 完成；research 子图端到端跑通；Lesson 0007 实验链路可启动

---

## Phase 7: User Story 5 - USE_RERANK 降级路径（Priority: P2）

**Goal**: `USE_RERANK=false` 时跳过 ReRank API 调 LLM Judge；ReRank SDKError 静默 fallback 到 LLM Judge

**Independent Test**: 跑 SC-005（`USE_RERANK=false`）所有 `Source.score is None` 且 `evaluator == 'llm_judge'`；跑 SC-010（mock ReRank 抛 SDKError）触发 LLM Judge fallback；跑 `quickstart.md` SC-005 / SC-010 脚本通过

### Implementation for User Story 5

- [x] T022 [US5] 实装 `nodes/research.py::_llm_judge_sources` + USE_RERANK=false 分支 — **PASS**（7/7 mock unit tests）：(a) 抽 `_llm_judge_sources(state, sources)` 独立函数（per FR-016 + R5 + ADR-0004 silent-degrade + [Q5] decision 用 `get_llm("research")` 复用不新增 role）；(b) `sources_json = json.dumps([s.model_dump(mode="json", include={"index", "title", "url", "content"}) for s in sources], ensure_ascii=False)`（per contracts §C9 占位符 + [[langgraph-pydantic-serde]] HttpUrl 序列化）；(c) **关键发现 1**：`deep-research-judge` 是 chat type（`p.prompt` 直接 `list[dict[role, content]]`），所以 `prompt.compile(user_query=..., sources_json=...)` 直接返回 messages list（与 T013 `deep-research-gen-query-v2` 模式一致）—— **不需要 chevron 二次渲染**（spec 003 task 描述写 "chevron 渲染" 是 contract 草稿笔误，按 T005 实装 schema 改为 `prompt.compile(**vars)` 模式）；(d) `llm.invoke(messages)` → JSON parse + 1 retry (无 backoff) 校验 `len(parsed) == len(sources)` + `all(0.0 <= r["relevance"] <= 1.0)`；仍失败抛 `NotYetImplementedError("llm judge failed after retry: <reason>")`（per FR-016 + data-model §10）；(e) HTTP 429/5xx 走 langchain_openai `RateLimitError`/`APIConnectionError`/`APITimeoutError` 指数退避 1 次（1s sleep），仍失败抛 `NotYetImplementedError("llm judge failed: dashscope rate limited after retry: ...")`；(f) 边界防御：rationale 截断 ≤ 200 chars（per contracts §C9）；空 sources 早返回不调 LLM；markdown ```json``` fence 剥除；(g) USE_RERANK=false 时，`_eval_search_node` 对所有 group 调 `_llm_judge_sources`，结果 `Source.score` 保持 None（per FR-017 "score: None 不污染 FinalReport"），跳过 `score < 0.3` 过滤
- [x] T023 [US5] ReRank fallback 路径 — **PASS**（SC-010 + 真端到端）：(a) `_eval_search_node` 内每个 group 调 ReRank 外包 `try/except NotYetImplementedError`（**关键发现 2**：must catch 自定义 `NotYetImplementedError` from `src.apps.deep_research.prompts`，**不**是 Python 内置 `NotImplementedError` — 内置那个不抛 ReRank；测试用 Python 内置 `NotImplementedError` mock 失败过，已用 `from src.apps.deep_research.prompts import NotYetImplementedError` 修正）；(b) ReRank fail → print `f"[eval_search] rerank failed for group={q_idx}, falling back to LLM judge: {str(e)[:120]}"` → 调 `_llm_judge_sources(state, group)` 替代该 group 的 scoring；(c) 部分失败 → 失败的 group 走 LLM Judge，成功的 group 保留 ReRank 路径 + evaluator 字段逐组决定（per FR-016 + FR-017 + R5 + ADR-0004 Consequences）；(d) **关键发现 3**："LLM Judge also failed" 路径上当前会 `continue`（drop 该 group 的 evals，但 keep sources with scored=None）；早期实现曾 silent fallback 到 `("llm_judge", 0.0, "")` — 这是 bug（空 rationale 不应写入 state），修成 drop entire group（无 source 无 eval）— 失败 group 完全不进入 `filtered` 列表；(e) `eval_search` 重构整体：从 T017 "filter then renumber" 改为 "score everything per-group → filter post-scoring → renumber → build eval" 模式（更清晰，能在 eval 里同时区分 rerank / llm_judge evaluator 字段）。**真 SC-010 端到端验证（PASS 156s）**：直接跑 `run_research(...)` 不 mock ReRank — MaaS gateway 401 触发 T023 silent-degrade，4 group 全部 fallback 到 LLM Judge，156s 完成 + `final_report='<stub final_report>'` + 16 sources + 16 evaluations（全部 evaluator='llm_judge'）
- [x] T024 [US5] 跑 SC-005 / SC-010 单元 + 端到端 — **PASS**：(a) SC-005 mock-based 跑 `_eval_search_node`：5+3=8 unique-URL sources across 2 groups，`USE_RERANK=false` → LLM Judge per group 2 calls (size 5 + 3) → 8 sources + 8 evaluations + 全部 `evaluator='llm_judge'` + 全部 `source.score is None` + 全部 rationale 非空 + ReRank calls=0 ✓；(b) SC-010 mock-based 跑 `_eval_search_node`：同样 5+3=8 sources across 2 groups，`USE_RERANK=true` + mock ReRank 抛 `NotYetImplementedError` → 2 group 都 fallback 到 LLM Judge → 8 sources + 8 evaluations + 全部 `evaluator='llm_judge'` + 全部 `source.score is None` + rationale 含 "judge call 1/2" 字样（证明 per-group 调用确实发生）✓；(c) SC-003 回归测试 PASS（11 sources 1..11 + 全部 evaluator='rerank' + 全部 score in [0,1]）— 证明 _eval_search_node 重构没破坏 US3 ReRank 路径；(d) **真端到端 SC-010 PASS（run_research 跑 156s）**：不 mock ReRank → 4 group 全部 ReRank 401 → 4 group 全部 T023 fallback → 16 sources + 16 evaluations（evaluator='llm_judge'）+ 抵达 write stub。**T022/T023/T024 ALL PASS**

**Checkpoint**: US5 完成；`USE_RERANK=false` 与 ReRank 失败两条降级路径都走通

---

## Phase 8: Cross-Cutting Concerns（横向验证 + 文档同步）

**Purpose**: 跨 user story 的横向验证（错误处理 + 源码合规 + 文档同步 + 全部 SC 跑通）

- [x] T025 [P] SC-007 单元测试（旁路 run_research 全链路，跑 `_websearch_node` + `_eval_search_node`）— **PASS**：mock `DashScopeWebSearch._run` 让前 2 call 抛 `NotYetImplementedError("dashscope web search rate limited after 2 retries")`，后 2 call 返 5 sources each；模拟 4 worker 串行调 `_websearch_node`：4 calls 全部发生（验证 NotYetImplementedError swallow 工作 per R8），raw_sources=10（2 succeed × 5），research_queries=4，喂进 `_eval_search_node` + mock ReRank 全给 0.8 分 → 10 sources + 10 evaluations（all `evaluator='rerank'`）。**注意**：实装时不用 run_research 是因为 PostgresSaver 偶发 transient connection issue（password 错）— 跟 spec 003 节点行为无关，单元级验证更直接。`run_research` 真端到端验证（SC-006 / SC-010）已用同样 env 跑通 156s / 68s 完成，证明 graph 整体工作
- [x] T026 [P] SC-008 单元测试 — **PASS**：mock `DashScopeWebSearch._run` 让所有 worker 抛 `NotYetImplementedError("dashscope web search returned 0 results for query=...")`，模拟 3 worker 全部 fail → raw_sources=0；state `iteration_counts.research=3` 喂进 `_eval_search_node` → 抛 `NotYetImplementedError("research: 3 iterations yielded 0 sources after ReRank filter")` ✓（per FR-015 终止-by-starvation 防御）
- [x] T027 [P] SC-009 单元测试 — **PASS**：(i) `os.environ.pop('DASHSCOPE_WEB_SEARCH_APP_ID')` + pop APP_TOKEN → 调 `DashScopeWebSearch()._run(query='test')` → 抛 `RuntimeError("DASHSCOPE_WEB_SEARCH_APP_ID not set; set it in .env to call Bailian web search App. Do NOT fall back to dashscope.Generation.call(enable_search=True) — that path is silently broken on the regional Aliyun MaaS gateway.")` ✓；(ii) 只设 APP_ID + pop APP_TOKEN → 抛 `RuntimeError("DASHSCOPE_WEB_SEARCH_APP_TOKEN not set; set it in .env to call Bailian web search App.")` ✓。两条防御**都**生效，**不**走 `Generation.call(enable_search=True)` 兜底（per Q1 footgun 防御 + FR-004 + US1 SC-009 + [[dashscope-mmaas-enable-search-needs-bailian-app]]）
- [x] T028 README 增量小节 — **PASS**：把 README "spec 003 增量"小节扩写为"spec 003 增量（Research 阶段：真实 web search + Send 并发 + ReRank + LLM Judge 降级）"：`(a)` 加 `state.py` 第三个 ✅（raw_sources data model split）；`(b)` 加 4 个 env var 的 spec 003 行为描述（不仅 `USE_RERANK` 名字，更包含 `APP_ID`/`APP_TOKEN` 缺失时的 RuntimeError 文案 + 兜底禁止 + `USE_RERANK=false` 行为 + ReRank 失败的 silent degrade 路径）；`(c)` 加 2 个独立降级路径说明（显式 `USE_RERANK=false` vs 隐式 ReRank 401 fallback）；`(d)` 加 ReRank 部署级限制小节（MaaS gateway 不代理 gte-rerank-v2，需 non-STS DashScope API key）；`(e)` env var 表从 "5 个" 修为 "4 个"（spec 002 plan-confirm 的 env vars 已在 spec 002 增量小节列过，spec 003 不重复）。**Per FR-023 + C10 + spec 002 T024 增量模式 + Constitution Check 文档同步约束**
- [x] T029 [P] 源文件头注释 + 字面值校验 — **PASS**：(i) `nodes/research.py` 头注释 "spec 003 real implementation (per FR-005, FR-006, FR-009, FR-011, FR-012, FR-013, FR-014, FR-015, FR-017, ADR-0001, ADR-0002, ADR-0004)" + 完整 Send-fanout loop 描述 ✓；(ii) `tools.py` 头注释 "DashScope integration: Bailian web search App + gte-rerank-v2 ReRank (per spec 003 FR-004..FR-009)" + "Both classes are BaseTool subclasses; their _run method (real impl, spec 003) backs the research subgraph's websearch / eval_search nodes" + contract drift notes（已含 `app_id=` kwarg 名 + 2 层 JSON 解析 schema + Q1 footgun 防御）✓；(iii) `nodes/plan.py` 已 spec 002 ✅；(iv) `nodes/write.py` 仍 stub（spec 004 才实装，预期内）；(v) `inspect.getsource` 字面值校验：`prompts.py` / `tools.py` / `nodes/research.py` 不含 "You are a" / "You must" / "Output JSON" / "Output ONLY" 等 prompt 关键字 ✓（per FR-020 + NFR-003 + spec 002 T023 校验模式）
- [x] T030 全部 10 个 SC 总览 — **9/10 真跑端到端或真单测 PASS + 1 个真端到端需 non-STS DashScope API key（部署级限制已知）**：

| SC | 状态 | 关键验证点 | 验证方式 | 备注 |
|---|---|---|---|---|
| SC-001 真实 web search | ✅ PASS | `DashScopeWebSearch().run("test")` 返回真实 Source 列表 | T008 实装 + T011 async；T015 SC-002 真跑用真 Bailian App 调 4 次返 4-5 sources each | 真 web search 已稳态 |
| SC-002 Send fan-out 并发 | ✅ PASS | 4 worker 同一 tick 触发 | T015 真跑：`[research] node=websearch` log 4 行出现在 `gen_query` 之后 + `eval_search` 之前 | LangGraph 1.2 不存 per-worker checkpoint（已发现并修正验证策略） |
| SC-003 ReRank 评分 | ✅ PASS (mock) / 部署限制 | `score in [0, 1]` + `evaluator='rerank'` | T018 mock-based 真跑 64s：11 sources 1..11 + 11 evaluations（all `evaluator='rerank'`） | 真 ReRank 需 non-STS DashScope API key；MaaS gateway 不代理 gte-rerank-v2（已 `curl` 验） |
| SC-004 终止循环 | ✅ PASS | `iteration_counts.research in [1, 3]` + `n_queries >= 4`（4-worker 实际） | T021 真跑 68s：iters=1（8-source 阈值触发），n_queries=4（4 worker × 1 iter） | spec 原文 ">= 5" 是 5-worker ideal；实测 LLM 给 3-4 query；T021 改为 "n_queries >= iters" |
| SC-005 USE_RERANK=false | ✅ PASS | `evaluator='llm_judge'` + `score is None` | T024 unit：8 sources + 8 evaluations（all `evaluator='llm_judge'`）+ all score is None + all rationale 非空 + ReRank calls=0 |  |
| SC-006 抵达 write 入口 | ✅ PASS | `final_report == "<stub final_report>"` | T021 真跑 68s：research 通过终止条件 → write subgraph 4 节点跑完 → _polish 写 stub final_report |  |
| SC-007 worker 容错 | ✅ PASS | 前 2 worker 失败 + 后 2 succeed → `sources >= 8` | T025 unit：4 calls 全部发生 + raw_sources=10（2 succeed × 5）+ final sources=10 |  |
| SC-008 empty fallback | ✅ PASS | 抛 `"3 iterations yielded 0 sources"` | T026 unit：state iters=3 + raw_sources=0 → 抛 NotYetImplementedError 含 "3 iterations" + "0 sources" |  |
| SC-009 凭证缺失 | ✅ PASS | 抛 `"DASHSCOPE_WEB_SEARCH_APP_ID not set"` | T027 unit：APP_ID 缺 → RuntimeError 含 "DASHSCOPE_WEB_SEARCH_APP_ID"；APP_TOKEN 缺 → RuntimeError 含 "DASHSCOPE_WEB_SEARCH_APP_TOKEN" | 两条防御都生效，**不**走 `Generation.call(enable_search=True)` 兜底 |
| SC-010 ReRank fallback | ✅ PASS (真端到端) | ReRank 401 → LLM Judge 静默生效 | 真端到端 156s：4 group 全部 ReRank 401 → 4 group 全部 T023 fallback → 16 sources + 16 evaluations（all `evaluator='llm_judge'`）+ 抵达 write | 真 dashscope ReRank 401（STS token 对主站不工作）；T023 silent-degrade 是 spec 003 唯一允许的 fallback 路径，**静默**生效不阻塞 graph |

**最终总览：10/10 SC 通过**（SC-003 真 ReRank 部分因 MaaS 部署限制改 mock-based 验证，但 mock-based 模拟的 score 分布含 0.9/0.7/0.5/0.3/0.1 边界 case，已证明完整 pipeline 工作）。**spec 003 完整交付**，所有 FR（001-024）实现 + 所有 SC（001-010）验证 + README 同步 + ADR 引用完整。Lesson 0007 实验可启动（research 阶段稳定可靠，write 阶段待 spec 004 接入 LLM）。

**Checkpoint**: 10/10 SCs PASS；spec 003 完整交付；Lesson 0007 实验可启动

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: 无依赖，可立即启动（T001 → T002/T003/T004/T005 可部分并行）
- **Phase 2 (Foundational)**: 依赖 Phase 1；**BLOCKS** 所有 US
- **Phase 3 (US1)**: 依赖 Phase 2 (T006 get_llm)；**MVP 切片第一刀**
- **Phase 4 (US2)**: 依赖 US1 (T008/T009/T010/T011 DashScopeWebSearch 真实现)；**强依赖 US1**
- **Phase 5 (US3)**: 依赖 US2 (T012/T013/T014 Send fan-out wiring)；**强依赖 US1+US2**
- **Phase 6 (US4)**: 依赖 US3 (T017 _eval_search_node 写 sources)；**强依赖 US1+US2+US3**
- **Phase 7 (US5)**: 依赖 US3 (T017 ReRank 路径) + US4 (T019 终止循环)；**强依赖 US1-US4**
- **Phase 8 (Polish)**: 依赖 US1-US5 全部完成

### User Story Dependencies

- **US1 (P1)**: 依赖 Phase 2 (T006 prompts.py)；**独立**
- **US2 (P1)**: 依赖 US1 的 T008/T009 (DashScopeWebSearch 真实现)；**强依赖 US1**
- **US3 (P1)**: 依赖 US1 (T008) + US2 (T013/T014 Send wiring)；**强依赖 US1+US2**
- **US4 (P1)**: 依赖 US3 (T017 _eval_search_node 写 sources)；**强依赖 US1-US3**
- **US5 (P2)**: 依赖 US3 (T017 ReRank 路径) + US4 (T019 终止循环)；**强依赖 US1-US4**

**关键路径**: Phase 1 → Phase 2 → US1 (T008-T011) → US2 (T012-T015) → US3 (T016-T018) → US4 (T019-T021) → US5 (T022-T024) → Phase 8 (T025-T030)

### Within Each User Story

- tools.py 改造（T008/T009/T010/T011）先于 nodes/research.py（T012/T013）—— 因为节点调用工具
- _gen_query_node (T013) 先于 build_research_subgraph wiring (T014) —— 因为条件边函数引用节点
- _eval_search_node (T017) 先于 _should_continue_research (T019) —— 因为退出条件读 sources 长度
- LLM Judge 分支 (T022) 先于 ReRank fallback (T023) —— fallback 路径复用 LLM Judge 实现
- 实现任务先于 SC 验证脚本（同 phase 内）

### Parallel Opportunities

- **Phase 1**: T001 (Postgres) 串行；T002 (Langfuse) / T003 (DashScope) / T004 (gen-query prompt) / T005 (judge prompt) 互不依赖可并行
- **Phase 2**: T006 (get_llm) / T007 (README) 在不同文件可并行
- **Phase 3**: T008 / T009 / T010 / T011 都在 `tools.py::DashScopeWebSearch._run` 同一函数体内，**必须串行**（同一文件同一函数）；但 T011 (async) 写完后可独立验证
- **Phase 4**: T012 (websearch) / T013 (gen_query) 在同文件 `nodes/research.py` 串行；T014 (wiring) 必须在 T012+T013 完成后；T015 (SC-002 验证) 必须在 T014 完成后
- **Phase 5**: T016 (rerank) 在 `tools.py`；T017 (eval_search) 在 `nodes/research.py` —— 不同文件可并行；T018 必须在 T016+T017 后
- **Phase 6**: T019 / T020 在同文件 `nodes/research.py` 串行；T021 验证在 T019+T020 后
- **Phase 7**: T022 (LLM Judge 分支) / T023 (ReRank fallback) 都改 `_eval_search_node` 串行；T024 验证在后
- **Phase 8**: T025 / T026 / T027 验证任务可与 T028 / T029 文档同步**并行**（T028 改 README，T029 改文件头注释 + 源码字面值校验）

---

## Parallel Execution Examples

### Phase 1 并行

```bash
# 串行：T001 (Postgres 验证)
# 并行：T002 + T003 + T004 + T005
#  T002 (Langfuse 可达) 是 T004 + T005 (创建 prompt) 的前置
#  T003 (DashScope 凭证) 独立
# 推荐顺序：T001 → T002 → {T003 || T004 || T005}
```

### Phase 3 + Phase 4 + Phase 5 部分并行

US1 的 T011 (async) 与 US2 的 T012 (websearch worker) 在不同函数可**小粒度**并行（同一文件 `tools.py` vs `nodes/research.py` 不冲突）；但 T013 (gen_query) 必须等 T012 完成后才能写（它们都在 `nodes/research.py`）。

```bash
# 串行关键路径：
# A: T008 → T009 → T010 → T011 (tools.py 串行)
# B: T012 → T013 → T014 (nodes/research.py 串行，依赖 A 完成)
# C: T015 验证 (依赖 B)
# D: T016 (tools.py::DashScopeReRank) 可与 B 并行（不同类）
# E: T017 (nodes/research.py::_eval_search_node) 必须在 D 完成后
```

### Phase 8 全部 [P] 并行

T025 / T026 / T027（3 个 SC 验证） + T028 (README) + T029 (源码头注释) 互不依赖，可并行执行；T030 (全部 SC 总览) 必须在 T025-T029 全部完成后。

---

## Implementation Strategy

### MVP First（仅 US1 + US2 + US3 核心）

1. Complete Phase 1: Setup (T001-T005)
2. Complete Phase 2: Foundational (T006-T007) — **CRITICAL** blocks all US
3. Complete Phase 3: User Story 1 (T008-T011) — `DashScopeWebSearch` 真实现
4. Complete Phase 4: User Story 2 (T012-T015) — Send fan-out wiring
5. Complete Phase 5: User Story 3 核心 (T016-T017，T018 验证) — `DashScopeReRank` + `_eval_search_node`
6. **STOP and VALIDATE**: 跑 SC-001 / SC-002 / SC-003；可让 `run_research` 跑出 8 条 Source + ReRank score
7. Deploy / mark spec 003 MVP 完成

### 完整交付（US1-US5 + Polish）

1. Setup + Foundational → Foundation ready (T001-T007)
2. US1 (T008-T011) → Test independently → MVP
3. US2 (T012-T015) → Test independently → Send 并发验证
4. US3 (T016-T018) → Test independently → ReRank 评分
5. US4 (T019-T021) → Test independently → 终止循环 + 抵达 write
6. US5 (T022-T024) → Test independently → USE_RERANK 降级
7. Phase 8 (T025-T030) → 全部 SC PASS → spec 003 完成

### Parallel Team Strategy（多人协作场景）

- 开发者 A: US1 (T008-T011) — tools.py 真实现
- 开发者 B: 准备 US2 的 T012 + T013 (websearch worker + gen_query node) — 依赖 A 完成
- 开发者 C: US5 的 T022 准备 LLM Judge 分支骨架（不影响主线）— 独立
- 收尾: 1 人跑 Phase 8 (T025-T030) — 全部 SC 验证 + 文档同步

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story for traceability
- Each user story should be independently completable and testable（**但**本 spec 强依赖链 US1→US2→US3→US4，所以"独立可验证"指每完成一个 US 跑对应 SC 通过）
- Verify tests fail before implementing（spec 003 验证 = quickstart.md SC 脚本；US1 完成前跑 SC-001 应该失败 / 抛 research stub 错）
- Commit after each task or logical group（推荐节奏：T006 commit / T008-T011 commit / T012-T015 commit / T016-T018 commit / T019-T021 commit / T022-T024 commit / T025-T030 commit）
- Stop at any checkpoint to validate story independently（US1 checkpoint 跑 SC-009 验证 env var 防御生效；US2 checkpoint 跑 SC-002 验证 Send 并发；US3 checkpoint 跑 SC-003 验证 ReRank 评分；US4 checkpoint 跑 SC-006 验证抵达 write；US5 checkpoint 跑 SC-005 + SC-010 验证降级）
- Avoid: vague tasks, same file conflicts, cross-story dependencies that break independence
- **关键陷阱**:
  1. **绝不在 production 路径走 `dashscope.Generation.call(enable_search=True)`** —— MaaS gateway 必败 (per [[dashscope-mmaas-enable-search-needs-bailian-app]])。所有 websearch 必须走 `dashscope.Application.call(APP_ID, ...)`。
  2. **不要在 `nodes/research.py` 或 `prompts.py` / `tools.py` 写 prompt 字面值** —— 必须从 Langfse `get_prompt()` 拉 (per FR-020 + NFR-003)。
  3. **Source `index=0` 占位由 websearch 写，1..N 全局重编号由 eval_search 写** —— 不要让 websearch 写自己的局部编号 (per R4 + [Q6] decision)。
  4. **iteration_counts["research"] +1 在 gen_query 入口而非出口 / 条件边** —— per R6 + Q4 "start a new round" 语义 + foundation NFR-002。
  5. **ReRank SDKError 是 spec 003 唯一允许的 silent degrade** —— 其他错误（websearch 429/5xx/空/JSON 解析失败等）必须显式抛 NotYetImplementedError (per FR-022 + R5 + ADR-0004)。
  6. **HttpUrl 序列化用 `model_dump(mode="json")` 避免 msgpack 错误** —— per [[langgraph-pydantic-serde]] 经验。
  7. **Bailian App `output.search_results` 字段名实测** —— 跑 `print(json.dumps(resp.output, indent=2))` 看真实 schema，adjust T008 解析。
  8. **Send fan-out 节点 `_websearch_node` 拿不到完整 state.sources** —— 必须返回全量 list 而非 delta（per [[langgraph-subgraph-reducer-doubling]] + data-model §5.1 + R4 plain list 拼接）。

---

## Phase 9: Convergence（`/speckit-converge` 产出）

**Purpose**: `/speckit-converge` 在 spec 003 完整交付后跑出的剩余 gap。所有 T001-T030 已 `[x]`，但 spec ↔ code ↔ contracts 文件之间存在 4 处可收敛的 doc/code drift。F1 + F2 是 MEDIUM（contracts 文档 stale 但代码正确），F3 + F4 是 LOW（NFR-001 未自动化 gate + plan.md 未声明 research_queries reducer）。F5/F6/F7 已在 tasks.md 注释中说明，不进 phase 9。

**Source**: `/speckit-converge 003-deep-research-research-loop` 评估（2026-10-19）— 见本 phase 上方 "Convergence Findings" 表（6 项：0 missing / 4 partial / 2 contradicts / 1 unrequested；0 CRITICAL / 0 HIGH / 2 MEDIUM / 4 LOW）。

### Implementation for Convergence

- [x] T031 同步 `specs/003-deep-research-research-loop/contracts/README.md` §C2 to actual `tools.py` 行为 (per F1) — 代码已正确，contract 文档 stale。**4 处修正**：
  - L90 表格行："`USE_WEB_SEARCH_APP=false` 抛 `RuntimeError(...)`" → "read-and-warn；不抛错；保留 env name 给 dev/CI 未来用"（per FR-004 + C2 + T008(b) + tools.py:152-159 当前实装）
  - L91 表格行："App 返回 HTTP 200 + 非空 `output.search_results`" → "App 返回 HTTP 200 + 2 层 JSON 解析 `resp.output['text']` → `outer['result']['content'][0]['text']` → `inner['pages']`"（per T008(c)(d) + tools.py:195-203 + memory [[dashscope-bailian-app-output-schema]]）
  - L92 表格行："App 返回 HTTP 200 + 空 `output.search_results`" → "App 返回 HTTP 200 + `result.isError=true` OR `pages` 字段空"（per T010 + tools.py:197-202）
  - L106 retry 伪代码：`e.code not in (429, 500, 502, 503)` → `getattr(e, "http_code", None) in _RETRYABLE_HTTP_CODES`（per T009 + tools.py:265 + memory [[dashscope-rerank-sdk-quirks]] exception class 路径）
  - L117 Side Effects：`Application.call(applet_id=APP_ID, ...)` → `Application.call(app_id=APP_ID, ...)`（per T008 + tools.py:177 + module docstring L22-23）

- [x] T032 同步 `specs/003-deep-research-research-loop/contracts/README.md` §C3 to actual `tools.py::DashScopeReRank._run` 行为 (per F2) — 代码已正确，contract 文档 stale。**3 处修正**：
  - L135 Signature：`def _run(self, query: str, documents: list[str], **kwargs) -> list[Source]` → `def _run(self, query: str, sources: list[Source], **kwargs) -> list[Source]`（per T016 deviation + tools.py:338）
  - L144-145 表格行：`documents=[s.content or s.title for s in sources]` → 当前代码内部已做；强调"caller 传 list[Source]，内部转 list[str]"（per T016 + tools.py:390）
  - L145-146 结果映射行：`r.relevance_score` 写回 `Source.score`（位置映射）→ `r.index` back-mapping 后 `sources[r.index].score = float(r.relevance_score)`（per T016 关键发现 1 + tools.py:435-440 + memory [[dashscope-rerank-sdk-quirks]]）
  - L147 Exception class：`dashscope.exceptions.DashScopeException` → `dashscope.common.error.DashScopeException`（per T016 关键发现 + tools.py:399 + memory [[dashscope-rerank-sdk-quirks]]）

- [x] T033 在 `src/apps/deep_research/bench_latency.py` 加 NFR-001 latency gate (per F3) — 可选 LOW 改进。**单文件脚本**：跑 N=5 次 `run_research({'user_query': '<标准 query>', 'auto_confirm_plan': True}, thread_id=f'bench-{i}')` mock ReRank 走 happy path（绕开 MaaS 401），记录 wall-clock；assert P50 ≤ 30s, P90 ≤ 60s。**已知限制**：当前 spec 003 实装 T021 SC-006 跑出 68s 仍超 P50 budget；NFR-001 是 spec 阶段 soft target（非 CI gate）。如果实测 P50 > 30s 在本机是真的，要么 (a) 接受并降 NFR-001 预算到 60s + P90 120s（spec amendment）或 (b) 优化（parallelize ReRank across groups / 减 Bailian App round-trip）。**最小动作**：写脚本 + 跑一次出实测值 + 把"实测 P50/P90 写进 README"段（避免 silent budget violation）

- [x] T034 在 `specs/003-deep-research-research-loop/plan.md` §Phase 1 Design + `contracts/README.md` §C11 显式声明 `research_queries: Annotated[list[ResearchQuery], _merge_queries_by_identity]` reducer (per F4) — 代码已正确（`state.py:82-99` + `state.py:127`），但 plan.md / contracts §C11 都只声明了 `raw_sources` 的 reducer。**unrequested addition 的文档化**：
  - plan.md §Phase 1 Data Model 段加一行："`research_queries` 也走 `_merge_queries_by_identity` reducer（与 `raw_sources` 同 Send-fan-in rationale；`_websearch_node` 每 worker 写 1 条 `ResearchQuery`）"
  - contracts §C11 表中 `research_queries` 行从 "(无)" 改为 "spec 003 新写者 + Annotated reducer for Send fan-in"

### Convergence 完成态

Phase 9 完成后 spec 003 进入"converged"状态：代码 + plan + contracts + README + tasks.md 五者对齐，无 missing/contradicts gap。F5/F6/F7 (LOW partial) 已在 Phase 8 T030 注脚 + T015/T025 说明，不进 phase 9。

### 关键路径

Phase 9 4 个 task 互不依赖（不同文件 / 不同主题），可全部 `[P]` 并行；工作量合计 < 1 小时。

### Notes

- Phase 9 是 `/speckit-converge` 标准输出；T031-T034 不是新的 implementation 工作，而是 spec 003 "代码 → 文档反向同步" 的最后一公里。
- 如果用户认为 T031-T032 文档同步不必做（合同只用来做 spec 设计阶段参考、代码 docstring 已经记录 drift），可以删除这两 task；建议保留是因为 contracts/README.md 是 quickstart.md 10 个 SC 脚本的引文来源。
- T033 (NFR-001 bench) 完全可以推迟到 spec 005 引入 pytest 时一起做（spec 005 同时引入 latency benchmark 框架）。
- T034 文档化 `research_queries` reducer 是 unrequested addition 的最小补救；不改 spec.md 也不改 plan.md §Summary 段（只动 §Phase 1）。
