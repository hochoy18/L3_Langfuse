# Implementation Plan: Write 阶段（真实 outline / draft / critic / polish + emit_final_report）

**Branch**: `[004-deep-research-write-loop]` | **Date**: 2026-10-19 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/004-deep-research-write-loop/spec.md`

---

## Summary

spec 004 在 foundation（spec 001）+ plan-confirm（spec 002）+ research-loop（spec 003）之上，把 write 子图的 stub 实现替换为真实端到端写作循环，并在外层图加 `emit_final_report` 节点（第 5 个顶层节点，per [Q6] 决策）：

1. **`nodes/write.py` 完整重写**：移除 stub；新增 4 个真实节点 `_outline_node`（拉 `deep-research-outline` prompt + `get_llm("write")`）/ `_draft_node`（拉 `deep-research-draft` + `get_llm("draft")`，temperature=0.7，产出含 `{{cite_N}}` 占位符的 Markdown）/ `_critic_review_node`（拉 `deep-research-critic` + `get_llm("critic")`，temperature=0.2，产 Critique JSON + 入口 `+1 iteration_counts["write"]`，per [Q5]）/ `_polish_node`（拉 `deep-research-polish` + `get_llm("polish")`，temperature=0.5，覆盖写 `state["draft"]`）。
2. **`nodes/write.py::build_write_subgraph()`** 重新组织节点连接：`(START) → outline → draft → critic_review → should_continue_writing` 条件边 `→ (polish | END)`；polish 回边 `→ critic_review` 形成循环。
3. **`graph.py` 新增 `_emit_final_report_node`** 作为**外层图第 5 节点**（per [Q6] 决策）：`(a)` `re.sub` 把 draft 的 `{{cite_N}}` 替换为 `[ref_N]`（per [Q3]）；`(b)` 拼接 `## References` 章节（按 Source.index 升序）；`(c)` 写 `state["final_report"]`。`graph.add_edge("write", "emit_final_report")` + `graph.add_edge("emit_final_report", END)`。
4. **`prompts.get_llm`** 实装 4 个新 role：`write`（0.4）/ `draft`（0.7）/ `critic`（0.2）/ `polish`（0.5）——兑现 spec 002 已预留的 5 元 role + 新增 `draft`（per [Q2]）。
5. **`schemas.Critique`** MINOR 升级：新增 `suggestions: list[str]` 字段（per [Q4] + FR-014），向后兼容（旧实例只有 score+comments，polish 节点 fallback 到 `comments` 派生）。
6. **新增 4 个 Langfuse chat template prompt**（label="production"）：`deep-research-outline` / `deep-research-draft` / `deep-research-critic` / `deep-research-polish`（per FR-010）——用户手动创建，spec 004 不写创建脚本。

技术栈：LangGraph 1.2.11+（条件边 + 子图 + 外层图扩展第 5 节点）+ Langfuse 3.x（chevron 渲染 4 个新 prompt）+ DashScope SDK 1.14+（通过 langchain-openai ChatOpenAI 间接使用，与 spec 003 一致）+ Postgres 16（PostgresSaver 继承 spec 001/002/003）+ pydantic 2.x。

---

## Technical Context

| 维度 | 值 |
|---|---|
| **Language/Version** | Python 3.11+（pyproject.toml `requires-python=">=3.11"`） |
| **Primary Dependencies** | langgraph>=1.2.11, langfuse>=3.0, langchain-openai>=1.6.2, pydantic>=2.7, dashscope>=1.14.0（spec 003 已加）, psycopg[binary]>=3.3.5, chevron>=0.14.0 |
| **Storage** | Postgres 16（来自 `langfuse-deploy`，本地 `localhost:5432`） |
| **Testing** | `uv run python -c "..."` 内联 smoke + `psql` Postgres 端验证；future pytest 留给 spec 005 |
| **Target Platform** | macOS / Linux 开发机；CI（future GitHub Actions） |
| **Project Type** | library（包 `src.apps.deep_research`） + Streamlit UI（spec 005） |
| **Performance Goals** | write 子图 P50 wall-clock ≤ 60s（含 6 次 LLM 调用：outline + draft + critic + polish × 平均 2 轮），P90 ≤ 120s（per NFR-001） |
| **Constraints** | DashScope free tier 60 RPM（write 阶段 4 个 LLM 共用）；同 spec 003 配额限制 |
| **Scale/Scope** | 单 User 单 thread；同 thread_id advisory lock（spec 002 FR-017 继承）；不涉及多租户 |

