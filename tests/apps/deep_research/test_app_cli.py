"""Tests for spec 005 Streamlit UI + CLI smoke (per FR-021 + FR-022).

Coverage:
- TC-1 / TC-2 / TC-3: app.py::_run_research_with_stages wrapper branches (US3)
- TC-4 / TC-5 / TC-6 / TC-7 / TC-8: cli.py main() scenarios (US4)
- TC-9 ~ TC-22: app.py::_render + format_plan_md branches (US1 + US2 + US3)
- TC-23 ~ TC-26: cli.py edge branches (OperationalError / no_pending_interrupt / __main__ / --help)

Per FR-021: spec 005 introduces pytest (project first); tests are written FIRST
per Principle III. Per FR-022 + R1: AppTest too heavy for our unit needs; we use
targeted monkeypatch on `st.*` + `cli.*` calls. `app_module.st` and `cli_module`
attributes are patched via `unittest.mock.patch.object`.

Streamlit session_state caveat: `_run_research_with_stages` reads
`st.session_state.user_query` when `input_payload is None`. We patch
`st.session_state` with a plain dict-like object before invoking.
"""
from __future__ import annotations

import sys
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# IMPORTANT: Replace sys.modules["streamlit"] with a fake BEFORE importing app.
# app.py's session_state init at module load time accesses `st.session_state`;
# the real Streamlit module raises in test environment without ScriptRunCtx.
# We need a fake module that satisfies `import streamlit as st` and supports
# dict-like session_state + spinner / markdown / etc.
# ---------------------------------------------------------------------------


class _FakeSessionState(dict):
    """Mock st.session_state as a plain dict (supports __contains__ / __setitem__)."""

    def __getattr__(self, key):
        # Streamlit SessionState allows attribute access; mirror that
        try:
            return self[key]
        except KeyError:
            raise AttributeError(key)

    def __setattr__(self, key, val):
        self[key] = val


class _FakeStreamlitModule:
    """Stand-in for the streamlit module — supports all st.* calls app.py makes."""

    def __init__(self) -> None:
        self.session_state = _FakeSessionState()
        self._markdown_calls: list[str] = []
        self._warning_calls: list[str] = []
        self._error_calls: list[str] = []
        self._button_clicks: dict[str, bool] = {}
        self._button_labels: list[str] = []
        self._text_input_values: dict[str, str] = {}
        self._text_area_values: dict[str, str] = {}
        # Pre-set so the fixture's `hasattr` check never raises AttributeError
        # on the first call before any rerun() invocation.
        self._rerun_called: bool = False

    def _make_spinner(self):
        @contextmanager
        def _ctx():
            yield None

        return _ctx()

    def spinner(self, text="", **kwargs):
        return self._make_spinner()

    def markdown(self, body="", **kwargs):
        self._markdown_calls.append(body)

    def warning(self, body="", **kwargs):
        self._warning_calls.append(body)

    def error(self, body="", **kwargs):
        self._error_calls.append(body)

    def success(self, body="", **kwargs):
        pass

    def caption(self, body, **kwargs):
        pass

    def title(self, body="", **kwargs):
        # __getattr__ fallback returns MagicMock for unknown attrs, which
        # short-circuits hasattr() to True; use direct __dict__ check instead.
        if "_title_calls" not in self.__dict__:
            self.__dict__["_title_calls"] = []
        self.__dict__["_title_calls"].append(body)

    def text_input(self, label, value="", key=None, **kwargs):
        return self._text_input_values.get(key or label, value)

    def text_area(self, label, value="", key=None, **kwargs):
        return self._text_area_values.get(key or label, value)

    def button(self, label, key=None, **kwargs):
        self._button_labels.append(label)
        return self._button_clicks.get(key or label, False)

    def download_button(self, label, **kwargs):
        return False

    def rerun(self):
        self._rerun_called = True

    def columns(self, n, **kwargs):
        return [self, self]

    @property
    def sidebar(self):
        # Real Streamlit: `st.sidebar` returns a context manager (no call).
        # We return self (the module) and rely on `__enter__`/`__exit__` below.
        return self

    def __enter__(self):
        # Make `with st.sidebar:` work — `st.sidebar` returns self; enter it.
        return self

    def __exit__(self, *args):
        return False

    def __getattr__(self, name):
        # Fallback for any other st.* call — return a MagicMock that records calls
        return MagicMock(name=f"streamlit.{name}")


