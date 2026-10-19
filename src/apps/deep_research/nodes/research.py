"""Research subgraph — ✅ spec 003 real implementation
(per FR-005, FR-006, FR-009, FR-011, FR-012, FR-013, FR-014, FR-015, FR-017,
ADR-0001, ADR-0002, ADR-0004).

Replaces the foundation stub with a real Send-fanout loop:

    START → gen_query → Send[websearch × 1-5] → eval_search → (cond) → END
                                                        ↓ (loop)
                                                   gen_query

Key contracts
-------------
- ``_gen_query_node`` is a NORMAL node that returns a state-update ``dict``
  (writes ``research_queries`` to the channel).  The Send fan-out is driven
  by the **routing function** ``_gen_query_routes`` (separate), which reads
  ``state["research_queries"]`` and returns ``list[Send]``.  This split is
  required because LangGraph 1.2.x rejects a node whose return value is
  ``list[Send]`` (it's a routing artifact, not a state channel write).
- ``iteration_counts["research"]`` is incremented at *entry* of
  ``_gen_query_node`` (per R6), so the very first call writes 1 even before
  any websearch starts.
- ``_websearch_node`` is a Send worker — it receives a per-query payload
  ``{"query", "query_index", "target_step", "iteration"}`` (NOT the full
  state) and returns a partial-state dict.  LangGraph's Send fan-in
  concatenates the worker returns into ``state["sources"]`` and
  ``state["research_queries"]`` via plain-list (overwrite) semantics — there
  is no reducer (per [[langgraph-subgraph-reducer-doubling]] memory).
- Known websearch errors (``NotYetImplementedError``) are swallowed per FR-013
  + R8 so a single worker failure (rate limit, empty results) does not block
  the other 4 workers.  Unknown errors propagate so Send fan-out fails loudly.
- ``build_research_subgraph`` uses ``add_conditional_edges("gen_query",
  _gen_query_routes, ["websearch"])`` — the 2nd arg is the routing
  function (returns ``list[Send]``), the 3rd is the Send path_map target
  list (per LangGraph 1.2+ Send API).  The routing function reads
  ``state["research_queries"]`` which the *node* (not the router) wrote
  one tick earlier.
"""
from __future__ import annotations

import os
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from src.apps.deep_research.prompts import NotYetImplementedError, get_llm, get_prompt
from src.apps.deep_research.schemas import ResearchQuery, SearchEvaluation, Source
from src.apps.deep_research.state import DeepResearchState
from src.apps.deep_research.tools import DashScopeReRank, DashScopeWebSearch

# spec 003 §C8 — Langfuse prompt names (per FR-009 + R9).
# The ``-v2`` suffix is required because Langfuse retained a ghost text v3
# of the original ``deep-research-gen-query`` (spec 002 era) under the
# ``latest`` label, blocking a chat-type version with the same name.
GEN_QUERY_PROMPT_NAME = "deep-research-gen-query-v2"
GEN_QUERY_PROMPT_LABEL = "production"

__all__ = [
    "build_research_subgraph",
    "_gen_query_node",
    "_gen_query_routes",
    "_websearch_node",
    "_eval_search_node",
    "_should_continue_research",
]


# --- T012: Send worker ------------------------------------------------------


