"""Langfuse Prompt Management + LLM factory (spec 002 FR-004 + FR-005).

Replaces the foundation stubs with real Langfuse + DashScope integrations.
``NotYetImplementedError`` is kept for ``get_llm`` roles that future specs
(003, 004) will implement.

Why chevron
-----------
Langfuse 4.x ``TextPromptClient.compile(**kwargs)`` performs simple
``{{var}}`` substitution but does NOT parse Mustache sections
(``{{#if user_feedback}}...{{/if}}``).  We render the Mustache template with
``chevron`` client-side so prompts can use the full Mustache language while
still being authored in Langfuse.
"""
from __future__ import annotations

import os
from typing import Any, Literal

import chevron
from langchain_openai import ChatOpenAI


class NotYetImplementedError(RuntimeError):
    """Raised by foundation-spec stubs to signal 'replace me in a later spec'.

    Inherits from ``RuntimeError`` (not ``NotImplementedError``) so callers can
    distinguish 'this code path isn't wired yet' from 'abstract method must be
    overridden'. Downstream specs ``except NotYetImplementedError: ...`` to
    fall back to hardcoded values.
    """

    pass


def _render_mustache(template: str, **vars: Any) -> str:
    """Render a Mustache template with chevron (supports sections like ``{{#user_feedback}}``).

    Langfuse's ``text``-type ``compile()`` only does ``{{var}}`` substitution
    and silently leaves section tags (``{{#if}}`` / ``{{/if}}``) untouched. We
    fall back to chevron so authors can use the full Mustache language.
    """
    return chevron.render(template, vars)


def get_prompt(name: str, label: str | None = None):
    """Fetch a Langfuse prompt by name and optional label (per FR-004 + contract §C1).

    Parameters
    ----------
    name : str
        Prompt name as registered in Langfuse UI (e.g. ``"deep-research-plan"``).
    label : str | None
        Label filter (e.g. ``"production"``, ``"staging"``); ``None`` returns
        the latest version.

    Returns
    -------
    langfuse.TextPromptClient
        Prompt object with ``.prompt`` (raw template), ``.config`` (JSON dict),
        ``.compile(**kwargs)`` (Langfuse simple ``{{var}}`` substitution),
        ``.get_langchain_prompt(**kwargs)`` (alias for ``compile``).

    Raises
    ------
    RuntimeError
        ``LANGFUSE_PUBLIC_KEY`` / ``LANGFUSE_SECRET_KEY`` / ``LANGFUSE_HOST``
        (or ``LANGFUSE_BASE_URL``) missing from environment.
    langfuse.errors.*
        Prompt not found / network error / auth failure — propagated as-is
        per FR-004 (NOT wrapped in ``NotYetImplementedError``).
    """
    from langfuse import get_client  # local import: env-must-be-set first

    # Validate env at call time (not at import time) so unit tests can patch
    # env without triggering this. Same semantics as FR-004 + C1 TC-3.
    if not os.environ.get("LANGFUSE_PUBLIC_KEY") or not os.environ.get("LANGFUSE_SECRET_KEY"):
        raise RuntimeError(
            "LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY not set in environment"
        )
    if not (
        os.environ.get("LANGFUSE_HOST") or os.environ.get("LANGFUSE_BASE_URL")
    ):
        raise RuntimeError(
            "LANGFUSE_HOST (or LANGFUSE_BASE_URL) not set in environment"
        )

    lf = get_client()
    return lf.get_prompt(name, label=label)


def get_llm(
    role: Literal["plan", "research", "write", "critic", "polish"],
) -> ChatOpenAI:
    """Return a ``ChatOpenAI`` instance for the named role (per FR-005 + contract §C2).

    spec 002 implements ``role="plan"`` (DashScope OpenAI-compatible qwen-plus,
    temperature=0.4).
    spec 003 implements ``role="research"`` (same model, temperature=0.2 — lower
    for query diversity / focus; per [Q3] clarification).
    Other roles raise ``NotYetImplementedError`` for spec 004 to fill in.

    Parameters
    ----------
    role : Literal["plan", "research", "write", "critic", "polish"]
        Logical LLM role. ``"plan"`` + ``"research"`` are wired.

    Returns
    -------
    langchain_openai.ChatOpenAI
        For ``role="plan"``: ``temperature=0.4``.
        For ``role="research"``: ``temperature=0.2``.

    Raises
    ------
    NotYetImplementedError
        ``role in {"write", "critic", "polish"}`` (spec 004).
    RuntimeError
        ``DASHSCOPE_API_KEY`` not set in environment.
    """
    if role in ("plan", "research"):
        api_key = os.environ.get("DASHSCOPE_API_KEY")
        if not api_key:
            raise RuntimeError(
                "DASHSCOPE_API_KEY not set; set it in .env to call the LLM"
            )
        # Per spec 002 research §R3: default to the public OpenAI-compatible
        # DashScope endpoint. ``DASHSCOPE_API_BASE_URL`` overrides for regional
        # gateways or self-hosted proxies.
        base_url = os.environ.get(
            "DASHSCOPE_API_BASE_URL",
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
        )
        # spec 002 originally hardcoded ``qwen-plus-2025-04-28`` (per research
        # §R3), but the regional Aliyun MaaS instance serving this deployment
        # only exposes qwen3.x / glm-5.x / deepseek-v4.x. ``DASHSCOPE_MODEL``
        # lets the deployer pin the actual model; default keeps the spec value
        # so public DashScope works out-of-the-box.
        model = os.environ.get("DASHSCOPE_MODEL", "qwen3.7-plus")
        # spec 003 [Q3]: research LLM uses lower temperature (0.2 vs plan's 0.4)
        # to keep query generation focused and avoid LLM "creativity" on search
        # query strings.
        temperature = 0.2 if role == "research" else 0.4
        return ChatOpenAI(
            model=model,
            api_key=api_key,
            base_url=base_url,
            temperature=temperature,
        )

    # write / critic / polish deferred to spec 004
    raise NotYetImplementedError(f"get_llm not yet implemented: {role}")


__all__ = [
    "NotYetImplementedError",
    "get_prompt",
    "get_llm",
    "_render_mustache",
]
