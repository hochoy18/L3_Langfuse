"""Streamlit UI entry for deep research agent (per ADR-0006 + spec 005).

Single-page Streamlit app:
- sidebar: thread_id display + Load thread / Reset thread buttons (per FR-003)
- main area: research question input + start button (per FR-004)
- plan review: __interrupt__ payload → Approve/Submit Edit buttons (per FR-006/FR-007)
- final report: st.markdown + st.download_button (per FR-008)
- error branch: warning / error mapping (per FR-009)
- progress: single st.spinner + completion marker (per FR-010 + Q4 澄清 2026-10-19)

Public API:
- `format_plan_md(plan_steps)` — plan list → Markdown renderer (per C2 contract)
- `_run_research_with_stages(input_payload, thread_id)` — invoke wrapper (per C1 contract)
- `_render()` — top-level render function (per FR-020 lifecycle; called once at import time
  so tests can also invoke it with a stubbed st module to exercise branches)

The top-level execution model is preserved by calling `_render()` immediately after
the function definition — Streamlit re-executes the whole script on every rerun, so
this is equivalent to running the body inline.
"""
from __future__ import annotations

import uuid
from typing import Any

import streamlit as st
from langgraph.types import Command

from src.apps.deep_research.graph import run_research
from src.apps.deep_research.schemas import PlanStep