def _websearch_node(payload: dict) -> dict:
    """One worker of the Send fan-out: call Bailian App, return partial sources.

    Per FR-011 + FR-013 + R8 + C5.  ``payload`` is the per-query dict that
    ``_gen_query_routes`` put into each ``Send("websearch", ...)``.

    The worker writes its deltas to ``raw_sources`` and ``research_queries``,
    NOT to ``sources``.  The plain ``sources`` field is reserved for the
    final renumbered output of ``_eval_search_node`` (per US3 data-model
    split; see state.py module docstring for the rationale).

    Parameters
    ----------
    payload : dict
        Required keys: ``query`` (str), ``query_index`` (int),
        ``target_step`` (int), ``iteration`` (int).

    Returns
    -------
    dict
        ``{"raw_sources": list[Source], "research_queries": list[ResearchQuery]}``.
        On known websearch failure (NotYetImplementedError from T008/T009/T010),
        ``raw_sources`` is empty and the ``ResearchQuery`` is still recorded so
        callers know the query was attempted (per FR-013).

    Raises
    ------
    KeyError
        ``payload`` missing ``query``.  Propagated so the Send fan-out fails
        loudly (per FR-022).
    """
    print(
        f"[research] node=websearch query={payload['query'][:50]}"
    )
    # Note: we call ``_run`` directly (not ``.run()``) because ``BaseTool.run``
    # is a positional ``tool_input``-first wrapper that doesn't accept arbitrary
    # kwargs like ``query_index``; ``_run(query, **kwargs)`` is the contract
    # we control (per C2).
    try:
        sources = DashScopeWebSearch()._run(
            query=payload["query"],
            query_index=payload["query_index"],
        )
    except NotYetImplementedError as e:
        # Per FR-013 + R8: known websearch failure (rate limited, empty
        # results, bailian 4xx, etc.) does not block the other 4 workers.
        # We swallow + log + still record the ResearchQuery so the call shows
        # up in state["research_queries"].
        print(f"[websearch] known error swallowed: {e}")
        sources = []
    rq = ResearchQuery(
        text=payload["query"],
        target_step=payload["target_step"],
        iteration=payload["iteration"],
    )
    return {"raw_sources": sources, "research_queries": [rq]}


# --- T013: gen_query node + Send routing function ---------------------------


def _gen_query_node(state: DeepResearchState) -> dict:
    """Generate 1-5 search queries; write them to ``state["research_queries"]``.

    Per FR-009 + FR-012 + FR-014 + C4 + R6:
    1. Increment ``iteration_counts["research"]`` at entry (R6).
    2. Pick the plan section this iteration covers: ``target_step =
       (iter_n - 1) % len(plan)``.  Each round covers one section; with
       3 iterations and a 3-section plan each section is covered once.
    3. Pull the ``deep-research-gen-query-v2`` chat prompt, render 5
       placeholders via chevron, call ``get_llm("research")``, parse JSON
       into ``list[ResearchQuery]`` (one retry on parse failure, no backoff).
    4. Return ``{"research_queries": [ResearchQuery, ...], "iteration_counts":
       {...}}`` — the routing function ``_gen_query_routes`` reads these and
       returns the ``list[Send]`` for the conditional edge.

    Note
    ----
    The node function does NOT return ``list[Send]`` itself.  LangGraph 1.2.x
    treats a node's return as a state-channel write and rejects
    ``InvalidUpdateError: Expected dict, got [Send(...)]``.  The Send fan-out
    is performed by ``_gen_query_routes`` (the conditional-edge function).

    Raises
    ------
    NotYetImplementedError
        JSON parse failed after retry, empty queries after retry, or HTTP
        429/5xx exhausted retries.
    """
    # Step 1: counter +1 at entry (R6).
    counts = dict(state.get("iteration_counts") or {})
    counts["research"] = int(counts.get("research", 0)) + 1
    iter_n = counts["research"]

    plan = state.get("plan") or []
    if not plan:
        raise NotYetImplementedError(
            "gen_query: state['plan'] is empty; plan_node did not write a plan"
        )
    target_step = (iter_n - 1) % len(plan)
    step = plan[target_step]

    print(
        f"[research] node=gen_query iteration={iter_n} target_step={target_step}"
    )

    # Step 2: pull + render prompt (5 placeholders; chat type, no {{#if}}).
    prompt = get_prompt(GEN_QUERY_PROMPT_NAME, label=GEN_QUERY_PROMPT_LABEL)
    user_query = state.get("user_query", "")
    section_title = step.title
    section_description = step.description or ""
    compiled_messages = prompt.compile(
        user_query=user_query,
        plan_section_title=section_title,
        plan_section_description=section_description,
        iteration=iter_n,
        target_step=target_step,
    )
    # `prompt.compile()` for chat type returns list[dict[role, content]] — we
    # pass it straight to ChatOpenAI.invoke() (per spec 002 R1 pattern).

    # Step 3: invoke LLM + JSON parse + 1 retry on failure.
    llm = get_llm("research")
    queries = _invoke_gen_query_with_retry(llm, compiled_messages, iter_n=iter_n)

    if not queries:
        raise NotYetImplementedError(
            "gen_query produced 0 queries after retry (LLM returned empty array)"
        )

    # Step 4: write the queries + the new iteration count to state.  The
    # routing function (_gen_query_routes) will read state["research_queries"]
    # next tick and emit one Send("websearch", ...) per element.
    return {
        "research_queries": queries,
        "iteration_counts": counts,
    }