### 关键决策（与 spec 差异已标注）

| 项 | spec.md 原文 | 实际（research.md 校正） | 来源 |
|---|---|---|---|
| LLM role 拆分 | spec 002 已预留 5 元（plan/research/write/critic/polish） | 实装 4 个（write/draft/critic/polish）+ 新增 `draft` role | Q2 / R2 |
| `{{cite_N}}` 占位符 | draft 节点用占位符，emit 替换为 `[ref_N]` | `re.sub(r'\{\{cite_(\d+)\}\}', lambda m: f'[ref_{m.group(1)}]', draft)` | Q3 / R3 |
| Critique schema 升级 | `score + comments + suggestions` | spec 001 升级加 `suggestions: list[str]`（MINOR 升级，向后兼容） | Q4 / R4 |
| counter +1 时机 | spec 003 `gen_query` 入口 +1（research） | spec 004 `critic_review` 入口 +1（write，对称） | Q5 / R5 |
| emit 节点位置 | write 子图内末尾 | **外层图第 5 节点**（ADR-0001 future-trigger 兑现） | Q6 / R6 |
| Source.index 重编号 | spec 003 已做 1..N 全局重排 | 直接复用 spec 003 结果；duplicate 防御 enumerate 兜底 | Q7 / R7 |
| `polish` 节点异常哲学 | spec 描述"尽力改进" | 允许 LLM 输出空字符串 / 大幅缩减 draft，**不抛错**（per FR-017 例外） | R8 |

详见 [research.md](./research.md) R1-R10 + [data-model.md](./data-model.md) §6 状态转换。

---

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

`.specify/memory/constitution.md` v1.0.0 已签署。spec 004 自检：

| 原则 / 约束 | 状态 | 说明 |
|---|---|---|
| **PRINCIPLE I · Module-First & Spec Compatibility** | ✅ PASS | spec 004 不改 `state.py` 字段；改 `schemas.Critique` 加 `suggestions` 是 MINOR 升级（向后兼容）；外层图加 1 个节点 per ADR-0001 future-trigger；purely additive |
| **PRINCIPLE II · CLI-Grade Smoke Tests** | ✅ PASS | `quickstart.md` 10 个 SC 全部 `uv run python -c "..."` 单行命令 + Postgres checkpoint 端验证 |
| **PRINCIPLE III · Test-First & Coverage Gate ≥80%** | ✅ PASS（暂以 quickstart.md SC 充约） | spec 004 阶段 pytest 未集成；quickstart.md SC-001 ~ SC-010 作为 contract test；spec 005 接 pytest 时同步补 unit/edge/error 三类覆盖 |
| **PRINCIPLE IV · Integration Testing at Module Boundaries** | ✅ PASS | SC-002 验证 outline-plan alignment 边界；SC-006 验证 emit_final_report 边界；SC-010 验证 Source.index duplicate 防御边界 |
| **PRINCIPLE V · Observability, Versioning, Simplicity** | ✅ PASS | `[write] node=...` print 日志占位（spec 006 完整接 Langfuse）；不预接 future spec 留口 |
| **Constraint #1 · State schema invariants** | ⚠️ **PARTIAL** —— 见 Complexity Tracking | `schemas.Critique` 加 `suggestions` 字段是 MINOR 升级（向后兼容）；`DeepResearchState` 不动 |
| **Constraint #2 · Prompt text never in source** | ✅ PASS | `outline` / `draft` / `critic` / `polish` 4 个 chat 类型 prompt 全部从 Langfuse 拉；`prompts.py` 与 `nodes/write.py` 不含 prompt 字面值 |
| **Constraint #3 · iteration_counts increment discipline** | ✅ PASS | `_critic_review_node` 入口 `+1`（per FR-003 + R5），条件边读"已 +1 的 counter"；与 spec 003 `_gen_query_node` 入口 +1 对称 |
| **Constraint #4 · Error propagation contract** | ✅ PASS | 所有已知错误包 `NotYetImplementedError`；polish 节点**例外**（允许空字符串 / 大幅缩减，per FR-017）；不引入 `try/except: pass`（除 polish 哲学例外） |
| **Constraint #5 · Postgres is the only state source** | ✅ PASS | write 子图状态由 LangGraph + PostgresSaver 持久化；advisory lock 沿用 spec 002 FR-017 |
| **Constraint #6 · Mustache rendered client-side via chevron** | ✅ PASS | 4 个新 prompt 走 chevron 客户端渲染（继承 spec 002 FR-001 模式） |
| **Constraint #7 · Env-var hygiene** | ✅ PASS | spec 004 不新增 env var（per FR-019）；`DASHSCOPE_API_KEY` / `DASHSCOPE_MODEL` / `DASHSCOPE_API_BASE_URL` 已存在 |
| **Constraint #8 · Library versions** | ✅ PASS | 不新增 Python 依赖（per FR-013）；`langchain-openai` 已锁定；prompt 渲染走 chevron 已锁定 |

