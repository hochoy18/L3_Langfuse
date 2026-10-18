# ADR-0003 · LangGraph `interrupt()` 实现 Plan Confirmation

| 字段 | 值 |
|---|---|
| Status | Accepted |
| Date | 2026-10-18 |
| Scope | `nodes/plan.py` + `graph.py` checkpointer 依赖 |

## Context

`plan` 节点产出 Plan 后，User 需要介入审阅 / 编辑 / 通过。如何在 LangGraph 里实现这个**人机交互**？

三种候选：
1. **Auto-confirm**：plan 生成后立即进入 research（无交互）
2. **`interrupt()` 暂停 + `Command(resume=...)`** 续跑（LangGraph 1.x 标准模式）
3. **Pre-graph CLI input**：图外 stdin 输入 plan 修改意见，作为图输入的一部分

## Decision

**采用方案 2**：`confirm_plan` 节点调 `interrupt({"plan": plan})` 暂停图，Streamlit UI 渲染 plan + 按钮，用户决策后用 `Command(resume=user_input)` 续跑。

关键依赖：
- **必须**有 checkpointer（我们用 PostgresSaver——见 ADR-0005），否则 `interrupt` 后状态丢失
- `Command(resume=...)` 是 LangGraph 1.x 的标准 resume 模式
- `confirm_plan` 节点对 `Command(resume)` 的 payload 做类型判断：`"approve"` 通过、`"edit: ..."` 替换 plan 后再通过

```python
# nodes/plan.py (示意)
def confirm_plan_node(state):
    user_decision = interrupt({"plan": state["plan"]})  # 暂停
    if user_decision.startswith("edit:"):
        # 把 plan 重新丢回 plan_node 生成新版本
        return Command(goto="plan", update={"plan_edit": user_decision})
    return {}  # 通过，进 research
```

## Alternatives Considered

### A. Auto-confirm

**否定原因**：deep research 的灵魂是用户审 plan。auto-confirm 把 agent 沦为"自动 LLM 流水线"，失去 iterative 价值。Lesson 0007 实验场景下才用 `auto_confirm_plan=True` 绕过。

### C. Pre-graph CLI input

```python
plan = llm.generate_plan(user_query)
plan = input(f"review plan: {plan}\npress enter to continue or type edits:")
result = graph.invoke({"plan": plan, ...})
```

**否定原因**：
- 计划阶段在 graph **外**，无法在 Langfuse 里看到完整 trace（包括 plan 生成 + 修改）
- 失去 Streamlit 图形界面（plan 用 markdown 渲染比 terminal 友好）
- 无法跨进程 resume（terminal 关了就断）

## Consequences

**正向**：
- 用户交互轨迹完整记录在 Langfuse（plan_node → interrupt → user input → continue）
- 未来可加 score 评估"plan 接受率"（用户直接通过 vs 修改后通过 vs 拒绝）
- Streamlit UI 自然集成（`st.stop()` + `st.button()` + `Command(resume=...)`）
- Lesson 0007 实验通过 `auto_confirm_plan=True` 旁路，保持代码统一

**负向**：
- 必须配 checkpointer（不能 MemorySaver，因为 interrupt 后 terminal 关了状态就丢）
- `interrupt()` API 在 LangGraph 1.x 才稳定，低版本需要 `__interrupt__` 模式兼容
- Streamlit session_state 需要存 `thread_id` + `user_input`，跨 rerun 一致

**未来触发条件**：如果 deep research agent 要在 headless 模式（无 UI）下跑，需要暴露 `auto_confirm_plan=True` env var（已预留）。