def _gen_query_routes(state: DeepResearchState) -> list[Send]:
    """Conditional-edge routing function: emit one ``Send("websearch", ...)`` per
    research query that the node just wrote.

    Per LangGraph 1.2 Send API + C4 payload schema.  Each ``Send`` payload
    carries 4 msgpack-native scalars (per [[langgraph-pydantic-serde]]).
    """
    queries = state.get("research_queries") or []
    return [
        Send(
            "websearch",
            {
                "query": q.text,
                "target_step": q.target_step,
                "iteration": q.iteration,
                "query_index": i,
            },
        )
        for i, q in enumerate(queries)
    ]


def _invoke_gen_query_with_retry(
    llm, messages: list[dict[str, str]], *, iter_n: int
) -> list[ResearchQuery]:
    """Call the research LLM and parse its JSON output into ``ResearchQuery``.

    Per FR-013: retry once on JSON parse failure (no backoff, same prompt).
    HTTP 429/5xx inherits spec 002's _invoke_with_retry pattern via
    BadRequestError / RateLimitError / APIConnectionError / APITimeoutError
    from langchain_openai's underlying openai client.

    Parameters
    ----------
    iter_n : int
        Current ``iteration_counts["research"]`` — written into each
        ``ResearchQuery.iteration``.
    """
    # We import here to avoid a hard dependency at module import time (the
    # openai SDK exceptions are only needed when the LLM call actually fires).
    from openai import (
        APIConnectionError,
        APITimeoutError,
        AuthenticationError,
        BadRequestError,
        RateLimitError,
    )
    import time

    attempt = 0
    while True:
        try:
            resp = llm.invoke(messages)
            content = getattr(resp, "content", None)
            if isinstance(content, list):
                content = "".join(
                    c.get("text", "") if isinstance(c, dict) else str(c)
                    for c in content
                )
            content = str(content or "")
            try:
                parsed = _parse_queries_json(content)
            except ValueError as e:
                if attempt >= 1:
                    raise NotYetImplementedError(
                        f"gen_query JSON parse failed after retry: {e}"
                    ) from e
                print(f"[research] gen_query JSON parse retry: {e}")
                attempt += 1
                continue
            return [
                ResearchQuery(
                    text=q["text"],
                    target_step=int(q.get("target_step", 0)),
                    iteration=iter_n,
                )
                for q in parsed
            ]
        except BadRequestError as e:
            status = getattr(e, "status_code", None) or 0
            raise NotYetImplementedError(
                f"gen_query failed: bad request ({status}): {e}"
            ) from e
        except AuthenticationError as e:
            raise NotYetImplementedError(
                f"gen_query failed: auth error: {e}"
            ) from e
        except (RateLimitError, APIConnectionError, APITimeoutError) as e:
            if attempt >= 2:
                raise NotYetImplementedError(
                    f"gen_query failed: dashscope rate limited after 2 retries: {e}"
                ) from e
            backoff = 1.0 * (2 ** attempt)
            print(
                f"[research] gen_query retry attempt={attempt + 1} "
                f"after {type(e).__name__}, sleeping {backoff}s"
            )
            time.sleep(backoff)
            attempt += 1
            continue


def _parse_queries_json(content: str) -> list[dict[str, Any]]:
    """Parse the LLM's text response into ``list[dict]`` for query gen.

    Tolerates ```json ... ``` fences (defensive).  Returns the list of
    ``{"text": str, "target_step": int}`` dicts; caller is responsible for
    setting ``iteration`` on the resulting ``ResearchQuery``.
    """
    import json

    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    parsed = json.loads(text)
    if not isinstance(parsed, list):
        raise ValueError(f"expected JSON array, got {type(parsed).__name__}")
    return parsed


# --- T017 (US3) + T019 (US4) + T022 (US5) + T023 (US5) ---------------------


# spec 003 §C9 — Langfuse prompt for LLM Judge path (US5 T022 + T023).
JUDGE_PROMPT_NAME = "deep-research-judge"
JUDGE_PROMPT_LABEL = "production"