# Install fake streamlit BEFORE app.py is imported
_FAKE_ST = _FakeStreamlitModule()
sys.modules["streamlit"] = _FAKE_ST

# Now safe to import app.py
from src.apps.deep_research import app as app_module
from src.apps.deep_research import cli as cli_module
from src.apps.deep_research.schemas import PlanStep

# ===========================================================================
# Test fixtures
# ===========================================================================


@pytest.fixture
def fake_st():
    """Provide a fresh fake streamlit module per test (reset state)."""
    _FAKE_ST.session_state.clear()
    _FAKE_ST._markdown_calls.clear()
    _FAKE_ST._warning_calls.clear()
    _FAKE_ST._error_calls.clear()
    _FAKE_ST._button_clicks.clear()
    _FAKE_ST._button_labels.clear()
    _FAKE_ST._text_input_values.clear()
    _FAKE_ST._text_area_values.clear()
    # Reset _rerun_called via setattr so it works whether or not __init__
    # pre-set the flag (which __getattr__'s MagicMock fallback would shadow
    # via hasattr→True but block via delattr on a non-existent __dict__ key).
    _FAKE_ST._rerun_called = False
    return _FAKE_ST


def _setup_session_state(fake_st: _FakeStreamlitModule, user_query: str, thread_id: str):
    fake_st.session_state["user_query"] = user_query
    fake_st.session_state["thread_id"] = thread_id


def _completed_call_count(fake_st: _FakeStreamlitModule) -> int:
    """Count how many times `st.markdown("✅ 完成")` was called."""
    return sum(1 for c in fake_st._markdown_calls if "✅ 完成" in str(c))


def _make_interrupt_result(plan_steps: list[PlanStep]) -> dict:
    """Make a __interrupt__ payload result dict per LangGraph 1.2.x shape."""
    return {
        "__interrupt__": [
            {"value": {"plan": plan_steps}}
        ]
    }


# ===========================================================================
# Phase 5: US3 - app.py::_run_research_with_stages tests (TC-1 / TC-2 / TC-3)
# ===========================================================================


class TestRunResearchWithStages:
    """Tests for app.py::_run_research_with_stages (per FR-010 + C1 contract)."""

    def test_tc_1_interrupt_branch(self, fake_st):
        """TC-1: __interrupt__ branch returns the interrupt dict; no completion marker."""
        _setup_session_state(fake_st, user_query="test interrupt", thread_id="test-thread-1")
        plan_steps = [PlanStep(title="Step 1", description="desc 1")]
        interrupt_result = _make_interrupt_result(plan_steps)

        with patch.object(app_module, "run_research", return_value=interrupt_result) as run_mock:
            result = app_module._run_research_with_stages(None, "test-thread-1")

        # run_research called with auto-built input from session_state
        run_mock.assert_called_once()
        call_args, call_kwargs = run_mock.call_args
        assert call_args[0] == {"user_query": "test interrupt"}
        assert call_kwargs.get("thread_id") == "test-thread-1"

        # result is the interrupt dict
        assert result == interrupt_result
        assert result.get("__interrupt__")

        # st.markdown("✅ 完成") NOT called on interrupt
        assert _completed_call_count(fake_st) == 0

    def test_tc_2_normal_final_report_branch(self, fake_st):
        """TC-2: normal final_report branch renders completion marker once."""
        _setup_session_state(fake_st, user_query="test final report", thread_id="test-thread-2")
        final_report = "## References\n\n1. [Source](http://example.com)\n"
        success_result = {"final_report": final_report}

        with patch.object(app_module, "run_research", return_value=success_result):
            result = app_module._run_research_with_stages(None, "test-thread-2")

        assert result == success_result
        assert result.get("final_report") == final_report
        # st.markdown("✅ 完成") called exactly once
        assert _completed_call_count(fake_st) == 1

    def test_tc_3_error_branch_no_pending_interrupt(self, fake_st):
        """TC-3 (a): no_pending_interrupt → no completion marker, error dict returned."""
        _setup_session_state(fake_st, user_query="test error", thread_id="t-err-a")
        err_a = {"error": "no_pending_interrupt", "thread_id": "t-err-a"}

        with patch.object(app_module, "run_research", return_value=err_a):
            result_a = app_module._run_research_with_stages(None, "t-err-a")

        assert result_a == err_a
        assert _completed_call_count(fake_st) == 0

    def test_tc_3_error_branch_thread_id_busy(self, fake_st):
        """TC-3 (b): thread_id_busy → no completion marker, error dict returned."""
        _setup_session_state(fake_st, user_query="test error", thread_id="t-err-b")
        err_b = {"error": "thread_id_busy", "thread_id": "t-err-b"}

        with patch.object(app_module, "run_research", return_value=err_b):
            result_b = app_module._run_research_with_stages(None, "t-err-b")

        assert result_b == err_b
        assert _completed_call_count(fake_st) == 0


