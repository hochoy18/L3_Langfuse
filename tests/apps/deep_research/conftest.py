"""Shared pytest fixtures for the deep_research foundation spec.

The foundation spec targets Postgres (per ADR-0005). When the langfuse-deploy
Postgres is not reachable, integration tests that require it MUST skip cleanly
rather than fail. This conftest provides:

- ``langgraph_db_uri`` — read from ``LANGGRAPH_CHECKPOINT_DB`` or use the ADR-0005
  default; tests that need a real DB use this fixture.
- ``require_postgres`` — fixture that probes the DB; skips the test if unreachable.
- ``fresh_thread_id`` — uuid4 string per test for checkpoint isolation.
- ``checkpoints_clean`` — drops + recreates the ``checkpoints`` table around a test
  (used by SC-004 auto-create verification).
"""
from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import psycopg
import pytest

# ADR-0005 default connection string. Override via LANGGRAPH_CHECKPOINT_DB env var.
DEFAULT_DB_URI = "postgresql://postgres:postgres@localhost:5432/postgres"


def _normalize_pg_uri(uri: str) -> str:
    """Strip SQLAlchemy driver suffix (e.g. ``+asyncpg``) from a Postgres URI.

    See ``src/apps/deep_research/graph.py::_normalize_pg_uri`` for rationale.
    """
    import re

    return re.sub(r"^postgresql\+\w+://", "postgresql://", uri)


@pytest.fixture(scope="session")
def langgraph_db_uri() -> str:
    """Return the Postgres URI to use for the deep_research checkpointer."""
    raw = os.environ.get("LANGGRAPH_CHECKPOINT_DB", DEFAULT_DB_URI)
    return _normalize_pg_uri(raw)


@pytest.fixture(scope="session")
def require_postgres(langgraph_db_uri: str) -> None:
    """Skip the test if Postgres is not reachable on the configured URI."""
    try:
        with psycopg.connect(langgraph_db_uri, connect_timeout=2) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Postgres not reachable at {langgraph_db_uri}: {exc}")


@pytest.fixture
def fresh_thread_id() -> str:
    """A unique thread_id per test for checkpoint isolation."""
    return f"test-{uuid.uuid4()}"


@pytest.fixture
def checkpoints_clean(langgraph_db_uri: str, require_postgres: None) -> Iterator[None]:
    """Drop ALL langgraph checkpoint tables before the test, restore after.

    Used by SC-004 verification (auto-create from a fresh DB). The test sees an
    empty schema; cleanup is best-effort.

    Why ALL tables (not just ``checkpoints``): ``PostgresSaver.setup()`` reads
    ``checkpoint_migrations`` to decide which migrations to run. If only
    ``checkpoints`` is dropped but migration v=1 is still recorded, setup()
    will skip re-creating the table, breaking SC-004 verification.
    """
    tables = [
        "checkpoint_writes",
        "checkpoint_blobs",
        "checkpoints",
        "checkpoint_migrations",
    ]
    drop_sql = "DROP TABLE IF EXISTS " + ", ".join(tables) + " CASCADE"
    with psycopg.connect(langgraph_db_uri) as conn:
        with conn.cursor() as cur:
            cur.execute(drop_sql)
        conn.commit()
    try:
        yield
    finally:
        with psycopg.connect(langgraph_db_uri) as conn:
            with conn.cursor() as cur:
                cur.execute(drop_sql)
            conn.commit()
