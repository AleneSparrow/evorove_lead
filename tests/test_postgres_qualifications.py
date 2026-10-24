"""Migration 0009 on the local Postgres from .env.example, port 5432."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text

LOCAL_URL = "postgresql+psycopg://evorove_lead:local_development_only@127.0.0.1:5432/evorove_lead"


def test_postgres_has_qualification_tables_after_migrate():
    try:
        engine = create_engine(LOCAL_URL, future=True)
        with engine.connect() as connection:
            names = set(
                connection.execute(
                    text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
                ).scalars()
            )
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"local postgres is not reachable: {exc}")
    assert {"lead_qualifications", "lead_feedback", "llm_usage"} <= names
