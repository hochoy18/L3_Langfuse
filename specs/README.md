# Spec Kit · Deep Research 拆分与提示词指南

> 本文档回答：**「deep research agent 已经沉淀了 CONTEXT + 6 篇 ADR，下一步该怎么用 /speckit-specify 拆分实现？」**
>
> 适用读者：写后续 spec 的人（通常是项目作者 cai_he 本人），或要给 deep research 加新 feature 的人。
> 内容包括：拆分策略、依赖图、5 个可直接复制的 prompt、写作约定。

---

## 1. 为什么需要拆分

`src/apps/deep_research/` 目录下有：
- `CONTEXT.md`（14 个领域实体、9 大分类词汇表）
- `docs/adr/0001` ~ `0006`（6 篇架构决策）
- `README.md`（文件布局 + 设计要点速览）

如果一次性写一个巨大的 spec 走 `/speckit-implement`，会出现：
- 任务列表过长，规划阶段无法聚焦
- 单个 spec fail 时整个流程回滚
- 后续维护者无法独立演进各模块

按 Spec Kit 最佳实践：**每个 spec 应该独立可测、可发布、可回滚**。

---

## 2. 拆分策略（6 个 spec）

| Spec | 短名 | 覆盖 Phase | 价值 | 依赖 |
|---|---|---|---|---|
| **001** | `deep-research-foundation` | Phase 0+1 | 地基：包骨架 + Schema + State + 空图 + stub 节点 | — |
| **002** | `deep-research-plan-confirm` | Phase 2 | plan 真生成 + interrupt() 暂停 | 001 |
| **003** | `deep-research-research-loop` | Phase 3 | DashScope search + Send 5 并发 + ReRank | 001, 002 |
| **004** | `deep-research-write-loop` | Phase 4 | outline → draft → critic → polish + emit_final_report | 001, 003 |
| **005** | `deep-research-streamlit-ui` | Phase 5 | UI + interrupt 接线 + CLI 烟雾 | 001, 002, 004 |
| **006** | `deep-research-observability` | Phase 6 | Langfuse hooks + TraceMetadata 复用 | 001-005 |

### 拆分原则

1. **P1 / MVP first**：001 让骨架能跑（即便所有节点是 stub）→ 验证整个 graph 拓扑、state reducer、PostgresSaver
2. **依赖单向**：002-006 全部依赖 001，但 002-006 之间只有少数互相依赖 → 任何 spec 都能并行规划
3. **每个 spec 独立可测**：
   - 001 验收 = "画得出 mermaid 图 + run_research() 能到 END"
   - 002 验收 = "interrupt 真的触发并可 resume"
   - 003 验收 = "research 跑到 write 入口时 sources >= 8"
   - 004 验收 = "final_report 是带 [ref_N] 的合法 Markdown"
   - 005 验收 = "浏览器跑通完整流程 + CLI smoke"
   - 006 验收 = "Langfuse UI 看到完整 span 树"

---

## 3. 依赖图

```
001 (foundation)
 │
 ├──> 002 (plan + confirm)
 │      │
 │      └──> 003 (research loop)
 │              │
 │              └──> 004 (write loop)
 │                      │
 │                      └──> 005 (Streamlit UI)
 │
 └──> 006 (observability，可任意时点接入，最早在 004 完成后）
```

---

## 4. 5 个可直接复制的 Prompt

每个 prompt 都遵循统一模板：**依赖锚点（@specs）+ 文档锚点（CONTEXT.md §X）+ ADR 编号 + Scope 列表 + 不做什么 + 验收锚点**。

---

### 4.1 Spec 002 · Plan 阶段