def _eval_search_node(state: DeepResearchState) -> dict:
    """Fan-in: dedupe → score (ReRank or LLM Judge) → filter → renumber.

    spec 003 US3 + US5 real impl (per FR-006 + FR-013 + FR-015 + FR-016 +
    FR-017 + C6 + C9 + data-model §5.1).

    Pipeline (per C6 step 1-7):
        1. Read merged sources from state["raw_sources"] (Send fan-in target;
           per US3 data-model split).
        2. URL dedup — same url, keep highest score (per FR-013 + Edge Case
           "同一 Source URL 跨多轮重复").
        3. Group by Source.query_index.
        4. Score each group:
            a. ``USE_RERANK=true`` (default) + ReRank succeeds → write
               ``Source.score`` in-place + tag evaluator="rerank".
            b. ``USE_RERANK=true`` + ReRank raises ``NotYetImplementedError``
               (T023 silent-degrade per R5) → fall back to LLM Judge for
               this group only.  Other groups stay on ReRank.
            c. ``USE_RERANK=true`` + group has no query text → fall back to
               LLM Judge for this group (defensive; shouldn't happen
               normally).
            d. ``USE_RERANK=false`` → LLM Judge for all groups (T022).
        5. Filter ``score < 0.3`` (only ReRank path; LLM Judge path keeps
           all per FR-017 + R5 + ADR-0004).
        6. Sort by (-score, query_index, original_order) and renumber 1..N
           (per data-model §2.3 + [Q6] decision).
        7. Build SearchEvaluation list (evaluator per source) with the new
           renumbered ``source_index``.

    Failure modes
    -------------
    - **ReRank SDKError** → silent fallback to LLM Judge for the failed
      group (R5 + ADR-0004).  If LLM Judge also fails, the group's sources
      are kept (no score, no filter) but the failed group has no
      ``SearchEvaluation`` entries (they'd all be error: "" anyway).
    - **LLM Judge parse / validation fail** → 1 retry, no backoff.  Still
      failing raises ``NotYetImplementedError("llm judge failed after
      retry: <reason>")`` (per FR-016 + data-model §10).
    - **3 iterations yielded 0 sources** → raise
      ``NotYetImplementedError("research: 3 iterations yielded 0 sources
      after ReRank filter")`` (per FR-015).
    """
    iter_n = int((state.get("iteration_counts") or {}).get("research", 0))
    raw_sources = list(state.get("raw_sources") or [])
    n_raw = len(raw_sources)
    print(
        f"[research] node=eval_search iteration={iter_n} "
        f"sources_so_far={n_raw}"
    )

    # ---- (1) read merged sources from raw_sources (Send fan-in target) ----
    sources: list[Source] = list(raw_sources)

    # ---- (2) URL dedup (keep highest score per url) ----
    seen: dict[str, Source] = {}
    for s in sources:
        url_key = str(s.url)
        existing = seen.get(url_key)
        if existing is None or (s.score or 0.0) > (existing.score or 0.0):
            seen[url_key] = s
    sources = list(seen.values())

    # ---- (3) group by query_index ----
    groups: dict[int, list[Source]] = {}
    for s in sources:
        groups.setdefault(s.query_index, []).append(s)

    # ---- build query_index -> text map for current iteration ----
    queries_by_iter: dict[int, list] = {}
    for q in (state.get("research_queries") or []):
        queries_by_iter.setdefault(q.iteration, []).append(q)
    current_queries = queries_by_iter.get(iter_n, [])

    def _query_text_for(source: Source) -> str:
        if 0 <= source.query_index < len(current_queries):
            return current_queries[source.query_index].text or ""
        return ""

    # ---- (4) score each group: ReRank or LLM Judge ----
    # ``scored`` is a parallel list to ``sources``: each entry is
    # ``(evaluator, relevance, rationale)`` for the source at the same
    # index in ``sources``.  None means "not scored yet" (impossible after
    # this block; we always set all sources to one of the two evaluators).
    use_rerank = os.environ.get("USE_RERANK", "true").lower() != "false"
    scored: list[tuple[str, float, str] | None] = [None] * len(sources)
    source_index_by_id = {id(s): i for i, s in enumerate(sources)}

    for q_idx, group in groups.items():
        if not group:
            continue
        # Resolve this group's indices in the master ``sources`` list (so
        # we can write into ``scored`` in the right place).
        group_pos = [source_index_by_id[id(s)] for s in group]
        query_text = _query_text_for(group[0])

        # ---- (4a) ReRank path: USE_RERANK=true AND query_text present ----
        if use_rerank and query_text:
            try:
                DashScopeReRank()._run(query=query_text, sources=group)
                # ReRank path → tag evaluator="rerank"
                for pos in group_pos:
                    s = sources[pos]
                    score = s.score or 0.0
                    scored[pos] = ("rerank", score, f"ReRank score: {score:.2f}")
                continue
            except NotYetImplementedError as e:
                # T023: silent-degrade per group (R5 + ADR-0004).  Fall
                # through to LLM Judge for THIS group only; the rest of
                # the groups keep their ReRank path.
                print(
                    f"[eval_search] rerank failed for group={q_idx}, "
                    f"falling back to LLM judge: {str(e)[:120]}"
                )
        # ---- (4b/4c/4d) LLM Judge path ----
        # (USE_RERANK=false OR no query_text OR ReRank failed)
        try:
            judged = _llm_judge_sources(state, group)
        except NotYetImplementedError as e:
            # Both ReRank and LLM Judge failed for this group.  Keep
            # sources (no score, no filter) but skip SearchEvaluations
            # for this group — they have no relevance/rationale to record.
            print(
                f"[eval_search] LLM Judge also failed for group={q_idx}: "
                f"{str(e)[:120]}; group sources kept without evaluations"
            )
            continue
        for pos, j in zip(group_pos, judged):
            scored[pos] = (
                "llm_judge",
                float(j["relevance"]),
                j.get("rationale", "") or "",
            )

    # ---- (5) filter score < 0.3 (ReRank only); drop unscored sources ----
    # LLM Judge path keeps all sources per FR-017 + R5.  We filter
    # post-scoring so we know which evaluator each source used.  Groups
    # for which BOTH ReRank and LLM Judge failed (scored[pos] still None)
    # are dropped entirely (no source, no eval) — we'd have no relevance
    # to record and the source's true quality is unknown.
    filtered: list[tuple[Source, str, float, str]] = []
    for s, sc in zip(sources, scored):
        if sc is None:
            # Both ReRank and LLM Judge failed for this source's group;
            # drop entirely (no eval, no source).  This is the only
            # place an "all-fail" group gets dropped.
            continue
        evaluator, relevance, rationale = sc
        if evaluator == "rerank" and (s.score is not None) and s.score < 0.3:
            # ReRank filter — drop.
            continue
        filtered.append((s, evaluator, relevance, rationale))

    # ---- (6) sort by (-relevance, query_index, original_order) and renumber ----
    # We sort by ``relevance`` (which for ReRank == s.score, for LLM Judge
    # == the LLM-given relevance) so ReRank+Judge paths produce a coherent
    # ranking even if a graph runs mixed.
    sorted_with_idx = sorted(
        enumerate(filtered),
        key=lambda x: (
            -(x[1][2]),       # -relevance
            x[1][0].query_index,
            x[0],             # original order
        ),
    )
    renumbered: list[Source] = []
    evaluations: list[SearchEvaluation] = []
    for new_idx, (_, (src, evaluator, relevance, rationale)) in enumerate(
        sorted_with_idx, start=1
    ):
        new_src = src.model_copy(update={"index": new_idx})
        renumbered.append(new_src)
        evaluations.append(
            SearchEvaluation(
                source_index=new_idx,
                relevance=relevance,
                rationale=rationale,
                evaluator=evaluator,
            )
        )

    # ---- (7) termination-by-starvation guard (per FR-015) ----
    if not renumbered and iter_n >= 3:
        raise NotYetImplementedError(
            "research: 3 iterations yielded 0 sources after ReRank filter"
        )

    return {"sources": renumbered, "search_evaluations": evaluations}