# ===========================================================================
# Phase 6: US4 - cli.py main() tests (TC-4 / TC-5 / TC-6 / TC-7 / TC-8)
# ===========================================================================


class TestCliMain:
    """Tests for cli.py main() (per FR-011 ~ FR-014 + Q5 + Q6 + Q7)."""

    def _mock_psycopg_connect_zero(self, monkeypatch):
        """Mock cli_module.psycopg.connect to return count=0 (no existing checkpoints)."""

        class _CursorCM:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *args):
                return False

            def execute(self, *args, **kwargs):
                return None

            def fetchone(self):
                return [0]

        class _ConnCM:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *args):
                return False

            def cursor(self_inner):
                return _CursorCM()

        mock_psycopg = MagicMock()
        mock_psycopg.OperationalError = type(
            "OperationalError", (Exception,), {}
        )
        # connect() raises OperationalError on bad URI → _check_thread_id_reuse returns 0
        mock_psycopg.connect.return_value = _ConnCM()
        monkeypatch.setattr(cli_module, "psycopg", mock_psycopg)
        return mock_psycopg

    def test_tc_4_print_final_report(self, monkeypatch, capsys):
        """TC-4: cli prints final_report to stdout, exit 0."""
        monkeypatch.setattr(sys, "argv", ["cli", "为什么 LangGraph 1.2 用 interrupt()"])
        self._mock_psycopg_connect_zero(monkeypatch)

        success_result = {"final_report": "## References\n\n[ref_1] source1\n"}
        with patch.object(cli_module, "run_research", return_value=success_result):
            rc = cli_module.main()

        captured = capsys.readouterr()
        assert rc == 0
        assert "## References" in captured.out
        # stderr has thread_id + query logs
        assert "[cli] thread_id=" in captured.err
        assert "[cli] query=" in captured.err

    def test_tc_5_exception_exits_1(self, monkeypatch, capsys):
        """TC-5: invoke raises → sys.exit(1) + stderr [ERROR] <type>: <msg>."""
        monkeypatch.setattr(sys, "argv", ["cli", "boom query"])
        self._mock_psycopg_connect_zero(monkeypatch)

        with patch.object(
            cli_module, "run_research", side_effect=RuntimeError("boom failure")
        ):
            rc = cli_module.main()

        captured = capsys.readouterr()
        assert rc == 1
        assert "[ERROR] RuntimeError: boom failure" in captured.err

    def test_tc_6_no_position_arg_exits_1(self, monkeypatch, capsys):
        """TC-6: no position arg → argparse usage + [ERROR] missing + exit 1."""
        monkeypatch.setattr(sys, "argv", ["cli"])

        rc = cli_module.main()

        captured = capsys.readouterr()
        assert rc == 1
        assert "[ERROR] missing query argument" in captured.err
        # argparse usage (may be in stdout or stderr)
        combined = captured.out + captured.err
        assert "usage" in combined.lower() or "query" in combined.lower()

    def test_tc_7_thread_id_busy_exits_2(self, monkeypatch, capsys):
        """TC-7: thread_id_busy → sys.exit(2) + stderr [ERROR] thread_id_busy."""
        # Pass --thread-id so the busy message echoes the deterministic tid
        # we assert on. Without --thread-id the CLI auto-generates a uuid4
        # and the [ERROR] line's `thread_id=...` field won't match.
        monkeypatch.setattr(
            sys, "argv", ["cli", "...", "--thread-id=t-busy-007"]
        )
        self._mock_psycopg_connect_zero(monkeypatch)

        busy_result = {"error": "thread_id_busy", "thread_id": "t-busy-007"}
        with patch.object(cli_module, "run_research", return_value=busy_result):
            rc = cli_module.main()

        captured = capsys.readouterr()
        assert rc == 2
        assert "[ERROR] thread_id_busy: thread_id=t-busy-007 busy; retry later" in captured.err

    def test_tc_8_thread_id_reuse_warning_exits_0(self, monkeypatch, capsys):
        """TC-8: --thread-id=<existing> → stderr [cli] WARNING + exit 0."""

        # Override psycopg mock to return count=1 (existing checkpoint)
        class _CursorCM1:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *args):
                return False

            def execute(self, *args, **kwargs):
                return None

            def fetchone(self):
                return [1]

        class _ConnCM1:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *args):
                return False

            def cursor(self_inner):
                return _CursorCM1()

        existing_tid = "existing-test-tid-008"
        monkeypatch.setattr(
            sys, "argv", ["cli", "...", f"--thread-id={existing_tid}"]
        )

        mock_psycopg = MagicMock()
        mock_psycopg.OperationalError = type("OperationalError", (Exception,), {})
        mock_psycopg.connect.return_value = _ConnCM1()
        monkeypatch.setattr(cli_module, "psycopg", mock_psycopg)

        success_result = {"final_report": "## ok\n"}
        with patch.object(cli_module, "run_research", return_value=success_result):
            rc = cli_module.main()

        captured = capsys.readouterr()
        assert rc == 0
        assert (
            f"[cli] WARNING: thread_id={existing_tid} already has 1 checkpoint(s)"
            in captured.err
        )
        # stdout still has final_report
        assert "## ok" in captured.out