无 Constitution 阻塞 violation；Constraint #1 的 PARTIAL 在 Complexity Tracking 说明。

---

## Project Structure

### Documentation (this feature)

```text
specs/004-deep-research-write-loop/
├── plan.md              # 本文件
├── research.md          # Phase 0 产出（10 个研究主题 + spec 修正表）
├── data-model.md        # Phase 1 产出（实体 + state transitions + 13 节细节）
├── quickstart.md        # Phase 1 产出（10 个 SC 验证脚本）
├── contracts/           # Phase 1 产出（Python API + Langfuse prompt contracts）
│   └── README.md
├── checklists/
│   └── requirements.md  # spec 004 质量清单（16 项 PASS）
└── spec.md              # spec 004 本身（含 7 个 Clarifications）
```

### Source Code（spec 004 触及）

```text
src/apps/deep_research/
├── __init__.py
├── graph.py             # ← 修改：新增 _emit_final_report_node + 外层图加第 5 节点
├── prompts.py           # ← 修改：get_llm 实装 role="write"/"draft"/"critic"/"polish"
├── schemas.py           # ← 修改：Critique 加 suggestions: list[str] 字段（MINOR 升级）
├── nodes/
│   ├── __init__.py
│   └── write.py         # ← 重写：移除 stub；新增 _outline_node / _draft_node /
│                          _critic_review_node / _polish_node / _should_continue_writing
│                          + build_write_subgraph()
└── (其他文件不动：tools.py / state.py / pydantic_serde.py / 
     nodes/plan.py / nodes/research.py —— 全是 spec 001/002/003 已有)
```

**Structure Decision**：Option 1（Single project / library），符合 foundation + spec 002/003 既有结构；spec 004 是 purely additive 在三层之上：
1. **受 layered on spec 001**：state / schemas / graph 拓扑（外层图加 1 节点符合 ADR-0001 future-trigger）
2. **受 layered on spec 002**：prompts.get_prompt / chevron 渲染 / Postgres advisory lock / `auto_confirm_plan`
3. **受 layered on spec 003**：research 子图完成态 + Source.index 1..N 重编号（直接复用，不重新编号）

---

## Complexity Tracking

> Fill ONLY if Constitution Check has violations that must be justified

### Constraint #1 (State schema invariants) 的 PARTIAL

