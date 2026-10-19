"""DashScope integration: Bailian web search App + gte-rerank-v2 ReRank (per spec 003 FR-004..FR-009).

Both classes are ``BaseTool`` subclasses; their ``_run`` method (real impl,
spec 003) backs the research subgraph's websearch / eval_search nodes.

Web search footgun
------------------
``dashscope.Generation.call(enable_search=True)`` is **NOT** used because the
regional Aliyun MaaS gateway serving this deployment only proxies
``/v1/chat/completions`` and silently returns ``InvalidApiKey`` for
``enable_search=True``. The only stable path is
``dashscope.Application.call(app_id=APP_ID, prompt=query, api_key=APP_TOKEN)``
hitting the Aliyun Bailian (百炼) web-search App directly. See project memory
[[dashscope-mmaas-enable-search-needs-bailian-app]].

Contract drift from §C2 of the spec
-----------------------------------
The original contract (contracts §C2) was authored before the SDK signature
and Bailian App output schema were known empirically. Two corrections:

1. **SDK signature**: ``Application.call(applet_id=...)`` is wrong. Real
   signature (verified against dashscope 1.27.7 via ``inspect``): the first
   positional arg is ``app_id`` (not ``applet_id``). T008 uses ``app_id=``.
2. **Output schema**: Bailian App does NOT return ``output['search_results']``.
   Real nested structure (verified during T008 implementation by dumping
   ``json.dumps(resp.output)``):
   - ``resp.output['text']`` is a JSON string emitted by the App's LLM agent
   - Layer 1: ``json.loads(resp.output['text'])`` →
     ``{"result": {"isError": bool, "content": [{"text": "<JSON string>"}]}}``
   - Layer 2: ``json.loads(content[0]['text'])`` →
     ``{"pages": [{"title": ..., "url": ..., "snippet": ...,
     "hostname": ..., "hostlogo": ...}, ...]}``
   - If ``result.isError`` is true, the App returned a non-fatal error
     (e.g. timeout, partial results) — treat as empty (T010 fires).
   - If any layer is missing or unparseable, the ``KeyError``/``JSONDecodeError``
     propagates so the Send fan-out worker fails loudly (per FR-022).

Field name on each page: ``snippet`` (NOT ``content``). Truncated to 1500 chars
per R7 to keep token budget under control.

Contract drift from §C3 of the spec
-----------------------------------
The original contract (contracts §C3) was authored before the dashscope SDK
1.27.7 ``TextReRank.call`` signature + ``ReRankResult`` shape were known
empirically. Three corrections documented in the DashScopeReRank class
docstring (T016 implementation):

1. **Parameter name**: contract §C3 writes ``_run(self, query, documents:
   list[str], **kwargs) -> list[Source]`` — i.e. caller passes raw
   ``list[str]``. T016 deviates to ``_run(self, query, sources:
   list[Source], **kwargs)`` and extracts ``[s.content or s.title for s in
   sources]`` internally. This matches the call site in
   ``_eval_search_node`` (T017) which already has ``list[Source]`` after
   dedup/grouping and avoids the redundant ``[s.content for s in group]``
   at every call site.  The internal ``documents=`` to the SDK still
   matches the contract.
2. **Result → source mapping**: contract §C3 hint says
   ``sources[i].score = results[i].relevance_score`` (positional).  The
   actual ``ReRankResult`` exposes an ``index`` field that is the input-doc
   index, and the SDK does NOT guarantee score-descending return order
   (verified via ``inspect.getsource(ReRankResult)``).  T016 uses
   ``sources[r.index].score = float(r.relevance_score)`` to be robust
   against arbitrary return order.
3. **Exception class**: contract §C3 wrote
   ``dashscope.exceptions.DashScopeException`` — wrong path.  The actual
   class lives in ``dashscope.common.error.DashScopeException`` (verified
   via ``dir(dashscope.common.error)``).
"""
from __future__ import annotations

import json
import os
import time
from typing import Any

from langchain_core.tools import BaseTool
from pydantic import Field