```
@specs/001-deep-research-foundation/spec.md
基于已完成的 spec 001（foundation：包骨架 + schemas + state + 空图 + stub 节点），
规划 spec 002：plan 阶段。

参考文档（已存在）：
- src/apps/deep_research/CONTEXT.md §2 Entities（UserQuery / Plan / PlanStep）
- src/apps/deep_research/CONTEXT.md §4 Operations（generatePlan / confirmPlan）
- src/apps/deep_research/docs/adr/0003-interrupt-for-plan-confirm.md
- src/apps/deep_research/docs/adr/0001-nested-subgraphs.md（plan/confirm_plan 是顶层 4 节点中的 2 个）

Scope：
1. 把 nodes/plan.py 的 stub plan_node 替换为真实 LLM 调用，从 Langfuse Prompt Management
   拉取 deep-research-plan prompt，产出结构化 Plan（list of PlanStep）
2. 把 stub confirm_plan_node 替换为 interrupt({"plan": state["plan"]}) 实现
3. 在 graph.py 暴露 auto_confirm_plan 旁路（Lesson 0007 实验用）
4. 验证 PostgresSaver 跨进程 resume：用同一 thread_id 关掉重连能续上
5. 验收：run_research()第一次 invoke 触发 __interrupt__，Command(resume="approve") 后
   能从 confirm_plan 继续跑到 research 入口

不要做：search / write / UI / Langfuse hooks（后续 spec）
```

---

### 4.2 Spec 003 · Research 循环

```
@specs/001-deep-research-foundation/spec.md @specs/002-deep-research-plan-confirm/spec.md
基于001 + 002，规划 spec 003：research 阶段。

参考文档：
- src/apps/deep_research/CONTEXT.md §2 Entities（ResearchQuery / Source / SearchEvaluation / ResearchIteration）
- src/apps/deep_research/CONTEXT.md §4 Operations（generateQueries / webSearch / evaluateSearch）
- src/apps/deep_research/CONTEXT.md §6 Policies（ResearchTerminationPolicy: len(sources) >= 8 OR research_iters >= 3）
- src/apps/deep_research/docs/adr/0001-nested-subgraphs.md（research 是嵌套子图）
- src/apps/deep_research/docs/adr/0002-send-fanout-five-queries.md（Send API fan-out 5 个 websearch）
- src/apps/deep_research/docs/adr/0004-dashscope-rerank.md（DashScope ReRank 二次精排）

Scope：
1. tools.DashScopeWebSearch 真实接入 dashscope.Generation.call(enable_search=True)，
   返回 list[Source]（含 index / title / url / content? / score?）
2. tools.DashScopeReRank 真实接入 dashscope.TextReRank.call(model="gte-rerank-v2")，
   把 score 写回每个 Source
3. nodes/research.py 真实现 build_research_subgraph：
   gen_query（产出 5 个 query）→ Send[websearch × 5]（LangGraph Send API fan-out）→
   eval_search（ReRank + LLM Judge）→ 条件边（should_continue_research）
4. 终止条件：ResearchTerminationPolicy（sources >= 8 OR iters >= 3）
5. 错误处理：websearch 空结果触发 gen_query 重试；DashScope 静默 rate limit skip → tool 内 raise
6. 环境变量：USE_RERANK=false 可降级到纯 LLM Judge

验收：run_research({'user_query': '...', 'auto_confirm_plan': True}) 跑到 write入口时，
sources >= 8 且 research_queries 至少 3 轮 * 5 条
```

---

### 4.3 Spec 004 · Write 循环

```
@specs/001-deep-research-foundation/spec.md @specs/003-deep-research-research-loop/spec.md
基于 001 + 003，规划 spec 004：write 阶段。

参考文档：
- src/apps/deep_research/CONTEXT.md §2 Entities（Outline / Draft / Critique / PolishedDraft / FinalReport / Reference）
- src/apps/deep_research/CONTEXT.md §4 Operations（generateOutline / generateDraft / reviewCritic / polishDraft / emitReport）
- src/apps/deep_research/CONTEXT.md §6 Policies（WriteTerminationPolicy: critic_score >= 0.8 OR write_iters >= 2 + CitationPolicy + ReferenceListPolicy）
- src/apps/deep_research/docs/adr/0001-nested-subgraphs.md（write 是嵌套子图）

Scope：
1. nodes/write.py 真实现 build_write_subgraph：
   outline（拉 deep-research-outline prompt）→ draft（拉 deep-research-draft prompt，
   从 sources 填充）→ critic_review（拉 deep-research-critic prompt 打 quality score）→
   条件边（should_continue_writing）→ polish（拉 deep-research-polish prompt）
   → 回 critic_review
2. 终止条件：WriteTerminationPolicy（critic_score >= 0.8 OR write_iters >= 2）
3. emit_final_report：把 final draft + 所有 sources 加工成 FinalReport，
   行内 [ref_N] 与 source.index 对齐（CitationPolicy），末尾 ## References 章节（ReferenceListPolicy）
4. graph.py 把 emit_final_report 接到 write 子图末尾，写 state["final_report"]

验收：run_research() 跑到 END，final_report 是合法 Markdown：
- 每个 h2 章节（与 PlanStep1:1）
- 行内 [ref_N] 至少 5 处
- 末尾 ## References 章节列出 title / url / score
-至少有 1 轮 critic → polish（critique_history.length >= 1）
```