# ===========================================================================
# Phase 7: US1 + US2 + US3 - app.py::format_plan_md + _render() branches
# ===========================================================================


class TestFormatPlanMd:
    """Tests for app.py::format_plan_md (per FR-006 + C2 contract)."""

    def test_tc_9_format_plan_md_basic(self):
        """TC-9: format_plan_md renders all step fields (title + description)."""
        plan = [
            PlanStep(title="Step A", description="desc A"),
            PlanStep(title="Step B", description="desc B"),
        ]
        md = app_module.format_plan_md(plan)

        # Header
        assert md.startswith("# 拟议研究计划\n")
        # 1-based numbering + bold title
        assert "1. **Step A**\n   desc A" in md
        assert "2. **Step B**\n   desc B" in md

    def test_tc_10_format_plan_md_missing_description(self):
        """TC-10: format_plan_md omits the description line when description is None."""
        plan = [PlanStep(title="Only title", description=None)]
        md = app_module.format_plan_md(plan)

        assert "1. **Only title**" in md
        # No "   " prefix line right after the bold title (description omitted)
        assert "   None" not in md


class TestAppRender:
    """Tests for app.py::_render() (per FR-002 ~ FR-009 + ADR-0006)."""

    def test_tc_11_render_first_visit(self, fake_st, monkeypatch):
        """TC-11: first visit (last_action=None) — no invoke, home rendered."""
        # Patch run_research so any accidental invoke would surface as a failure
        run_mock = MagicMock(return_value={"final_report": "should-not-render"})
        monkeypatch.setattr(app_module, "run_research", run_mock)

        # Empty session_state → thread_id auto-gen + last_action default
        app_module._render()

        # run_research NOT called on first visit
        run_mock.assert_not_called()
        # st.title rendered for main area (per FR-004)
        assert hasattr(fake_st, "_title_calls")
        assert any("Deep Research" in str(t) for t in fake_st._title_calls)

    def test_tc_12_render_sidebar_reset_thread(self, fake_st, monkeypatch):
        """TC-12: sidebar Reset thread button → new uuid + rerun()."""
        # Seed session_state with a known thread_id
        fake_st.session_state["thread_id"] = "old-thread-id-aaaa"
        fake_st._button_clicks["reset_thread_btn"] = True

        # _render() will read the button, see True, generate new uuid, rerun
        app_module._render()

        # thread_id regenerated (different from the seed)
        new_tid = fake_st.session_state["thread_id"]
        assert new_tid != "old-thread-id-aaaa"
        # It's a valid uuid4 (8-4-4-4-12 hex with dashes)
        import uuid as _uuid

        # Should parse as uuid (raises ValueError otherwise)
        _uuid.UUID(new_tid)
        # rerun called
        assert fake_st._rerun_called is True

    def test_tc_13_render_sidebar_load_thread(self, fake_st, monkeypatch):
        """TC-13: sidebar Load thread (with input) → thread_id replaced + rerun."""
        fake_st.session_state["thread_id"] = "old-thread-id-bbbb"
        fake_st.session_state["last_action"] = "stale"
        fake_st.session_state["user_query"] = "stale query"
        fake_st._text_input_values["load_thread_input"] = "  pasted-tid-ccc  "
        fake_st._button_clicks["load_thread_btn"] = True

        app_module._render()

        # thread_id replaced with trimmed value
        assert fake_st.session_state["thread_id"] == "pasted-tid-ccc"
        # transient state cleared
        assert fake_st.session_state["last_action"] is None
        assert fake_st.session_state["user_query"] == ""
        assert fake_st._rerun_called is True

    def test_tc_14_render_sidebar_load_empty_warning(self, fake_st, monkeypatch):
        """TC-14: sidebar Load thread (empty input) → warning + no thread_id change."""
        fake_st.session_state["thread_id"] = "original-thread-dddd"
        fake_st._text_input_values["load_thread_input"] = "   "
        fake_st._button_clicks["load_thread_btn"] = True

        app_module._render()

        # thread_id unchanged
        assert fake_st.session_state["thread_id"] == "original-thread-dddd"
        # warning emitted
        assert any("请粘贴有效的 thread_id" in w for w in fake_st._warning_calls)
        # rerun NOT called
        assert fake_st._rerun_called is False

    def test_tc_15_render_start_dispatch_empty_query(self, fake_st, monkeypatch):
        """TC-15: last_action='start' with empty query → warning + no invoke."""
        run_mock = MagicMock(return_value={"final_report": "should-not-render"})
        monkeypatch.setattr(app_module, "run_research", run_mock)
        fake_st.session_state["last_action"] = "start"
        fake_st.session_state["user_query"] = "   "  # whitespace-only

        app_module._render()

        run_mock.assert_not_called()
        assert any("请输入研究问题" in w for w in fake_st._warning_calls)

    def test_tc_16_render_start_dispatch_runs_research(self, fake_st, monkeypatch):
        """TC-16: last_action='start' with query → invoke + last_action cleared."""
        success_result = {"final_report": "report body"}
        run_mock = MagicMock(return_value=success_result)
        monkeypatch.setattr(app_module, "run_research", run_mock)
        fake_st.session_state["last_action"] = "start"
        fake_st.session_state["user_query"] = "real query"
        fake_st.session_state["thread_id"] = "tid-start-016"

        app_module._render()

        run_mock.assert_called_once()
        call_args, call_kwargs = run_mock.call_args
        assert call_args[0] == {"user_query": "real query"}
        assert call_kwargs.get("thread_id") == "tid-start-016"
        # last_action cleared after dispatch
        assert fake_st.session_state["last_action"] is None
        # success branch rendered final_report
        assert any("report body" in str(c) for c in fake_st._markdown_calls)

    def test_tc_17_render_approve_dispatch(self, fake_st, monkeypatch):
        """TC-17: last_action='approve' → Command(resume='approve') passed."""
        success_result = {"final_report": "after approve"}
        run_mock = MagicMock(return_value=success_result)
        monkeypatch.setattr(app_module, "run_research", run_mock)
        fake_st.session_state["last_action"] = "approve"
        fake_st.session_state["user_input"] = "approve"
        fake_st.session_state["thread_id"] = "tid-approve-017"

        app_module._render()

        run_mock.assert_called_once()
        call_args, call_kwargs = run_mock.call_args
        # First positional arg is a Command(resume=...) — verify via type
        from langgraph.types import Command

        assert isinstance(call_args[0], Command)
        assert call_args[0].resume == "approve"
        assert call_kwargs.get("thread_id") == "tid-approve-017"

    def test_tc_18_render_edit_dispatch(self, fake_st, monkeypatch):
        """TC-18: last_action='edit' → Command(resume='edit: <fb>') passed."""
        run_mock = MagicMock(return_value={"final_report": "after edit"})
        monkeypatch.setattr(app_module, "run_research", run_mock)
        fake_st.session_state["last_action"] = "edit"
        fake_st.session_state["user_input"] = "edit: please add section"
        fake_st.session_state["thread_id"] = "tid-edit-018"

        app_module._render()

        from langgraph.types import Command

        call_args, _ = run_mock.call_args
        assert isinstance(call_args[0], Command)
        assert call_args[0].resume == "edit: please add section"

    def test_tc_19_render_result_interrupt_plan(self, fake_st, monkeypatch):
        """TC-19: result with __interrupt__ + plan → plan md + Approve/Edit buttons."""
        # Need to trigger interrupt-rendering branch by having result set;
        # easiest path: invoke returns interrupt result via session_state trigger.
        # Use a fake "render trigger" via patching _run_research_with_stages to
        # return the interrupt payload directly.
        interrupt_result = {
            "__interrupt__": [
                {"value": {"plan": [PlanStep(title="P1", description="D1")]}}
            ]
        }
        monkeypatch.setattr(
            app_module, "_run_research_with_stages", MagicMock(return_value=interrupt_result)
        )
        fake_st.session_state["last_action"] = "start"
        fake_st.session_state["user_query"] = "x"

        app_module._render()

        # plan_md rendered
        assert any("# 拟议研究计划" in str(c) for c in fake_st._markdown_calls)
        # Approve + Submit Edit buttons rendered (labels recorded)
        assert any("Approve" in str(label) for label in fake_st._button_labels)
        assert any("Submit Edit" in str(label) for label in fake_st._button_labels)

    def test_tc_20_render_result_no_pending_interrupt(self, fake_st, monkeypatch):
        """TC-20: result with error='no_pending_interrupt' → warning."""
        monkeypatch.setattr(
            app_module,
            "_run_research_with_stages",
            MagicMock(return_value={"error": "no_pending_interrupt"}),
        )
        fake_st.session_state["last_action"] = "start"
        fake_st.session_state["user_query"] = "x"

        app_module._render()

        assert any("⚠️ 该 thread 无 pending interrupt" in w for w in fake_st._warning_calls)

    def test_tc_21_render_result_thread_id_busy(self, fake_st, monkeypatch):
        """TC-21: result with error='thread_id_busy' → busy warning."""
        monkeypatch.setattr(
            app_module,
            "_run_research_with_stages",
            MagicMock(return_value={"error": "thread_id_busy"}),
        )
        fake_st.session_state["last_action"] = "start"
        fake_st.session_state["user_query"] = "x"

        app_module._render()

        assert any("Thread 正在被另一个 session 占用" in w for w in fake_st._warning_calls)

    def test_tc_22_render_result_other_error(self, fake_st, monkeypatch):
        """TC-22: result with unknown error → st.error."""
        monkeypatch.setattr(
            app_module,
            "_run_research_with_stages",
            MagicMock(return_value={"error": "weird_unknown_error"}),
        )
        fake_st.session_state["last_action"] = "start"
        fake_st.session_state["user_query"] = "x"

        app_module._render()

        assert any("weird_unknown_error" in str(c) for c in fake_st._error_calls)