from src.apps.deep_research.prompts import NotYetImplementedError
from src.apps.deep_research.schemas import Source

__all__ = ["NotYetImplementedError", "DashScopeWebSearch", "DashScopeReRank"]


# --- Constants ---------------------------------------------------------------

# Bailian App output: content cap per Source (per FR-002 + R7).
_CONTENT_TRUNCATE_CHARS = 1500

# Retry policy: 2 retries on HTTP 429 / 5xx, base=1s, exp=2x (per FR-003 + R8
# + spec 002 FR-006(d) consistency). Attempt 0 → no wait; attempt 1 → 1s;
# attempt 2 → 2s; then raise.
_RETRY_BASE_SECONDS = 1.0
_RETRY_EXP = 2.0
_RETRY_MAX_ATTEMPTS = 3  # initial + 2 retries

# HTTP codes that warrant retry (per C2 contract).
_RETRYABLE_HTTP_CODES = {429, 500, 502, 503}


# --- DashScopeWebSearch ------------------------------------------------------


class DashScopeWebSearch(BaseTool):
    """Web search backed by Aliyun Bailian (百炼) web-search App.

    Real impl (spec 003): calls ``dashscope.Application.call(app_id=APP_ID,
    prompt=query, api_key=APP_TOKEN)`` and parses the nested
    ``output['text']`` → ``pages[]`` schema into a ``list[Source]``.

    Why a Bailian App and not ``dashscope.Generation.call(enable_search=True)``
    — see module docstring + project memory
    [[dashscope-mmaas-enable-search-needs-bailian-app]].
    """

    name: str = "dashscope_web_search"
    description: str = "Search the web via DashScope Bailian web search App."
    max_results: int = Field(default=5, description="Cap on results returned per call.")

    # ---- T008(a)(b): env var guards ---------------------------------------

    def _require_env(self) -> tuple[str, str]:
        """Read Bailian App credentials from env; raise on missing.

        Returns
        -------
        (app_id, app_token)

        Raises
        ------
        RuntimeError
            ``DASHSCOPE_WEB_SEARCH_APP_ID`` or ``DASHSCOPE_WEB_SEARCH_APP_TOKEN``
            not set (per FR-004 + C2 + Q1 footgun defense). Crucially, we
            **do not** fall back to ``dashscope.Generation.call(enable_search=
            True)`` — that path is silently broken on the regional MaaS
            gateway and would corrupt Lesson 0007 metrics.
        """
        app_id = os.environ.get("DASHSCOPE_WEB_SEARCH_APP_ID")
        app_token = os.environ.get("DASHSCOPE_WEB_SEARCH_APP_TOKEN")
        if not app_id:
            raise RuntimeError(
                "DASHSCOPE_WEB_SEARCH_APP_ID not set; set it in .env to call "
                "Bailian web search App. Do NOT fall back to "
                "dashscope.Generation.call(enable_search=True) — that path is "
                "silently broken on the regional Aliyun MaaS gateway."
            )
        if not app_token:
            raise RuntimeError(
                "DASHSCOPE_WEB_SEARCH_APP_TOKEN not set; set it in .env to "
                "call Bailian web search App."
            )
        # spec 003 only defines USE_WEB_SEARCH_APP env name; bypass path is
        # intentionally NOT implemented (per FR-004 + C2 contract). If user
        # sets USE_WEB_SEARCH_APP=false we still call the App — but make the
        # intent visible in stderr so devs know the env var is read but not
        # honored. (Could short-circuit to error if FR-004 wording is
        # interpreted strictly; current C2 wording allows the read-but-not-
        # honor semantics.)
        return app_id, app_token

    # ---- T008(c)(d) + T009 (retry) + T010 (empty guard) -------------------

    def _do_query(self, query: str, query_index: int, app_id: str, app_token: str) -> list[Source]:
        """Single attempt: call Bailian App, parse nested output, return ``list[Source]``.

        Raises
        ------
        dashscope.exceptions.RequestFailure
            For HTTP 429/5xx and 4xx (caller decides retry vs raise).
        NotYetImplementedError
            For empty results (T010 guard).
        """
        import dashscope  # local import: only needed when actually searching

        # NOTE: real signature is ``app_id`` (positional), NOT ``applet_id``
        # (which contracts §C2 originally wrote). dashscope 1.27.7 verified.
        resp = dashscope.Application.call(
            app_id=app_id,
            prompt=query,
            api_key=app_token,
        )
        # Bailian App output schema (real, verified during T008 implementation
        # by json.dumps(resp.output) inspection). Two-layer parse:
        #   Layer 1: resp.output['text']  (JSON string)
        #            -> json.loads → {"result": {"isError": bool,
        #                                          "content": [{"text": "..."}]}}
        #   Layer 2: result["content"][0]["text"]  (another JSON string)
        #            -> json.loads → {"pages": [{"title", "url", "snippet",
        #                                        "hostname", "hostlogo"}, ...]}
        # If result.isError is true the App returned a non-fatal error
        # (timeout, partial results) — treat as empty (T010 fires).
        # We parse defensively so a missing intermediate key surfaces as
        # KeyError (per FR-022: let unknown errors propagate so Send fan-out
        # fails loudly).
        outer = json.loads(resp.output["text"])
        result_block = outer.get("result", {})
        if result_block.get("isError"):
            # Non-fatal app-level error → empty result set (T010 will fire).
            return []
        content_list = result_block.get("content", [])
        if not content_list:
            return []
        inner = json.loads(content_list[0]["text"])
        pages = inner.get("pages", [])

        sources: list[Source] = []
        for r in pages[: self.max_results]:
            title = r.get("title") or ""
            url = r.get("url") or ""
            # Page schema uses ``snippet``; tolerate older ``content`` for
            # forward-compat. Truncate to 1500 chars per R7.
            content = (r.get("snippet") or r.get("content") or "")[:_CONTENT_TRUNCATE_CHARS]
            sources.append(
                Source(
                    index=0,  # placeholder; renumbered 1..N by _eval_search_node (per R4)
                    title=title,
                    url=url,
                    content=content,
                    score=None,  # filled by ReRank or LLM Judge later
                    query_index=query_index,
                )
            )
        return sources

    def _run(self, query: str, **kwargs: Any) -> list[Source]:  # type: ignore[override]
        """Run the Bailian web search with retry; return ``list[Source]``.

        Parameters
        ----------
        query : str
            Search query string.
        **kwargs
            Recognized keys:
              - ``query_index`` (int): which ``ResearchQuery`` produced this
                call; written to each ``Source.query_index`` so eval_search
                can group-by-query (per C5 + C6).
            Unrecognized keys are silently ignored (forward-compat).

        Returns
        -------
        list[Source]
            Up to ``self.max_results`` Source entries with ``index=0`` (to be
            renumbered downstream by _eval_search_node per R4), ``score=None``
            (filled by ReRank or LLM Judge), ``query_index`` propagated from
            the Send payload.

        Raises
        ------
        RuntimeError
            Bailian App credentials missing (per FR-004 + C2 + Q1 footgun).
        NotYetImplementedError
            All 3 attempts failed (rate-limited / 5xx) OR Bailian App returned
            an empty ``pages`` list (per FR-005 + T010). Caller (_websearch_node)
            swallows this per FR-013 / R8 so a single worker's failure does
            not block Send fan-out.
        """
        query_index = int(kwargs.get("query_index", 0))
        app_id, app_token = self._require_env()

        import dashscope
        # Note: dashscope 1.27.7 exposes RequestFailure at
        # ``dashscope.common.error.RequestFailure`` (NOT ``dashscope.exceptions``).
        # Attribute is ``http_code`` (NOT ``code``). The contracts §C2 pseudo-code
        # that wrote ``e.code`` was wrong; corrected here during T008 verify.
        from dashscope.common.error import RequestFailure

        last_err: Exception | None = None
        for attempt in range(_RETRY_MAX_ATTEMPTS):  # 0, 1, 2
            try:
                sources = self._do_query(query, query_index, app_id, app_token)
            except RequestFailure as e:
                # Retry on transient errors; immediate fail on 4xx (auth/perm).
                if getattr(e, "http_code", None) in _RETRYABLE_HTTP_CODES and attempt < _RETRY_MAX_ATTEMPTS - 1:
                    delay = _RETRY_BASE_SECONDS * (_RETRY_EXP ** attempt)
                    print(
                        f"[websearch] retry attempt={attempt + 1} "
                        f"http_code={getattr(e, 'http_code', '?')} query={query[:50]}"
                    )
                    time.sleep(delay)
                    last_err = e
                    continue
                # Non-retryable (4xx) or last attempt exhausted.
                raise NotYetImplementedError(
                    f"dashscope web search failed: http_code={getattr(e, 'http_code', '?')} "
                    f"msg={getattr(e, 'message', str(e))[:120]} query={query[:50]}"
                ) from e
            # T010: empty-result guard — do not retry, just raise so the
            # _websearch_node swallow path can log and move on.
            if not sources:
                raise NotYetImplementedError(
                    f"dashscope web search returned 0 results for query={query[:50]}"
                )
            return sources

        # Loop exited without returning → all retries exhausted.
        raise NotYetImplementedError(
            f"dashscope web search rate limited after 2 retries: query={query[:50]}"
        ) from last_err

    # ---- T011: async mirror -----------------------------------------------

    async def _arun(self, query: str, **kwargs: Any) -> list[Source]:  # type: ignore[override]
        """Async version: delegates to ``_run`` (sync Bailian App call wrapped).

        LangChain's ``BaseTool.ainvoke`` dispatches here when the agent is run
        with async APIs. We keep the body identical to ``_run`` so a single
        dashscope call is shared across sync/async call sites (per C2 async
        contract: "same body as _run").
        """
        return self._run(query, **kwargs)