**问题**：spec 004 修改 `schemas.Critique` 加 `suggestions: list[str]` 字段（per [Q4] 决策 + FR-014）。

**为什么需要**：spec 004 polish 节点需要读取 `critique.suggestions` 来决定改进方向；spec 001 原 schema 只有 `score + comments`，polish 只能从 `comments` 字符串里 parsed 改进点（不可靠、不结构化）。

**为什么不破坏 spec 001 兼容性**：
- `suggestions: list[str] = Field(default_factory=list)` —— 默认空 list，旧 Critique 实例（spec 002/003 持久化的 checkpoint 数据）反序列化时 `suggestions=[]`
- `polish_node` 读 `critique.suggestions` 为空时 fallback 到 `comments` 派生（LLM 解析 comments 字符串）——向后兼容
- `Critique` 是 Pydantic BaseModel，加字段是 MINOR 升级（v1.1.0），不破坏 foundation 兼容

**为什么不写到 state 新字段**：spec 004 仍把 Critique 存到 `state["critique_history"]`（已有字段），不引入新 state 字段。**Critique 实体本身的字段升级**与 Constraint #1 "state 字段不变" 不冲突——Constraint #1 管 state，不管 entity schema。

**为什么接受 PARTIAL**：与 spec 001 / spec 002 / spec 003 已建立的一致性模式一致——spec 003 修改 `state.py` 加 `raw_sources` reducer 也是 MINOR 升级。Critique schema 升级是同样性质。

**Action Item**：spec 005 接 pytest 时补 Critique 实体的 `model_validate(strict=True)` 测试（spec 004 quickstart.md SC-009 已覆盖 Langfuse prompt 缺失防御，schema 升级测试留给 spec 005 pytest 框架）。

---

## Phase 0 · Research

10 个研究主题，详见 [research.md](./research.md)：

| # | 主题 | 关键决策 |
|---|---|---|
| R1 | 4 个 LLM role 兑现与新增 | 兑现 `write/critic/polish` + 新增 `draft` role；temperature 4 档（0.4/0.7/0.2/0.5）|
| R2 | `{{cite_N}}` 占位符机制 | draft 用占位符；emit `re.sub` 替换；与 spec 003 Q6 Source.index 1..N 全局唯一对齐 |
| R3 | Critique schema 升级 | 加 `suggestions: list[str]`，默认空 list；向后兼容（旧实例 suggestions=[]，polish fallback 到 comments）|
| R4 | counter +1 对称性 | `_critic_review_node` 入口 +1；与 spec 003 `_gen_query_node` 入口 +1 对称 |
| R5 | emit_final_report 拓扑 | 外层图第 5 节点；write 子图保持纯循环；ADR-0001 future-trigger 兑现 |
| R6 | Source.index 防御 | 直接复用 spec 003 重排结果；duplicate 时 enumerate 重新编号兜底 |
| R7 | polish 哲学 | 尽力改进；允许空字符串 / 大幅缩减；不抛错（spec 唯一 silent degrade）|
| R8 | Langfuse prompt 占位符 | 4 个 chat 类型；chevron 客户端渲染；join 字符串传递 plan / sources |
| R9 | chevron 渲染一致性 | 4 个 prompt 都用 `{{var}}` 替换（plan_steps / sources_with_index / sources_summary 都是 join 字符串，非 list）|
| R10 | end-to-end 验证路径 | quickstart.md SC-001 ~ SC-010 全链路 + Postgres checkpoint 端验证 |

**所有 NEEDS CLARIFICATION 已解决**：spec 004 的 Clarifications Q1-Q7 已在 spec 阶段落地；research.md 仅补全"为什么这么决策"的研究证据。

---

## Phase 1 · Design & Contracts

### Data Model（[data-model.md](./data-model.md)）

- **实体继承** spec 001 schemas.py + spec 004 MINOR 升级：
  - `Critique` 加 `suggestions: list[str] = Field(default_factory=list)`（per [Q4] + R3）
  - 其他实体（Outline / Draft / PolishedDraft / FinalReport / Reference）不动