# ---------------------------------------------------------------------------
# Helper: format plan list → Markdown (per FR-006 + C2 contract)
# ---------------------------------------------------------------------------
def format_plan_md(plan_steps: list[PlanStep]) -> str:
    """Render a list of PlanStep into a Markdown block (1-based).

    Output shape (per C2 contract):
        # 拟议研究计划

        1. **{title_1}**
           {description_1}

        2. **{title_2}**
           {description_2}

        ...

    - `description is None` → description 行省略
    - 不做 Pydantic 校验失败处理（假定 plan_steps 来自 LangGraph interrupt payload）
    """
    lines: list[str] = ["# 拟议研究计划", ""]
    for i, step in enumerate(plan_steps, start=1):
        title = getattr(step, "title", None) or "(no title)"
        description = getattr(step, "description", None)
        lines.append(f"{i}. **{title}**")
        if description:
            lines.append(f"   {description}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Wrapper: invoke + spinner + completion marker (per FR-010 + R3 + C1 contract)
# ---------------------------------------------------------------------------
def _run_research_with_stages(
    input_payload: dict | Command | None,
    thread_id: str,
) -> dict[str, Any]:
    """Run `run_research` under a single spinner + completion marker.

    Per FR-010 + R3 (Q4 澄清 2026-10-19) — single st.spinner wraps the entire
    invoke (不切 Plan/Research/Write 阶段，因为阻塞 invoke 模型无真实信号触发
    阶段切换；真实阶段切换留给 spec 006 Langfuse hooks).

    - `input_payload is None` → first invoke: build from `st.session_state.user_query`
    - `input_payload is not None` → resume: pass dict/Command directly
    - On non-interrupt / non-error → single-shot `st.markdown("✅ 完成")` after spinner exits
    - On `__interrupt__` → spinner exits naturally; plan review handled by caller
    - On error → spinner exits naturally; error handled by caller
    """
    if input_payload is None:
        input_payload = {"user_query": st.session_state.user_query}

    with st.spinner("研究进行中..."):
        result = run_research(input_payload, thread_id=thread_id)

    # Single-shot completion marker — only on success (no interrupt / no error)
    if (
        isinstance(result, dict)
        and not result.get("__interrupt__")
        and not result.get("error")
    ):
        st.markdown("✅ 完成")
    return result


# ---------------------------------------------------------------------------
# Top-level render (per FR-020 + ADR-0006 single-page pattern)
# ---------------------------------------------------------------------------
def _render() -> None:
    """Run the entire Streamlit UI render in one call.

    Per ADR-0006: a single function so we can drive it from tests with a
    stubbed streamlit module, AND call it inline at import time so Streamlit's
    rerun-everything semantics still work.
    """
    # ---- Session state init (per FR-002) ----------------------------------
    if "thread_id" not in st.session_state:
        st.session_state.thread_id = str(uuid.uuid4())

    # ---- Sidebar (per FR-003 — US2 stories included) ----------------------
    with st.sidebar:
        st.markdown("### 当前会话")
        st.text_input(
            "thread_id",
            value=st.session_state.thread_id,
            disabled=True,
            key="_display_thread_id",
            help="当前 LangGraph thread_id（Postgres checkpoint key）",
        )

        st.markdown("---")
        st.markdown("### 跨 session 续跑")

        load_thread_input = st.text_input(
            "Load thread_id (paste from another session)",
            key="load_thread_input",
            help="粘贴其他 tab / session 的 thread_id 接管历史 state（per US2）",
        )
        if st.button("Load thread", key="load_thread_btn"):
            if load_thread_input.strip():
                st.session_state.thread_id = load_thread_input.strip()
                # Reset transient state on Load
                st.session_state.last_action = None
                st.session_state.user_input = None
                st.session_state.user_query = ""
                st.rerun()
            else:
                st.warning("请粘贴有效的 thread_id")

        if st.button("Reset thread (新 uuid)", key="reset_thread_btn"):
            st.session_state.thread_id = str(uuid.uuid4())
            st.session_state.last_action = None
            st.session_state.user_input = None
            st.session_state.user_query = ""
            st.rerun()

    # ---- Rerun dispatch on last_action (per FR-005) ------------------------
    last_action: str | None = st.session_state.get("last_action")
    result: dict[str, Any] | None = None

    if last_action == "start":
        user_query = st.session_state.get("user_query", "").strip()
        if not user_query:
            st.warning("请输入研究问题")
        else:
            result = _run_research_with_stages(None, st.session_state.thread_id)
        st.session_state.last_action = None
    elif last_action in ("approve", "edit"):
        user_input = st.session_state.get("user_input", "")
        result = _run_research_with_stages(
            Command(resume=user_input), st.session_state.thread_id
        )
        st.session_state.last_action = None
    else:
        # First visit (last_action is None) — render home below; no invoke
        pass

    # ---- Main area: title + query input + start button (per FR-004) -------
    st.title("🔬 Deep Research Agent")
    st.caption(
        "基于 LangGraph 1.2+ 的 deep research agent · "
        "plan 审阅 + 研究循环 + 写作循环 + 跨 tab 续跑（per spec 001-005）"
    )

    st.text_input(
        "研究问题",
        key="user_query",
        placeholder="例如：为什么 LangGraph 1.2 用 interrupt() + Command(resume)?",
        help="你的研究问题；agent 会先生成 plan 让你审阅，再跑 research + write 循环。",
    )
    if st.button("开始研究", key="start_btn"):
        if not st.session_state.user_query.strip():
            st.warning("请输入研究问题")
        else:
            st.session_state.last_action = "start"
            st.rerun()

    # ---- Result rendering: __interrupt__ / error / final_report (per FR-006/008/009)
    if result is not None:
        # ---- Error branch (per FR-009) — checked first to skip final_report render
        err = result.get("error")
        if err == "no_pending_interrupt":
            st.warning("⚠️ 该 thread 无 pending interrupt，可能已完成或不存在")
        elif err == "thread_id_busy":
            st.warning("⚠️ Thread 正在被另一个 session 占用，请稍后重试")
        elif err:
            st.error(f"❌ {err}")

        # ---- Interrupt branch (per FR-006)
        elif result.get("__interrupt__"):
            interrupt_payload = result["__interrupt__"][0]
            # LangGraph 1.2.x returns either a tuple-like object with .value
            # or a dict; handle both (per project memory [[langgraph-1.2-interrupt-dict-and-cmd-goto]])
            if hasattr(interrupt_payload, "value"):
                payload = interrupt_payload.value
            elif isinstance(interrupt_payload, dict):
                payload = interrupt_payload
            else:
                payload = {}
            plan = payload.get("plan", []) if isinstance(payload, dict) else []

            st.markdown("## Plan 审阅")
            st.caption("agent 已生成研究计划，请审阅后选择 Approve 或 Edit")
            st.markdown(format_plan_md(plan))

            plan_feedback = st.text_area(
                "反馈（留空 = approve；非空 = edit 重生成）",
                key="plan_feedback",
                help="整段反馈文本模式：留空表示 approve；非空触发 plan_node 重生成（per Q1 决策）。",
            )

            col1, col2 = st.columns(2)
            with col1:
                if st.button("Approve", key="approve_btn"):
                    st.session_state.last_action = "approve"
                    st.session_state.user_input = "approve"
                    st.rerun()
            with col2:
                if st.button("Submit Edit", key="edit_btn"):
                    feedback = plan_feedback.strip()
                    if not feedback:
                        st.warning("请输入反馈内容（非空 = edit 重生成）")
                    else:
                        st.session_state.last_action = "edit"
                        st.session_state.user_input = f"edit: {feedback}"
                        st.rerun()

        # ---- Success branch (per FR-008)
        elif result.get("final_report"):
            st.success("✅ 研究完成")
            st.markdown(result["final_report"])
            st.download_button(
                "下载 Markdown",
                data=result["final_report"],
                file_name=f"{st.session_state.thread_id}.md",
                mime="text/markdown",
                help="下载 final_report 到本地（含 ## References 章节 + [ref_N] 引用）。",
            )


# ---------------------------------------------------------------------------
# Inline kickoff — Streamlit re-executes the whole script on rerun, so we
# invoke `_render()` here to preserve the original top-level body semantics.
# ---------------------------------------------------------------------------
_render()