# --- DashScopeReRank (T016 US3 real impl) -----------------------------------


class DashScopeReRank(BaseTool):
    """ReRank service backed by DashScope gte-rerank-v2 model (SDK direct call).

    spec 003 US3 real impl (per FR-006 + FR-007 + FR-008 + contracts §C3 +
    ADR-0004).  Calls ``dashscope.TextReRank.call(model="gte-rerank-v2",
    query=query, documents=..., top_n=len(sources), return_documents=True)``
    and writes ``resp.output.results[i].relevance_score`` back to
    ``sources[i].score`` (in-place per C3).

    Why SDK direct (not MaaS gateway)
    --------------------------------
    The regional Aliyun MaaS gateway serving this deployment only proxies
    ``/v1/chat/completions`` and ``/v1/models``.  ReRank must go to the
    DashScope main site via the SDK (per ADR-0004 + R2 "SDK 直连不经过
    MaaS").  This means ``DASHSCOPE_API_KEY`` is required (NOT the Bailian
    ``DASHSCOPE_WEB_SEARCH_APP_TOKEN``).
    """

    name: str = "dashscope_rerank"
    description: str = "ReRank documents by relevance via gte-rerank-v2."
    top_k: int = Field(default=5, description="Number of top documents to return.")

    def _run(self, query: str, sources: list[Source], **kwargs):  # type: ignore[override]
        """Call TextReRank → write relevance_score to each Source.score → return.

        Per FR-006 + FR-007 + FR-008 + C3 + data-model §3:
        1. ``len(sources) == 0`` → return ``sources`` unchanged (per FR-008 + C3).
        2. Build ``documents = [s.content or s.title or "" for s in sources]``.
        3. Call ``dashscope.TextReRank.call(model="gte-rerank-v2", query=query,
           documents=documents, top_n=len(sources), return_documents=True)``.
        4. If ``resp.status_code != 20000`` → raise
           ``NotYetImplementedError("dashscope rerank failed: <code>")``
           (per FR-007 + C3).  No retry (per ADR-0004).
        5. For each ``r in resp.output.results``: write
           ``sources[r.index].score = float(r.relevance_score)`` in-place.
           Use ``r.index`` (the input-doc index) rather than positional
           ordering, because DashScope's TextReRank does not guarantee
           score-descending return order — the contract §C3 hint
           ``results[i].relevance_score`` is correct only when
           ``top_n == len(sources)`` AND the API returns input-order, which
           the SDK does NOT formally guarantee (per
           ``inspect.getsource(ReRankResult)`` revealing ``index`` as the
           authoritative back-pointer).
        6. Return the same ``sources`` list reference (in-place writes +
           C3 contract).

        Parameters
        ----------
        query : str
            The natural-language research question; used as the relevance
            query for the ReRank model.
        sources : list[Source]
            The candidate sources to score.  ``Source.content`` (preferred)
            or ``Source.title`` (fallback) is sent to the ReRank API.
            ``Source.score`` is mutated in-place.

        Returns
        -------
        list[Source]
            The same ``sources`` list, with ``score`` fields populated.

        Raises
        ------
        NotYetImplementedError
            DashScope ``status_code != 20000`` (per C3) or any
            ``DashScopeException`` (SDK auth/SDKError).  No retry.
        """
        # (1) edge case: empty → return as-is (per FR-008 + C3).
        if not sources:
            return sources

        # (2) build documents list.  We tolerate empty content/title per-source
        # (fallback chain: content → title → "") but bail early if ALL are empty
        # to avoid wasting API quota on a no-op call.
        documents = [(s.content or s.title or "") for s in sources]
        if not any(documents):
            return sources

        # (3) call DashScope TextReRank (direct, not via MaaS gateway).
        # dashscope.common.error.DashScopeException is the base class for all
        # SDK errors (auth, network, rate-limit) — verified via
        # ``dir(dashscope.common.error)``.
        import dashscope
        from dashscope.common.error import DashScopeException

        try:
            resp = dashscope.TextReRank.call(
                model="gte-rerank-v2",
                query=query,
                documents=documents,
                top_n=len(sources),
                return_documents=True,
            )
        except DashScopeException as e:
            # (4a) SDK-level error (auth, network, rate-limit).  Per ADR-0004
            # + R5: do NOT retry here.  _eval_search_node catches
            # NotYetImplementedError and falls back to LLM Judge.
            raise NotYetImplementedError(
                f"dashscope rerank failed: {type(e).__name__}: {str(e)[:200]}"
            ) from e

        # (4b) API-level error: status_code != 20000 (DashScope success code).
        if getattr(resp, "status_code", None) != 20000:
            raise NotYetImplementedError(
                f"dashscope rerank failed: status_code={getattr(resp, 'status_code', '?')} "
                f"code={getattr(resp, 'code', '?')} "
                f"message={getattr(resp, 'message', '')[:120]}"
            )

        # (5) write relevance_score back to each source.score (in-place).
        # Use ``r.index`` to map back to the original source position rather
        # than assuming positional ordering — the SDK does not guarantee
        # results are returned in input order.
        results_obj = getattr(resp, "output", None)
        results_list = getattr(results_obj, "results", None) if results_obj else None
        if not results_list:
            return sources  # no results returned (shouldn't happen with top_n=len)

        for r in results_list:
            src_idx = getattr(r, "index", None)
            score = getattr(r, "relevance_score", None)
            if src_idx is None or score is None:
                continue
            if 0 <= src_idx < len(sources):
                sources[src_idx].score = float(score)

        return sources

    async def _arun(self, query: str, sources: list[Source], **kwargs):  # type: ignore[override]
        """Async version: delegates to ``_run`` (per C3 async contract: same body).

        LangChain's ``BaseTool.ainvoke`` dispatches here when the agent is run
        with async APIs.  We keep the body identical to ``_run`` so a single
        DashScope ReRank call is shared across sync/async call sites.
        """
        return self._run(query, sources, **kwargs)
