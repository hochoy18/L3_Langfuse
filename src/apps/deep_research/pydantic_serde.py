"""Custom serializer that adds Pydantic support to LangGraph checkpointing.

LangGraph's default ``JsonPlusSerializer`` uses msgpack to serialize state
snapshots to Postgres. Pydantic ``BaseModel`` instances are not msgpack-
serializable, so checkpointing fails with::

    TypeError: Type is not msgpack serializable: Source

This module provides :class:`PydanticJsonPlusSerializer` which transparently
calls ``model_dump()`` on any Pydantic model encountered during serialization,
and reconstructs the model on the way back via ``model_validate``.

The serde is injected into :class:`PostgresSaver` from :mod:`graph`.
"""
from __future__ import annotations

from typing import Any

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from pydantic import BaseModel


class PydanticJsonPlusSerializer(JsonPlusSerializer):
    """JsonPlusSerializer extension that round-trips Pydantic models.

    On ``dumps_typed``: walks the object and converts every Pydantic
    ``BaseModel`` to a plain dict via ``model_dump()`` before msgpack encoding.
    On ``loads_typed``: cannot reverse the conversion (the type tag is not
    preserved by msgpack), so values come back as dicts. Downstream code that
    expects Pydantic models should call ``cls.model_validate(d)`` explicitly,
    or rely on the in-memory state which is never re-loaded from disk for the
    foundation spec.
    """

    def _pydantic_to_dict(self, obj: Any) -> Any:
        if isinstance(obj, BaseModel):
            # ``mode="json"`` ensures ``HttpUrl`` and other constrained string
            # types are emitted as plain strings rather than ``Url`` objects
            # (which msgpack cannot encode).
            return obj.model_dump(mode="json")
        if isinstance(obj, dict):
            return {k: self._pydantic_to_dict(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            converted = [self._pydantic_to_dict(v) for v in obj]
            return type(obj)(converted) if isinstance(obj, tuple) else converted
        return obj

    def dumps_typed(self, obj: Any) -> tuple[str, bytes]:
        # Convert any Pydantic instances (possibly nested in dicts/lists) to
        # dicts so the default msgpack encoder can serialize them.
        return super().dumps_typed(self._pydantic_to_dict(obj))