- **State 字段细化**：
  - `outline`：`outline_node` 一次性写入（str Markdown）
  - `draft`：`draft_node` 写入一次；`polish_node` **覆盖**写入（str Markdown，不是 list）
  - `critique_history`：`critic_review_node` 每轮追加 1 条（append-reducer 模式，per spec 001 FR-003）
  - `iteration_counts["write"]`：`critic_review_node` 入口 +1（per FR-003 + R5）
  - `final_report`：`emit_final_report_node` 一次性写入（str Markdown，含 `[ref_N]` + `## References` 章节）
  - **外层图第 5 节点** `emit_final_report` 不在 write 子图内（per R5 + ADR-0001）
- **Send 边界**：write 子图无 Send fan-out（与 research 子图不同，write 是串行循环，4 个节点逐一跑）
- **错误 schema**：4 类 `NotYetImplementedError` 子消息（critic JSON parse failed / critic score 越界 / outline 章节数不一致 / draft 占位符缺失）
- **与 spec 002/003 接口边界**：spec 002 写 `plan` / `messages` / `auto_confirm_plan`；spec 003 写 `sources` / `search_evaluations` / `research_queries` / `iteration_counts["research"]`；spec 004 写 `outline` / `draft` / `critique_history` / `iteration_counts["write"]` / `final_report` —— **互不重叠**

### Contracts（[contracts/README.md](./contracts/README.md)）

11 个 Python / Langfuse / env var 契约：

| C# | 主题 | 角色 |
|---|---|---|
| C1 | `prompts.get_llm` 4 个新 role | ChatOpenAI 实例，temperature 4 档 |
| C2 | `nodes.write._outline_node` | outline 节点 LLM 调用契约 |
| C3 | `nodes.write._draft_node` | draft 节点占位符契约 |
| C4 | `nodes.write._critic_review_node` | critic JSON schema + counter +1 |
| C5 | `nodes.write._polish_node` | polish 覆盖写覆盖契约 |
| C6 | `nodes.write._should_continue_writing` | 条件边判据 |
| C7 | `nodes.write.build_write_subgraph()` | 节点连接契约 |
| C8 | `graph._emit_final_report_node` | emit 节点 transformation 契约 |
| C9 | Langfuse prompt 4 个 | chat template 占位符表 |
| C10 | Env Var Schema | spec 004 不新增 env var |
| C11 | State Schema 兼容 | spec 004 不改 `DeepResearchState`，仅 Critique 加字段 |

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
| PRINCIPLE I · Module-First & Spec Compatibility | ✅ PASS —— data-model.md §11 显式声明；Critique 加 suggestions 是 MINOR 升级 |
| PRINCIPLE II · CLI-Grade Smoke Tests | ✅ PASS —— quickstart.md 10 个 SC |
| PRINCIPLE III · Test-First & Coverage ≥80% | ✅ PASS（quickstart.md SC 充约；pytest 留给 spec 005）|
| PRINCIPLE IV · Integration Testing at Module Boundaries | ✅ PASS —— SC-002 outline-plan 边界；SC-006 emit 边界；SC-010 Source.index duplicate 边界 |
| PRINCIPLE V · Observability, Versioning, Simplicity | ✅ PASS —— print 日志占位；不预接 future spec |
| Constraint #1 · State schema invariants | ⚠️ PARTIAL —— Complexity Tracking 说明（Critique 加 suggestions）|
| Constraint #2 · Prompt text never in source | ✅ PASS —— contracts C9 把 prompt 推到 Langfuse |
| Constraint #3 · iteration_counts increment discipline | ✅ PASS —— R5 + FR-003（critic_review 入口 +1）|
| Constraint #4 · Error propagation contract | ✅ PASS —— FR-017 + R8 polish 例外；其他 known error 包 NotYetImplementedError |
| Constraint #5 · Postgres is the only state source | ✅ PASS —— write 子图 + PostgresSaver + advisory lock |
| Constraint #6 · Mustache via chevron | ✅ PASS —— contracts C9 |
| Constraint #7 · Env-var hygiene | ✅ PASS —— 不新增 env var |
| Constraint #8 · Library versions | ✅ PASS —— 不新增依赖 |