---

### 4.4 Spec 005 · Streamlit UI

```
@specs/001-deep-research-foundation/spec.md @specs/002-deep-research-plan-confirm/spec.md @specs/004-deep-research-write-loop/spec.md
基于 001 + 002 + 004，规划 spec 005：Streamlit UI 入口。

参考文档：
- src/apps/deep_research/CONTEXT.md §1 Actors（User 是最终 UI 用户）
- src/apps/deep_research/docs/adr/0003-interrupt-for-plan-confirm.md（interrupt() 接线）
- src/apps/deep_research/docs/adr/0006-streamlit-ui-entry.md（Streamlit 单页 + session_state pattern）

Scope：
1. app.py 实现 Streamlit 单页 UI：
   - session_state 存 thread_id（首次访问时生成 uuid4）
   - 输入框收 user_query
   - "开始研究" 按钮触发 run_research()
   - 检测 result["__interrupt__"] → 渲染 plan markdown + Approve/Edit 按钮 + 编辑文本框
   -按钮点击后 session_state.user_input + st.rerun() → Command(resume=...) 续跑
   - run结束 → st.markdown(final_report)
2. progress 显示：research / write 阶段用 st.progress() 或 st.empty() placeholder   显示当前阶段和 iteration 数
3. cli.py 实现 CLI烟雾测试：python -m src.apps.deep_research.cli "..." 跑通
   auto_confirm_plan=True 的全流程，print final_report 到 stdout

验收：
- streamlit run app.py 浏览器开 http://localhost:8501
- 输入 query → 看 plan → 点 Approve → 看 research progress → 看 final report
- 关掉 terminal 后用同一 thread_id 重新 invoke 能续上
- CLI 跑通最终报告渲染无异常
```

---

### 4.5 Spec 006 · Langfuse 可观测性

```
@specs/001-deep-research-foundation/spec.md @specs/002-deep-research-plan-confirm/spec.md @specs/003-deep-research-research-loop/spec.md @specs/004-deep-research-write-loop/spec.md
基于 001-004，规划 spec 006：Langfuse 可观测性接入。

参考文档：
- src/apps/fuse/meta.py（已有 TraceMetadata 6 分类配置，可复用）
- src/apps/fuse/react_agent.py（已有 LangGraph + CallbackHandler 接入 pattern）
- src/apps/deep_research/CONTEXT.md §2 Entities（每个实体都是潜在 score target）
- 6 篇 ADR 的 "Consequences" 章节（每篇都提到 Langfuse hook 预留点）

Scope：
1. 每个节点函数加 @observe(name=..., as_type=...) 装饰：
   - plan / gen_query / outline / draft / critic_review / polish / eval_search → as_type="generation"
   - websearch → as_type="tool"
2. graph.py::run_research() 包 start_as_current_span("deep-research")
 + 子图入口包 start_as_current_span("research") / start_as_current_span("write")
3. meta.py：复用 src/apps/fuse/meta.py::TraceMetadata（不要重复造）
   新增 DeepResearchMeta 子类（agent_name="deep-research", agent_strategy="plan-research-write"）
4. Langfuse UI 验证：跑一次完整流程后看 trace 树，
   应该看到 deep-research → research → websearch × 5 / write → critic_review 层级
5. 验收：每个 LLM 调用 span 都有 token 用量；websearch span 都有 URL / latency
6. 不做：score 打分（留给 Lesson 0007 的 dataset experiment）
```

---

## 5. 如何使用这些 Prompt

```bash
# 在项目根目录
cd /Users/cai.he/Desktop/AI/202607_智泊/Learning/L3_Langfuse

# 跑 002
/speckit-specify 002-plan 阶段：  （← 后面紧跟 4.1 节 prompt 正文）
# 然后粘贴：
@specs/001-deep-research-foundation/spec.md
基于已完成的 spec 001...
（见 §4.1）
```

