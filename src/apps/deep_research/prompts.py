"""Langfuse Prompt Management stubs (per FR-009).

Per the foundation spec, these functions raise ``NotYetImplementedError`` so
that subsequent specs (002-005) can catch the call uniformly and fall back to
hardcoded values. Re-exported by ``tools.py``.
"""
from __future__ import annotations

from typing import Literal


class NotYetImplementedError(RuntimeError):
    """Raised by foundation-spec stubs to signal 'replace me in a later spec'.

    Inherits from ``RuntimeError`` (not ``NotImplementedError``) so callers can
    distinguish 'this code path isn't wired yet' from 'abstract method must be
    overridden'. Downstream specs ``except NotYetImplementedError: ...`` to
    fall back to hardcoded values.
    """

    pass


def get_prompt(name: str, label: str | None = None) -> str:
    """Stub: return a Langfuse prompt by name and optional label.

    Foundation spec raises. Spec 002 replaces with ``langfuse.get_prompt(...)``
    that returns the resolved prompt text.
    """
    raise NotYetImplementedError(
        f"get_prompt not yet implemented in foundation spec: {name}"
    )


def get_llm(role: Literal["plan", "research", "write", "critic", "polish"]):
    """Stub: return a ChatOpenAI (or compatible) instance for the named role.

    Foundation spec raises. Spec 002 replaces with Qwen-via-DashScope
    ChatOpenAI configured for the role's model + temperature.
    """
    raise NotYetImplementedError(
        f"get_llm not yet implemented in foundation spec: {role}"
    )


__all__ = ["NotYetImplementedError", "get_prompt", "get_llm"]