# ===========================================================================
# Phase 8: US4 edge branches — cli.py OperationalError + non-busy error + --help
# ===========================================================================


class TestCliEdgeBranches:
    """Tests for cli.py edge-case branches (OperationalError / other errors)."""

    def test_tc_23_cli_db_unreachable_silently_returns_zero(
        self, monkeypatch, capsys
    ):
        """TC-23: psycopg.connect raises OperationalError → _check returns 0 silently."""
        # Mock psycopg.connect to raise OperationalError
        import psycopg as real_psycopg

        class _RaisingConnCM:
            def __enter__(self_inner):
                raise real_psycopg.OperationalError("connection failed")
            def __exit__(self_inner, *args):
                return False

        mock_psycopg = MagicMock()
        mock_psycopg.OperationalError = real_psycopg.OperationalError
        mock_psycopg.connect.return_value = _RaisingConnCM()
        monkeypatch.setattr(cli_module, "psycopg", mock_psycopg)

        # Direct call to _check_thread_id_reuse
        n = cli_module._check_thread_id_reuse("any-tid-023")
        assert n == 0

    def test_tc_23b_cli_uri_normalization_strips_asyncpg(self, monkeypatch):
        """TC-23b: LANGGRAPH_CHECKPOINT_DB=postgresql+asyncpg://... → normalized.

        Per project memory [[sqlalchemy-style-uri-with-sync-driver]]: sync
        psycopg.connect can't consume `postgresql+asyncpg://` URIs. cli.py
        must call `_normalize_pg_uri` before connecting.
        """
        captured_uris: list[str] = []

        class _CapturingConnCM:
            def __enter__(self_inner):
                return self_inner
            def __exit__(self_inner, *args):
                return False

            def cursor(self_inner):
                class _Cursor:
                    def __enter__(self_inner2):
                        return self_inner2
                    def __exit__(self_inner2, *args):
                        return False
                    def execute(self_inner2, *a, **k):
                        return None
                    def fetchone(self_inner2):
                        return [0]
                return _Cursor()

        import psycopg as real_psycopg

        mock_psycopg = MagicMock()
        mock_psycopg.OperationalError = real_psycopg.OperationalError

        def _capture(uri, **kwargs):
            captured_uris.append(uri)
            return _CapturingConnCM()

        mock_psycopg.connect.side_effect = _capture
        monkeypatch.setattr(cli_module, "psycopg", mock_psycopg)

        # Set env to asyncpg URI — must be normalized before psycopg.connect
        monkeypatch.setenv(
            "LANGGRAPH_CHECKPOINT_DB",
            "postgresql+asyncpg://postgres:123456@localhost:5432/deep_research_db",
        )

        n = cli_module._check_thread_id_reuse("tid-asyncpg-023b")
        assert n == 0
        # URI passed to psycopg.connect must NOT contain "+asyncpg"
        assert len(captured_uris) == 1
        assert "+asyncpg" not in captured_uris[0]
        assert captured_uris[0].startswith("postgresql://")

    def test_tc_24_cli_other_error_exits_1(self, monkeypatch, capsys):
        """TC-24: result.error='no_pending_interrupt' (non-busy) → exit 1."""
        monkeypatch.setattr(sys, "argv", ["cli", "q"])

        # Mock psycopg to return count=0
        class _CursorCM0:
            def __enter__(self_inner):
                return self_inner
            def __exit__(self_inner, *args):
                return False
            def execute(self, *a, **k):
                return None
            def fetchone(self):
                return [0]

        class _ConnCM0:
            def __enter__(self_inner):
                return self_inner
            def __exit__(self_inner, *args):
                return False
            def cursor(self_inner):
                return _CursorCM0()

        mock_psycopg = MagicMock()
        mock_psycopg.OperationalError = type("OperationalError", (Exception,), {})
        mock_psycopg.connect.return_value = _ConnCM0()
        monkeypatch.setattr(cli_module, "psycopg", mock_psycopg)

        err_result = {"error": "no_pending_interrupt"}
        with patch.object(cli_module, "run_research", return_value=err_result):
            rc = cli_module.main()

        captured = capsys.readouterr()
        assert rc == 1
        assert "[ERROR] no_pending_interrupt" in captured.err

    def test_tc_25_cli_help_exits_0(self, monkeypatch, capsys):
        """TC-25: argparse --help → SystemExit(0) caught → return 0."""
        monkeypatch.setattr(sys, "argv", ["cli", "--help"])
        rc = cli_module.main()
        assert rc == 0

    def test_tc_26_cli_main_module_runs(self, monkeypatch, capsys):
        """TC-26: __main__ block → sys.exit(main()) invoked (covered by direct call).

        The __main__ block is `sys.exit(main())` — we exercise the same
        call sequence directly to avoid subprocess / runpy complexity.
        """
        # Mock psycopg to return count=0
        class _CursorCMm:
            def __enter__(self_inner):
                return self_inner
            def __exit__(self_inner, *args):
                return False
            def execute(self, *a, **k):
                return None
            def fetchone(self):
                return [0]

        class _ConnCMm:
            def __enter__(self_inner):
                return self_inner
            def __exit__(self_inner, *args):
                return False
            def cursor(self_inner):
                return _CursorCMm()

        mock_psycopg = MagicMock()
        mock_psycopg.OperationalError = type("OperationalError", (Exception,), {})
        mock_psycopg.connect.return_value = _ConnCMm()
        monkeypatch.setattr(cli_module, "psycopg", mock_psycopg)

        # Patch sys.exit to capture exit code without raising
        exit_codes: list[int] = []
        monkeypatch.setattr(
            sys, "exit", lambda code=None: exit_codes.append(0 if code is None else code)
        )

        success_result = {"final_report": "## done\n"}
        with patch.object(cli_module, "run_research", return_value=success_result):
            # Replicate the __main__ block: sys.exit(main())
            sys.exit(cli_module.main(["main-block query"]))

        assert exit_codes and exit_codes[0] == 0

    def test_tc_27_cli_stdout_clean_during_run_research(self, monkeypatch, capsys):
        """TC-27: run_research progress noise (printed by graph/nodes) → stderr.

        Per FR-013: stdout = clean final_report only. The CLI uses
        `contextlib.redirect_stdout` to capture node logs at the CLI level
        (per FR-017: graph/nodes are off-limits). Capture stdout from
        run_research, re-route to stderr; only final_report reaches stdout.
        """
        monkeypatch.setattr(sys, "argv", ["cli", "q"])

        # Mock psycopg to return count=0
        class _CursorCM27:
            def __enter__(self_inner):
                return self_inner
            def __exit__(self_inner, *args):
                return False
            def execute(self, *a, **k):
                return None
            def fetchone(self_inner):
                return [0]

        class _ConnCM27:
            def __enter__(self_inner):
                return self_inner
            def __exit__(self_inner, *args):
                return False
            def cursor(self_inner):
                return _CursorCM27()

        mock_psycopg = MagicMock()
        mock_psycopg.OperationalError = type("OperationalError", (Exception,), {})
        mock_psycopg.connect.return_value = _ConnCM27()
        monkeypatch.setattr(cli_module, "psycopg", mock_psycopg)

        # Mock run_research to print progress noise to stdout BEFORE
        # returning the final_report dict. This simulates what graph.py
        # does (per the bug discovered during manual smoke).
        def fake_run_research(payload, *, thread_id):
            print("[research] node=gen_query iteration=1 target_step=0")
            print("[write] node=outline drafts_chars=920")
            print("[emit] final_report chars=42 references=1")
            return {"final_report": "## CLEAN REPORT\n\nbody\n"}

        with patch.object(cli_module, "run_research", side_effect=fake_run_research):
            rc = cli_module.main()

        captured = capsys.readouterr()

        # stdout: ONLY final_report — no progress noise leaked
        assert rc == 0
        # print() adds a trailing newline; allow it
        assert captured.out == "## CLEAN REPORT\n\nbody\n\n"
        # stderr: CLI logs + the captured progress noise
        assert "[cli] thread_id=" in captured.err
        assert "[cli] query=q" in captured.err
        assert "[research] node=gen_query" in captured.err
        assert "[write] node=outline" in captured.err
        assert "[emit] final_report" in captured.err
        # AND: progress noise did NOT leak to stdout
        assert "[research]" not in captured.out
        assert "[write]" not in captured.out
        assert "[emit]" not in captured.out