实际命令格式（提示词正文直接放命令行即可）：

```bash
/speckit-specify "@specs/001-deep-research-foundation/spec.md 基于已完成的 spec 001...规划 spec 002：plan 阶段。Scope：..."
```

---

## 6. Prompt 写作约定

为了让 `/speckit-specify` 能稳定产出高质量 spec，每条 prompt 必须包含以下 6 段：

| 段 | 作用 | 示例 |
|---|---|---|
| **依赖锚点** | 显式 `@specs/NNN-...` 引用前置 spec | `@specs/001-deep-research-foundation/spec.md` |
| **文档锚点** | 引用 CONTEXT.md §X + ADR 编号 | `CONTEXT.md §2 Entities` + `adr/0003-...` |
| **Scope 列表** | 编号列出"做什么" | `1. xxx, 2. yyy, 3. zzz` |
| **不做清单** | 明确 scope 外的事 | `不要做：search / write / UI / Langfuse hooks` |
| **环境变量** | 涉及 env 的 spec 显式提 | `USE_RERANK=false 降级路径` |
| **验收锚点** | 在 prompt 里就埋"验收：xxx"让 spec 抓到 SC | `验收：run_research() 跑到 END，final_report 是合法 Markdown` |

### 反模式（不要这样写）

| ❌ 写法 | ✅ 应该改为 |
|---|---|
| "实现 plan 节点" | "把 nodes/plan.py 的 stub plan_node 替换为真实 LLM 调用" |
| "用 LangGraph 的 interrupt" | "interrupt({"plan": state["plan"]}) 实现，参考 ADR-0003" |
| "加 Langfuse" | "在 graph.py::run_research() 包 start_as_current_span，参考 ADR-000X 的 Consequences" |
| "要测试通过" | "验收：run_research() 跑到 END，final_report 非空" |

### 锚点命名约定

- ADR 引用：`src/apps/deep_research/docs/adr/000N-short-name.md`（避免歧义）
- 章节引用：`CONTEXT.md §X YYY`（§2 Entities / §4 Operations / §6 Policies）
- 字段引用：`state["plan"]`、`sources[0]`（**用代码字段名，不要用业务名词**）

---

## 7. 实施顺序（推荐时间线）

```
Day 1 (4h):
  ├── 001 → plan → tasks → implement  (1.5h)
  ├── 002 → plan → tasks → implement  (1h)
  └── 003 → plan → tasks → implement  (1.5h, 卡 DashScope API 测试)

Day 2 (4h):
  ├── 004 → plan → tasks → implement  (1h)
  ├── 005 → plan → tasks → implement  (1h, 调 Streamlit interrupt)
  └── 006 → plan → tasks → implement  (1h, 接 Langfuse)

Day 3 (2h):
  └── smoke 全流程 + 写 learning-records
```

每个 spec 内部走：`/speckit-plan`（架构 / 数据模型 / 接口）→ `/speckit-tasks`（任务分解）→ `/speckit-implement`（写代码）。

---

## 8. 与现有文档体系的关系

```
specs/
├── README.md                                  ← 你在这里（拆分指南）
├── 001-deep-research-foundation/
│   ├── spec.md                                ← spec 001 本身
│   └── checklists/requirements.md             ← 001 验收清单
├── 002-...                    ← (待创建)
├── 003-...                    ← (待创建)
└── ...

src/apps/deep_research/
├── CONTEXT.md                                 ← 领域事实（不变，spec 引用）
├── docs/adr/0001-0006                         ← 设计决策（不变，spec 引用）
└── ...                                        ← 代码
```

**关键不变量**：
- CONTEXT.md 和 6 篇 ADR **永远只追加不修改**——新的决策开新 ADR
- 每个 spec 引用 ADR 但不重复其内容
- spec 完成后归档（`specs/NNN-...` 保留作为历史记录）

---

## 9. 未来扩展

- 新增 spec（如 `007-deep-research-multilang`）：照本文档 §6 约定写 prompt 即可
- 修改既有 spec：开新 spec 替代，旧 spec 标 Superseded
- 跨模块的 spec（如把 deep research 接入业务 agent）：新建 `specs/NNN-business-.../`，引用 deep research 的 spec + ADR