无新增 violation；`Critique.suggestions` 的 PARTIAL 已在 Complexity Tracking 显式 justification。

---

## 实现路径概要（tasks 阶段细化）

```text
1. 改 src/apps/deep_research/schemas.py
   - Critique: 加 suggestions: list[str] = Field(default_factory=list)
   - 其他 entity 不动

2. 改 src/apps/deep_research/prompts.py
   - get_llm: 实装 role="write" (temperature=0.4)
   - get_llm: 实装 role="draft" (temperature=0.7)（spec 004 新增）
   - get_llm: 实装 role="critic" (temperature=0.2)
   - get_llm: 实装 role="polish" (temperature=0.5)

3. 改 src/apps/deep_research/nodes/write.py
   - 移除 stub _outline / _draft / _critic_review / _polish
   - 新增 _outline_node(state) -> dict:
     ├─ get_prompt("deep-research-outline")
     ├─ chevron 渲染 3 个占位符
     ├─ get_llm("write").invoke(...)
     ├─ retry 1 次 (章节数 != len(plan))
     └─ 返回 {"outline": markdown_str}
   - 新增 _draft_node(state) -> dict:
     ├─ get_prompt("deep-research-draft")
     ├─ chevron 渲染 2 个占位符 (outline + sources_with_index)
     ├─ get_llm("draft").invoke(...)
     ├─ retry 1 次 (placeholder count == 0)
     ├─ log placeholder_count
     └─ 返回 {"draft": markdown_str}
   - 新增 _critic_review_node(state) -> dict:
     ├─ 入口 iteration_counts["write"] += 1 (per R5)
     ├─ get_prompt("deep-research-critic")
     ├─ chevron 渲染 3 个占位符
     ├─ get_llm("critic").invoke(...)
     ├─ strip ```json fence
     ├─ parse Critique (含 suggestions 字段)
     ├─ retry 1 次 (ValidationError)
     └─ 返回 {"critique_history": [critique]} (append)
   - 新增 _polish_node(state) -> dict:
     ├─ get_prompt("deep-research-polish")
     ├─ chevron 渲染 4 个占位符
     ├─ get_llm("polish").invoke(...)
     ├─ log warning if new_draft << 0.5 * old_draft (不抛错)
     └─ 返回 {"draft": new_draft} (覆盖)
   - 新增 _should_continue_writing(state) -> str:
     ├─ read critique_history[-1].score + iteration_counts["write"]
     └─ return "polish" | "END"
   - 重写 build_write_subgraph():
     ├─ add_node("outline", _outline_node)
     ├─ add_node("draft", _draft_node)
     ├─ add_node("critic_review", _critic_review_node)
     ├─ add_node("polish", _polish_node)
     ├─ add_edge(START, "outline")
     ├─ add_edge("outline", "draft")
     ├─ add_edge("draft", "critic_review")
     ├─ add_conditional_edges("critic_review", _should_continue_writing,
                                {"polish": "polish", END: END})
     └─ add_edge("polish", "critic_review")

4. 改 src/apps/deep_research/graph.py
   - 新增 _emit_final_report_node(state) -> dict:
     ├─ read state["draft"] + state["sources"]
     ├─ detect Source.index duplicates → log + enumerate reindex
     ├─ re.sub {{cite_N}} → [ref_N]
     ├─ concat "## References\n\n" + sorted sources list
     └─ 返回 {"final_report": markdown_str}
   - graph.add_node("emit_final_report", _emit_final_report_node)
   - graph.add_edge("write", "emit_final_report")
   - graph.add_edge("emit_final_report", END)

