"""DashScope integration stubs (per FR-010).

Both classes are ``BaseTool`` subclasses; their ``_run`` method raises
``NotYetImplementedError`` (re-exported from ``prompts``) until spec 003
replaces the bodies with real ``dashscope.Generation.call(enable_search=True)``
and ``dashscope.TextReRank.call(model='gte-rerank-v2')`` invocations.
"""
from __future__ import annotations

from langchain_core.tools import BaseTool
from pydantic import Field

from src.apps.deep_research.prompts import NotYetImplementedError

__all__ = ["NotYetImplementedError", "DashScopeWebSearch", "DashScopeReRank"]


class DashScopeWebSearch(BaseTool):
    """Stub web-search tool backed by DashScope's enable_search parameter.

    Foundation spec: ``_run`` raises ``NotYetImplementedError`` on every call.
    Spec 003 replaces with a real ``dashscope.Generation.call(...)`` call.
    """

    name: str = "dashscope_web_search"
    description: str = "Search the web via DashScope (Qwen with enable_search=True)."
    max_results: int = Field(default=5, description="Cap on results returned per call.")

    def _run(self, query: str, **kwargs) -> list:  # type: ignore[override]
        raise NotYetImplementedError(
            f"DashScopeWebSearch not yet implemented in foundation spec: query={query[:50]}"
        )

    async def _arun(self, query: str, **kwargs) -> list:  # type: ignore[override]
        raise NotYetImplementedError(
            f"DashScopeWebSearch not yet implemented in foundation spec: query={query[:50]}"
        )


class DashScopeReRank(BaseTool):
    """Stub rerank tool backed by DashScope's gte-rerank-v2 model.

    Foundation spec: ``_run`` raises ``NotYetImplementedError`` on every call.
    Spec 003 replaces with a real ``dashscope.TextReRank.call(...)`` call.
    """

    name: str = "dashscope_rerank"
    description: str = "Rerank documents by relevance to a query via DashScope gte-rerank-v2."
    top_k: int = Field(default=5, description="Number of top documents to return.")

    def _run(self, query: str, documents: list[str], **kwargs) -> list:  # type: ignore[override]
        raise NotYetImplementedError(
            f"DashScopeReRank not yet implemented in foundation spec: query={query[:50]}"
        )

    async def _arun(self, query: str, documents: list[str], **kwargs) -> list:  # type: ignore[override]
        raise NotYetImplementedError(
            f"DashScopeReRank not yet implemented in foundation spec: query={query[:50]}"
        )