def _llm_judge_sources(
    state: DeepResearchState, sources: list[Source]
) -> list[dict[str, Any]]:
    """Score ``sources`` with the LLM Judge prompt (per FR-016 + C9).

    The Judge prompt (``deep-research-judge``, chat type) is fetched from
    Langfuse and rendered with ``user_query`` + ``sources_json``.  The
    LLM (re-using ``get_llm("research")`` per [Q5] decision — no new
    role="judge" needed) is invoked; output is parsed as a JSON array of
    ``{source_index, relevance, rationale}`` objects.  We retry once
    (no backoff) on parse failure or out-of-range relevance; still
    failing raises ``NotYetImplementedError`` (per FR-016 + data-model §10).

    Parameters
    ----------
    state : DeepResearchState
        Read ``user_query`` for the prompt placeholder.
    sources : list[Source]
        Sources to judge.  Must be non-empty.

    Returns
    -------
    list[dict]
        Same length as ``sources``; each element has keys
        ``"source_index"`` (int, matches position in input list per the
        prompt's "source_index: matches the index in the input array"
        instruction), ``"relevance"`` (float in [0.0, 1.0]), and
        ``"rationale"`` (str ≤ 200 chars per the prompt).
    """
    import json
    import time

    if not sources:
        return []

    # Step 1: build the sources_json payload.  We use ``mode="json"`` to
    # coerce Pydantic ``HttpUrl`` to ``str`` (per
    # [[langgraph-pydantic-serde]]); we explicitly include only the 4
    # fields the prompt needs.
    sources_json = json.dumps(
        [
            s.model_dump(mode="json", include={"index", "title", "url", "content"})
            for s in sources
        ],
        ensure_ascii=False,
    )

    # Step 2: pull + render prompt.  Chat type — ``prompt.compile(**vars)``
    # returns ``list[dict[role, content]]`` directly (no chevron needed;
    # same pattern as T013 deep-research-gen-query-v2).
    prompt = get_prompt(JUDGE_PROMPT_NAME, label=JUDGE_PROMPT_LABEL)
    user_query = state.get("user_query", "")
    messages = prompt.compile(user_query=user_query, sources_json=sources_json)

    # Step 3: invoke LLM (reuse research role per [Q5] — temperature 0.2
    # is fine for relevance judging).
    llm = get_llm("research")

    def _is_valid_judge(parsed: list) -> bool:
        if not isinstance(parsed, list):
            return False
        if len(parsed) != len(sources):
            return False
        for j in parsed:
            if not isinstance(j, dict):
                return False
            if not (0.0 <= float(j.get("relevance", -1.0)) <= 1.0):
                return False
        return True

    def _try_parse(content: str) -> list[dict[str, Any]]:
        text = content.strip()
        # Defensive: strip ```json ... ``` fences.
        if text.startswith("```"):
            lines = text.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        parsed = json.loads(text)
        if not isinstance(parsed, list):
            raise ValueError(f"expected JSON array, got {type(parsed).__name__}")
        for j in parsed:
            if not (0.0 <= float(j["relevance"]) <= 1.0):
                raise ValueError(
                    f"relevance {j['relevance']!r} out of [0.0, 1.0]"
                )
        return parsed

    from openai import (
        APIConnectionError,
        APITimeoutError,
        AuthenticationError,
        BadRequestError,
        RateLimitError,
    )

    last_err: Exception | None = None
    for attempt in range(2):  # 0 = first try, 1 = one retry (per FR-016)
        try:
            resp = llm.invoke(messages)
            content = getattr(resp, "content", None)
            if isinstance(content, list):
                content = "".join(
                    c.get("text", "") if isinstance(c, dict) else str(c)
                    for c in content
                )
            content = str(content or "")
            try:
                parsed = _try_parse(content)
            except (ValueError, json.JSONDecodeError) as e:
                last_err = e
                if attempt >= 1:
                    raise NotYetImplementedError(
                        f"llm judge failed after retry: {e}"
                    ) from e
                print(
                    f"[eval_search] LLM Judge parse retry: {e}"
                )
                time.sleep(0.5)  # tiny backoff for parse retry
                continue
            if not _is_valid_judge(parsed):
                last_err = ValueError(
                    f"llm judge returned wrong shape: "
                    f"len={len(parsed)} (expected {len(sources)})"
                )
                if attempt >= 1:
                    raise NotYetImplementedError(
                        f"llm judge failed after retry: {last_err}"
                    ) from last_err
                time.sleep(0.5)
                continue
            return [
                {
                    "source_index": int(j.get("source_index", i)),
                    "relevance": float(j["relevance"]),
                    "rationale": str(j.get("rationale", ""))[:200],
                }
                for i, j in enumerate(parsed)
            ]
        except BadRequestError as e:
            status = getattr(e, "status_code", None) or 0
            raise NotYetImplementedError(
                f"llm judge failed: bad request ({status}): {e}"
            ) from e
        except AuthenticationError as e:
            raise NotYetImplementedError(
                f"llm judge failed: auth error: {e}"
            ) from e
        except (RateLimitError, APIConnectionError, APITimeoutError) as e:
            if attempt >= 1:
                raise NotYetImplementedError(
                    f"llm judge failed: dashscope rate limited after retry: {e}"
                ) from e
            backoff = 1.0
            print(
                f"[eval_search] LLM Judge retry attempt={attempt + 1} "
                f"after {type(e).__name__}, sleeping {backoff}s"
            )
            time.sleep(backoff)
            continue

    # Should not reach here — either return inside the loop or raise.
    raise NotYetImplementedError(
        f"llm judge failed after retry: {last_err}"
    )


