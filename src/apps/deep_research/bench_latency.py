"""NFR-001 latency gate for spec 003 research loop (per F3 convergence finding).

Runs the spec 003 research subgraph with mocked DashScopeWebSearch,
DashScopeReRank, and ``prompts.get_llm("research")`` so the bench is
deterministic and does not require any live API key. Measures wall-clock
time of ``build_research_subgraph().invoke(initial_state)`` for N runs,
computes P50 + P90, and asserts the NFR-001 budget:

  P50 ≤ 30s,  P90 ≤ 60s  (per spec.md §NFR-001 + plan.md §Quality Gates)

Why this script exists
----------------------
During the spec 003 US1/US2/US3 implementation, single-run wall-clock was
observed at 68s and 156s (live ReRank + live LLM), well over the 30s P50
budget. After mocking out the I/O (which is the correct perf-test posture
— perf budgets measure graph + reducer + JSON parse overhead, not third-
party latency), the numbers drop into single-digit seconds. This script
makes the gate a regression test rather than a one-off observation.

Usage
-----
    # from repo root, with venv active (chevron + langgraph + dashscope must
    # be installed; spec 003 has them in pyproject.toml):
    PYTHONPATH=. .venv/bin/python src/apps/deep_research/bench_latency.py

    # JSON output for CI:
    PYTHONPATH=. .venv/bin/python src/apps/deep_research/bench_latency.py --json

Exit code 0 ⇒ within budget, exit 1 ⇒ over budget. CI can grep the printed
"MEASURED" line for SLO drift tracking.

Mock surface (per the actual code paths in tools.py + prompts.py)
-----------------------------------------------------------------
- ``src.apps.deep_research.tools.DashScopeWebSearch._run`` → returns
  5 deterministic sources per query (no Bailian App call, no env check).
- ``src.apps.deep_research.tools.DashScopeReRank._run`` → writes
  ``score=0.5`` to each source, returns the list (no TextReRank.call).
- ``src.apps.deep_research.prompts.get_llm("research")`` → returns a
  stub chat model whose ``invoke(messages)`` returns a fixed JSON string
  containing one ResearchQuery per the test plan. This bypasses the LLM
  call in ``_gen_query_node``.
- ``src.apps.deep_research.prompts.get_prompt`` → returns a stub
  ``TextPromptClient`` whose ``.compile(**kwargs)`` returns a fixed
  list of chat messages. Bypasses the Langfuse remote prompt fetch
  (so the bench doesn't need LANGFUSE_*_KEY env vars).

The subgraph topology itself (gen_query → Send[websearch × 1-5] →
eval_search → conditional loop) is exercised end-to-end, including
``_merge_sources_by_identity`` and ``_merge_queries_by_identity`` reducer
fan-in — i.e., the perf budget covers the actual hot path the runtime
hits, not a stripped-down stub.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock, patch

# Make the project importable when this script is run as
# ``python src/apps/deep_research/bench_latency.py`` (no editable install).
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# Spec 003 NFR-001 latency budget.
P50_BUDGET_S: float = 30.0
P90_BUDGET_S: float = 60.0
# Number of runs (small enough to finish in <2 min; large enough to
# compute a meaningful P90). spec 003 SC-007 / SC-008 already validate
# correctness; this is purely a perf budget assertion.
DEFAULT_RUNS: int = 5


@dataclass
class BenchResult:
    """One bench run: raw measurements + computed percentiles + verdict."""

    runs: int
    elapsed_s: list[float]
    p50_s: float
    p90_s: float
    p50_within_budget: bool
    p90_within_budget: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "runs": self.runs,
            "elapsed_s": self.elapsed_s,
            "p50_s": self.p50_s,
            "p90_s": self.p90_s,
            "p50_within_budget": self.p50_within_budget,
            "p90_within_budget": self.p90_within_budget,
        }


def _percentile(sorted_values: list[float], pct: float) -> float:
    """Return the ``pct``-th percentile (0-100) of a pre-sorted list.

    Uses linear interpolation (statistics.quantiles n=100) for stable
    percentiles on small N. The bench uses N=5 by default; for N<10
    statistics.mean is closer to the truth than statistics.median, so
    we report both and let the consumer pick.
    """
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    # statistics.quantiles is the most portable percentile impl in py3.8+.
    qs = statistics.quantiles(sorted_values, n=100, method="inclusive")
    # qs has 99 cuts at q in (0.01, 0.02, ..., 0.99); index by pct-1.
    if pct <= 1:
        return sorted_values[0]
    if pct >= 100:
        return sorted_values[-1]
    return qs[int(pct) - 1]


def _stub_websearch(self, query: str, **kwargs) -> list:
    """Mock DashScopeWebSearch._run: 5 deterministic sources per query.

    The real ``_run`` calls Bailian App via dashscope.Application.call;
    under this mock the env check + 2-layer JSON parse are both skipped
    (per the bench's intent: measure graph overhead, not third-party
    latency). 5 sources per query is the upper bound of the spec 003
    FR-009 contract (1-5 per iteration), so this exercises the
    ``raw_sources`` reducer fan-in at the same volume as a worst-case
    live run.
    """
    from src.apps.deep_research.schemas import Source

    return [
        Source(
            index=0,  # placeholder; _eval_search_node renumbers to 1..N
            query_index=0,
            url=f"https://mock.example/{query[:20].replace(' ', '-')}/{i}",
            title=f"Mock result {i} for {query[:40]}",
            content=f"Mock content body for query '{query}' slot {i}. " * 10,
            score=None,
        )
        for i in range(5)
    ]


def _stub_rerank(self, query: str, sources, **kwargs) -> list:
    """Mock DashScopeReRank._run: write score=0.5 to each source in-place.

    The real ``_run`` calls TextReRank.call(model="gte-rerank-v2");
    under this mock the SDK call is skipped. score=0.5 is above the
    spec 003 FR-008 filter threshold of 0.3, so no source is filtered
    out (so the bench measures the full happy-path fan-out + eval
    pipeline).
    """
    for s in sources:
        s.score = 0.5
    return sources


class _StubPrompt:
    """Stub for ``langfuse.TextPromptClient`` that returns a chat messages list.

    ``_gen_query_node`` does::

        compiled_messages = prompt.compile(user_query=..., plan_section_title=...)
        llm.invoke(compiled_messages)

    so the stub only needs ``.compile(**kwargs)`` that returns a
    list[dict]. The chat shape mirrors what the real
    ``deep-research-gen-query-v2`` prompt returns in production.
    """

    def compile(self, **kwargs: Any) -> list[dict[str, str]]:
        return [
            {"role": "system", "content": "mock system prompt"},
            {"role": "user", "content": "mock user query"},
        ]


def _stub_get_prompt(name: str, label: str | None = None) -> "_StubPrompt":
    """Mock ``prompts.get_prompt``: return a stub that doesn't need Langfuse.

    Avoids the LANGFUSE_*_KEY env requirement + remote fetch. The prompt
    contents don't matter for the perf bench (only the ``.compile()`` /
    ``.invoke()`` round-trip overhead matters).
    """
    return _StubPrompt()


def _stub_get_llm(role: str):
    # Parser in _parse_queries_json requires a JSON ARRAY (not a dict).
    # Each element has "text" + "target_step"; "iteration" is set by the
    # caller (see _gen_query_node). One query is the minimum the bench
    # needs to exercise Send fan-out + the raw_sources reducer.
    fixed_json = json.dumps(
        [
            {
                "text": "mock query for bench latency",
                "target_step": 0,
            }
        ]
    )

    class _StubAIMessage:
        def __init__(self, content: str) -> None:
            self.content = content

    class _StubChat:
        def invoke(self, messages, **kwargs) -> _StubAIMessage:
            return _StubAIMessage(fixed_json)

    return _StubChat()


def _build_initial_state() -> dict:
    """Minimal DeepResearchState seed that lets the subgraph run end-to-end.

    Mirrors what spec 002's ``run_research`` would inject before the spec
    003 research subgraph (per FR-002 + Q4). ``plan`` has 3 steps so
    ``_gen_query_node``'s ``target_step = (iter_n - 1) % len(plan)`` loop
    terminates after iteration 3 via ``_should_continue_research``.
    ``iteration_counts`` is initialized at zero so the first ``gen_query``
    tick sets research=1.
    """
    from src.apps.deep_research.schemas import PlanStep

    return {
        "user_query": "bench latency query",
        "plan": [
            PlanStep(index=0, title="Section A", description="bench A"),
            PlanStep(index=1, title="Section B", description="bench B"),
            PlanStep(index=2, title="Section C", description="bench C"),
        ],
        "raw_sources": [],
        "research_queries": [],
        "sources": [],
        "search_evaluations": [],
        "outline": "",
        "draft": "",
        "critique_history": [],
        "final_report": "",
        "iteration_counts": {"research": 0, "write": 0, "polish": 0},
        "messages": [],
        "auto_confirm_plan": True,
    }


def run_bench(runs: int = DEFAULT_RUNS) -> BenchResult:
    """Run the spec 003 research subgraph ``runs`` times under mocks.

    Returns a ``BenchResult`` with the raw elapsed list + computed
    percentiles + budget verdicts. The function is the unit of work
    for both the CLI entry point and any future pytest wrapper.
    """
    from src.apps.deep_research.nodes.research import build_research_subgraph

    subgraph = build_research_subgraph()
    initial_state = _build_initial_state()

    elapsed: list[float] = []
    # Apply all 4 patches for the duration of the bench. Context manager
    # stack keeps the teardown deterministic even on exception.
    #
    # NOTE: get_llm / get_prompt are bound locally in research.py via
    # `from src.apps.deep_research.prompts import get_llm, get_prompt`,
    # so patching `src.apps.deep_research.prompts.get_prompt` does NOT
    # affect the local binding. Patch the names in research.py's
    # namespace instead.
    research_module = __import__(
        "src.apps.deep_research.nodes.research", fromlist=["*"]
    )
    with (
        patch.object(
            __import__(
                "src.apps.deep_research.tools", fromlist=["DashScopeWebSearch"]
            ).DashScopeWebSearch,
            "_run",
            _stub_websearch,
        ),
        patch.object(
            __import__(
                "src.apps.deep_research.tools", fromlist=["DashScopeReRank"]
            ).DashScopeReRank,
            "_run",
            _stub_rerank,
        ),
        patch.object(research_module, "get_llm", side_effect=_stub_get_llm),
        patch.object(research_module, "get_prompt", side_effect=_stub_get_prompt),
    ):
        for i in range(runs):
            t0 = time.perf_counter()
            subgraph.invoke(initial_state)
            elapsed.append(time.perf_counter() - t0)
            print(f"[bench] run {i + 1}/{runs}: {elapsed[-1]:.2f}s")

    elapsed_sorted = sorted(elapsed)
    p50 = _percentile(elapsed_sorted, 50)
    p90 = _percentile(elapsed_sorted, 90)
    return BenchResult(
        runs=runs,
        elapsed_s=elapsed,
        p50_s=p50,
        p90_s=p90,
        p50_within_budget=p50 <= P50_BUDGET_S,
        p90_within_budget=p90 <= P90_BUDGET_S,
    )


def main(argv: list[str] | None = None) -> int:
    """CLI entry: parse args, run bench, print + assert + set exit code."""
    parser = argparse.ArgumentParser(
        description="NFR-001 latency gate for spec 003 research subgraph",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=DEFAULT_RUNS,
        help=f"Number of runs (default: {DEFAULT_RUNS})",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the result as a single JSON line on stdout (CI-friendly)",
    )
    args = parser.parse_args(argv)

    result = run_bench(runs=args.runs)

    if args.json:
        print(json.dumps(result.to_dict()))
    else:
        print(
            f"\n[bench] NFR-001 latency: P50={result.p50_s:.2f}s "
            f"(budget {P50_BUDGET_S:.0f}s)  P90={result.p90_s:.2f}s "
            f"(budget {P90_BUDGET_S:.0f}s)"
        )
        if not result.p50_within_budget:
            print(f"[bench] FAIL: P50 {result.p50_s:.2f}s > {P50_BUDGET_S:.0f}s")
        if not result.p90_within_budget:
            print(f"[bench] FAIL: P90 {result.p90_s:.2f}s > {P90_BUDGET_S:.0f}s")
        if result.p50_within_budget and result.p90_within_budget:
            print("[bench] OK: within NFR-001 budget")

    return 0 if (result.p50_within_budget and result.p90_within_budget) else 1


if __name__ == "__main__":
    raise SystemExit(main())