5. README 同步 (spec 004 实装)
   - "前置 4 步: 在 Langfuse UI 创建 deep-research-outline + deep-research-draft + deep-research-critic + deep-research-polish prompts"
   - "新增 env var: 无（沿用 spec 002/003 配置）"

6. 跑 quickstart.md 的 10 个 SC 脚本
```

预计 3-4 小时工作量（不含 Langfuse UI 手动创建 4 个 prompt 的等待时间）。

---

## 已知风险与缓解

| 风险 | 影响 | 缓解 |
|---|---|---|
| Langfuse UI 上 4 个 prompt 占位符语义不一致（chevron 渲染时找不到变量） | 节点入口抛 NotImplementedError | tasks 阶段先在 Langfuse UI 创建 1 个 prompt（outline）跑通 SC-001，验证 chevron 渲染；剩余 3 个 prompt 复用模式 |
| `polish` LLM 输出空字符串（"尽力改进"哲学导致 draft 被擦除） | critic 第 2 轮 score=0 但仍 emit | FR-017 已声明允许 polish 输出空字符串；emit_final_report 仍 emit（"尽力而为"哲学一致）|
| `polish` LLM 大幅缩减 draft（new_draft < 0.5 * old_draft） | 章节内容缺失 | log 警告但**不抛错**；emit 仍 emit；critique_history 留下"polish 大幅删减"的审计痕迹 |
| draft LLM 直接写 `[ref_N]` 而非 `{{cite_N}}` | emit_final_report 不替换 `[ref_N]`（已是最终格式）| US2 SC-002 已声明：emit 把 `[ref_N]` 视为合法引用；不抛错 |
| Source.index 在 spec 003 已重排为 1..N，但 spec 004 端不校验唯一性 | emit 静默 fallback enumerate reindex | US5 SC-010 已覆盖该边界 |
| LangGraph 外层图加第 5 节点 vs write 子图退出子图行为 | END marker 边界与 outer graph edge 不一致 | tasks 阶段先 mock 跑一个简化版（只有 plan → write → emit_final_report → END）验证拓扑 |
| `Critique.suggestions` 字段升级与旧 Postgres checkpoint 数据兼容性 | 反序列化失败 | 默认 `default_factory=list`；旧数据反序列化 suggestions=[]；polish fallback 到 comments |

---

## 完成态对比

| 维度 | spec 003 完成态 | spec 004 完成态 |
|---|---|---|
| `run_research` 端到端 | plan + confirm → research 真实跑 → write stub 抛错 | plan + confirm → research 真实跑 → write 真实跑 → emit → END（agent 完整闭环）|
| `state["outline"]` | `<stub outline>` | 真实 LLM 生成 Markdown，h2 章节数 == len(plan) |
| `state["draft"]` | `<stub draft>` | 真实 LLM 填充，含 ≥ 5 处 `{{cite_N}}` 占位符 |
| `state["critique_history"]` | `[Critique(score=0.0, ...)]` (1 条 stub) | 1-2 条真实 Critique，含 score + comments + suggestions |
| `state["iteration_counts"]["write"]` | 0（stub 不动） | 1-2（critic_review 入口 +1）|
| `state["final_report"]` | `<stub final_report>` | 真实 emit_final_report 加工 Markdown，含 `[ref_N]` + `## References` |
| `get_llm("write"/"draft"/"critic"/"polish")` | NotYetImplementedError | ChatOpenAI 实例，temperature 0.4/0.7/0.2/0.5 |
| 外层图节点数 | 4（plan/confirm_plan/research/write）| 5（+ emit_final_report，per ADR-0001 future-trigger）|
| Lesson 0007 可启动 | research-only 实验 | 端到端实验（plan-confirm → research → write → emit → final_report 输出）|
| spec 005 Streamlit UI 接力 | ❌（无 final_report 可展示）| ✅（final_report Markdown 可视化）|
| spec 006 Langfuse hook 接力 | ❌ | ✅（plan/research/write/emit 4 阶段 span 树齐全）|