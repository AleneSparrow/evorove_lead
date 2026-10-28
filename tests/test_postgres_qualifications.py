"""Check qualification tables in the explicitly enabled disposable PostgreSQL."""

from __future__ import annotations

import os
import pytest
from sqlalchemy import create_engine, text

pytestmark = pytest.mark.skipif(os.getenv("EVOROVE_TEST_POSTGRES") != "1", reason="isolated PostgreSQL not enabled")


def test_postgres_has_qualification_tables_after_migrate():
    url = os.getenv("EVOROVE_TEST_POSTGRES_URL", "postgresql+psycopg://evorove_test@127.0.0.1:55439/evorove_test")
    engine = create_engine(url, future=True)
    with engine.connect() as connection:
        names = set(
            connection.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
            ).scalars()
        )
    engine.dispose()
    assert {"lead_qualifications", "lead_feedback", "llm_usage"} <= names