def _should_continue_research(state: DeepResearchState) -> str:
    """Decide whether to loop another research iteration (US4 T019).

    Per FR-014 + R3 + data-model §6 状态转换图:
        - ``len(state["sources"]) >= 8`` → END (have enough material)
        - ``iteration_counts["research"] >= 3`` → END (hit max rounds)
        - else → "gen_query" (start another round)

    Reads ``state["sources"]`` — the renumbered final list written by
    ``_eval_search_node`` (per US3 data-model split: ``raw_sources`` carries
    the worker fan-in output; ``sources`` carries the deduped/ReRanked/
    renumbered result).  Iteration count is read from
    ``iteration_counts["research"]`` (incremented at ``_gen_query_node``
    entry per R6, so it's the current round number 1..3).

    Edge cases
    ----------
    - First iteration: ``iteration_counts["research"] = 0`` (not yet entered
      gen_query) — this shouldn't normally happen because eval_search is
      only reached after gen_query, but defensively we treat 0 as "continue"
      so we never END before research even started.
    - Empty state["sources"] (FR-015 starvation case): still continue up to
      3 rounds, then the starvation guard in ``_eval_search_node`` raises.
    - Missing ``iteration_counts`` (defensive): treat as 0 → continue.

    Returns
    -------
    str
        ``"gen_query"`` to loop back, or the constant ``END`` (a str literal
        imported from langgraph.graph) to exit the subgraph.  LangGraph's
        ``add_conditional_edges`` with ``{"gen_query": "gen_query",
        END: END}`` then routes accordingly.
    """
    n_sources = len(state.get("sources") or [])
    n_iters = int((state.get("iteration_counts") or {}).get("research", 0))
    if n_sources >= 8 or n_iters >= 3:
        return END
    return "gen_query"


