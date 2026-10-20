"""CLI smoke test for deep research agent (per ADR-0006 + spec 005).

Usage:
    python -m src.apps.deep_research.cli "..." [--thread-id=<tid>]

Exit code convention (per Q5 澄清 2026-10-19):
    0 = success / 2 = thread_id_busy (retry-able per POSIX < 64) / 1 = other error

Stderr logs (per FR-014):
    [cli] thread_id=<tid>
    [cli] query=<query[:80]>
    [cli] WARNING: thread_id=<tid> already has N checkpoint(s); ...  (when --thread-id reused)

Stdout:
    final_report Markdown (per FR-013) — clean final_report, no progress noise
"""
from __future__ import annotations

import argparse
import contextlib
import io
import os
import sys
import uuid

import psycopg

from src.apps.deep_research.graph import DEFAULT_DB_URI, _normalize_pg_uri, run_research


def _check_thread_id_reuse(tid: str) -> int:
    """Return count of existing checkpoints for tid; 0 if none / DB unreachable.

    Per R4 + FR-014: invokes `psycopg.connect(LANGGRAPH_CHECKPOINT_DB)` and runs
    `SELECT count(*) FROM checkpoints WHERE thread_id = :tid`. If Postgres is
    unreachable, returns 0 (silent — subsequent run_research will surface the
    connection error via exit 1).

    Note: normalize the URI first so `postgresql+asyncpg://...` (commonly set
    in .env for the async driver) doesn't trip psycopg's conninfo parser as a
    `ProgrammingError`. Mirrors `_normalize_pg_uri()` in graph.py.
    """
    raw_uri = os.environ.get("LANGGRAPH_CHECKPOINT_DB", DEFAULT_DB_URI)
    uri = _normalize_pg_uri(raw_uri)
    try:
        with psycopg.connect(uri, connect_timeout=3) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM checkpoints WHERE thread_id = %s",
                (tid,),
            )
            row = cur.fetchone()
            return int(row[0]) if row else 0
    except psycopg.OperationalError:
        return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry — parses args + invokes run_research + prints final_report.

    Per FR-011 ~ FR-014 + Q5 (exit code 0/1/2) + Q7 (auto_confirm_plan=True hardcoded).
    """
    parser = argparse.ArgumentParser(
        prog="deep-research-cli",
        description="Deep research agent CLI smoke test (auto_confirm_plan=True hardcoded per ADR-0006)",
    )
    parser.add_argument(
        "query",
        help="研究问题（必填，空字符串视作缺参 → exit 1）",
    )
    parser.add_argument(
        "--thread-id",
        default=None,
        help="LangGraph thread_id（缺省时 CLI 生成新 uuid4）",
    )
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        # argparse raises SystemExit on usage errors (default code 2).
        # Per spec Edge Cases: convert to exit 1 + add our [ERROR] marker
        # so the failure mode matches FR-013 (--help, code 0, passes through).
        code = exc.code
        if code is None or code == 0:
            return 0  # --help or explicit exit(0)
        print("[ERROR] missing query argument", file=sys.stderr)
        return 1

    query = (args.query or "").strip()
    if not query:
        print("[ERROR] missing query argument", file=sys.stderr)
        return 1

    tid = args.thread_id or str(uuid.uuid4())
    print(f"[cli] thread_id={tid}", file=sys.stderr)
    print(f"[cli] query={query[:80]}", file=sys.stderr)

    # Per FR-014 + Q6 (2026-10-19): warn when --thread-id already has checkpoints
    n = _check_thread_id_reuse(tid)
    if n > 0:
        print(
            f"[cli] WARNING: thread_id={tid} already has {n} checkpoint(s); "
            f"new run will create a fresh checkpoint and not inherit prior state",
            file=sys.stderr,
        )

    # Per FR-013: stdout = clean final_report only. The graph / nodes use
    # `print(...)` to emit `[research]` / `[write]` / `[emit]` progress logs,
    # which would otherwise contaminate stdout. Capture stdout at the CLI
    # boundary (per FR-017: we don't modify graph / nodes) and re-route the
    # captured buffer to stderr so the progress info is still visible.
    with contextlib.redirect_stdout(io.StringIO()) as stdout_buf:
        try:
            result = run_research(
                {"user_query": query, "auto_confirm_plan": True},
                thread_id=tid,
            )
        except Exception as exc:  # noqa: BLE001 (per FR-011: top-level guard, catch all + emit + exit 1)
            captured = stdout_buf.getvalue()
            if captured:
                print(captured, file=sys.stderr, end="")
            print(f"[ERROR] {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1

    # Re-route any progress noise captured during run_research to stderr
    captured = stdout_buf.getvalue()
    if captured:
        print(captured, file=sys.stderr, end="")

    # Error mapping (per FR-013 + Q5)
    err = result.get("error") if isinstance(result, dict) else None
    if err == "thread_id_busy":
        # Per Q5: busy → exit 2 (retry-able per POSIX < 64 convention)
        print(
            f"[ERROR] thread_id_busy: thread_id={tid} busy; retry later",
            file=sys.stderr,
        )
        return 2
    if err:
        # Other errors → exit 1 (fatal, no retry)
        print(f"[ERROR] {err}", file=sys.stderr)
        return 1

    # Success: print final_report to stdout (per FR-013 + Q3)
    final_report = (
        result.get("final_report", "") if isinstance(result, dict) else ""
    )
    print(final_report)
    return 0


if __name__ == "__main__":
    sys.exit(main())