# Implementation Plan: Plan 与 Confirm 阶段（真实 LLM + interrupt）

**Branch**: `[002-deep-research-plan-confirm]` | **Date**: 2026-10-18 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/002-deep-research-plan-confirm/spec.md`

---

## Summary

spec 002 在 foundation spec（spec 001）之上，把 `nodes/plan.py` 的两个 stub 节点（plan_node / confirm_plan_node）替换为真实实现：

1. **plan_node** 调 DashScope `qwen-plus-2025-04-28` LLM，从 Langfuse Prompt Management 拉 `deep-research-plan` prompt（label=production），产出 `list[PlanStep]` 写入 `state["plan"]`；带指数退避 retry + JSON 重试。
2. **confirm_plan_node** 用 LangGraph 1.x `interrupt({"plan": ...})` 暂停图，支持 `"approve"` 通过 + `"edit: <text>"` 回 plan_node 重生成；`auto_confirm_plan=True` 旁路。
3. **`run_research`** 新增 Postgres advisory lock 互斥（同 thread_id 并发返回 `thread_id_busy`）；保留 foundation 的 `no_pending_interrupt` 错误。

技术栈：LangGraph 1.2+ + Langfuse 3.x + DashScope OpenAI-compatible + Postgres 16 + pydantic 2.x。

---

## Technical Context

| 维度 | 值 |
|---|---|
| **Language/Version** | Python 3.11+（pyproject.toml `requires-python=">=3.11"`） |
| **Primary Dependencies** | langgraph>=1.2.11, langfuse>=3.0, langchain>=1.4.0, langchain-openai>=1.6.2, pydantic>=2.7, psycopg[binary]>=3.3.5 |
| **Storage** | Postgres 16（来自 `langfuse-deploy` 的 docker-compose，本地 `localhost:5432`） |
| **Testing** | `uv run python -c "..."` 内联 smoke test；future pytest 留给 spec 003/004 引入 |
| **Target Platform** | macOS / Linux 开发机；CI（future GitHub Actions） |
| **Project Type** | library（包 `src.apps.deep_research`） + 后续 Streamlit UI（spec 005） |
| **Performance Goals** | plan_node LLM 调用 P50 ≤ 8s（per NFR-001）；advisory lock 等待 ≤ 1s（per FR-017） |
| **Constraints** | DashScope free tier 60 RPM；spec 002 不引入新 env var（FR-016） |
| **Scale/Scope** | 单 User 单 thread；同 thread_id 并发互斥（FR-017）；不涉及多租户 |

### 关键决策（与 spec 差异已标注）

| 项 | spec.md 原文 | 实际（research.md 校正） |
|---|---|---|
| Langfuse SDK 版本 | "Langfuse 2.x client" | `langfuse>=3.0`（pyproject.toml 锁定）；API 兼容 |
| model name | 未指定 | `qwen-plus-2025-04-28` |
| interrupt payload | `{"plan": <list of PlanStep>}` | `{"plan": [s.model_dump(mode="json") for s in state["plan"]]}`（防 msgpack 失败） |

详见 [research.md §R1 §R3 §R6](./research.md)。

---

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

项目 `.specify/memory/constitution.md` 是模板骨架（无具体原则）。spec 002 自我约束：

| 原则 | 状态 | 说明 |
|---|---|---|
| spec 001 不可修改（purely additive on spec 002） | ✅ PASS | spec 002 不改 schemas.py / state.py / graph.py 的 public API |
| Langfuse prompt 字面值不在 Python 源码（NFR-003） | ✅ PASS | plan_node 全部 prompt 从 Langfuse 拉 |
| 跨进程状态载体只有 Postgres（FR-013） | ✅ PASS | advisory lock 用 Postgres 原语，不引入 Redis |
| 不引入新环境变量（FR-016） | ✅ PASS | 沿用 foundation 的 `LANGGRAPH_CHECKPOINT_DB` + Langfuse/DashScope 标准 env |

无 Constitution violation。

---

## Project Structure

### Documentation (this feature)

```text
specs/002-deep-research-plan-confirm/
├── plan.md              # 本文件
├── research.md          # Phase 0 产出（10 个研究主题）
├── data-model.md        # Phase 1 产出（实体 + state transitions + interrupt schema）
├── quickstart.md        # Phase 1 产出（9 个 SC 验证脚本）
├── contracts/           # Phase 1 产出（Python API contracts）
│   └── README.md
├── checklists/
│   └── requirements.md  # spec 002 质量清单
└── spec.md              # spec 002 本身（含 8 个 Clarifications）
```

### Source Code（spec 002 触及）

```text
src/apps/deep_research/
├── __init__.py
├── graph.py              # ← 修改：run_research 加 advisory lock
├── prompts.py            # ← 重写：get_prompt / get_llm 真实接入 Langfuse + DashScope
├── nodes/
│   ├── __init__.py
│   └── plan.py           # ← 重写：plan_node 真接 LLM；confirm_plan_node 加 edit 分支
└── (其他文件不动：schemas.py / state.py / pydantic_serde.py / tools.py / nodes/research.py / nodes/write.py)
```

**Structure Decision**：Option 1（Single project / library），符合 foundation spec 既有布局；spec 002 是 purely additive，**不动**其他模块。

---

## Complexity Tracking

> Fill ONLY if Constitution Check has violations that must be justified

无 violation，section 为空。

---

## Phase 0 · Research

10 个研究主题，详见 [research.md](./research.md)：

| # | 主题 | 关键决策 |
|---|---|---|
| R1 | Langfuse 3.x SDK API | `langfuse>=3.0`；text 类型 prompt + 客户端拼 chat messages |
| R2 | LangGraph 1.x interrupt + Command(resume) | `interrupt({"plan": [...]})` + `Command(goto="plan", update=...)` |
| R3 | DashScope qwen-plus | `qwen-plus-2025-04-28` + `temperature=0.4` |
| R4 | Postgres advisory lock | `pg_advisory_xact_lock(hashtext(:tid))` + 1s timeout |
| R5 | Retry 模式（手写 vs tenacity） | 手写指数退避；不引入 tenacity |
| R6 | Interrupt payload 序列化 | `[s.model_dump(mode="json") for s in state["plan"]]` |
| R7 | 跨进程 resume 验证 | 两个独立 shell 跑 `python -c "..."` |
| R8 | Langfuse prompt 模板（手动创建） | Mustache 语法 + `{{#if user_feedback}}`；README 同步 |
| R9 | Spec 接口边界 | plan_node 只写 `plan` + `messages` |
| R10 | SC-003 宽容判定 | Postgres checkpoint 有 `node="research"` 即 PASS |

**所有 NEEDS CLARIFICATION 已解决**：spec 002 的 Clarifications Q1-Q8 已经由 spec / clarify 阶段落地；research.md 仅纠正 spec 中的 Langfuse 2.x 笔误。

---

## Phase 1 · Design & Contracts

### Data Model（[data-model.md](./data-model.md)）

- 实体继承 spec 001 schemas.py，spec 002 **不改 schema**
- State 字段细化：spec 002 写入 `plan` / `messages`；不动 `iteration_counts`
- Interrupt payload schema：`{"plan": list[dict]}`（每个 dict 是 `PlanStep.model_dump(mode="json")` 输出）
- Resume payload schema：`str`（"approve" / "edit: ..." / 其他 = approve）
- 错误 schema：3 类结构化错误（`no_pending_interrupt` / `thread_id_busy` / `NotYetImplementedError`）

### Contracts（[contracts/README.md](./contracts/README.md)）

7 个 Python API 契约：

| C# | 函数 | 角色 |
|---|---|---|
| C1 | `prompts.get_prompt(name, label)` | Langfuse SDK bridge |
| C2 | `prompts.get_llm(role)` | DashScope LLM factory（仅 role="plan" 实装） |
| C3 | `nodes.plan.plan_node(state)` | plan 生成 |
| C4 | `nodes.plan.confirm_plan_node(state)` | interrupt + edit 分支 |
| C5 | `graph.run_research(input, *, thread_id, config)` | entry point（spec 002 加 advisory lock） |
| C6 | Langfuse prompt template `deep-research-plan` | Mustache 模板契约 |
| C7 | State schema 兼容性 | spec 002 不改 `DeepResearchState` 字段 |

### Quickstart（[quickstart.md](./quickstart.md)）

9 个 Success Criteria 的可执行验证脚本（SC-001 ~ SC-009）。

---

## Post-Design Constitution Check

| 原则 | 状态 |
|---|---|
| spec 001 不可修改（purely additive） | ✅ PASS — data-model.md §9 显式声明 |
| Langfuse prompt 不在 Python 源码 | ✅ PASS — research.md R8 + contracts C6 把 prompt 推到 Langfuse |
| Postgres 是唯一状态载体 | ✅ PASS — research.md R4 + R7 |
| 不引入新环境变量 | ✅ PASS — research.md R3 / R4 / C5 |
| spec 002 不跨 spec 边界 | ✅ PASS — data-model.md §10 显式列出 spec 003/004 的字段 |

无新增 violation。

---

## 实现路径概要（tasks 阶段细化）

```text
1. 改 src/apps/deep_research/prompts.py
   - get_prompt: 接 langfuse.get_client().get_prompt(name, label)
   - get_llm: 实装 role="plan"（ChatOpenAI + DashScope）
   - 保留 NotYetImplementedError 用于其他 role

2. 改 src/apps/deep_research/nodes/plan.py
   - plan_node: 真实 LLM 调用 + retry + JSON 解析 + Pydantic validation
   - confirm_plan_node: 加 "edit:" 分支（HumanMessage + Command(goto="plan"))
   - extract_user_feedback helper: 从 state["messages"] 提取

3. 改 src/apps/deep_research/graph.py
   - run_research: 加 advisory lock 入口（pg_advisory_xact_lock + 1s timeout）
   - run_research: 加 thread_id_busy 错误返回

4. README 同步
   - "前置 1 步：在 Langfuse UI 创建 deep-research-plan prompt"

5. 跑 quickstart.md 的 9 个 SC 脚本
```

预计 2-3 小时工作量（不含 Langfse UI 手动创建 prompt 的等待时间）。