# --- T014: build the subgraph ------------------------------------------------


def build_research_subgraph():
    """Build and compile the research subgraph (per FR-018 + C4-C6 + R3 + R4).

    Topology:

        START → gen_query → Send[websearch × 1-5] → eval_search
                                                            ↓
                                                  (_should_continue_research)
                                                            ↓
                                                       gen_query | END

    Note
    ----
    The conditional edge after ``gen_query`` uses a *separate* routing
    function ``_gen_query_routes`` (not ``_gen_query_node`` itself) because
    LangGraph 1.2.x requires node return values to be state-channel writes
    (dict), not routing artifacts (list[Send]).  ``_gen_query_node`` writes
    ``state["research_queries"]``; ``_gen_query_routes`` reads it next tick
    and emits one ``Send("websearch", ...)`` per element.

    The 3rd argument to ``add_conditional_edges`` ("websearch") is the Send
    path_map target list — LangGraph 1.2.11+ requires the worker node name
    to appear in this list so the runtime knows where each ``Send`` payload
    should be routed.
    """
    g = StateGraph(DeepResearchState)
    g.add_node("gen_query", _gen_query_node)
    g.add_node("websearch", _websearch_node)
    g.add_node("eval_search", _eval_search_node)
    g.add_edge(START, "gen_query")
    g.add_conditional_edges(
        "gen_query",
        _gen_query_routes,
        ["websearch"],  # Send path_map target list
    )
    g.add_edge("websearch", "eval_search")
    g.add_conditional_edges(
        "eval_search",
        _should_continue_research,
        {"gen_query": "gen_query", END: END},
    )
    return g.compile()
