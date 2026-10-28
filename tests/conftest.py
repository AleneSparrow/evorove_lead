"""Keep the suite off whatever database or provider the shell exported.

A leaked ``DATABASE_URL`` makes engine tests share one Postgres and then
reject a contact an earlier test already stored. Tests that need a
database set the variable themselves.
"""

from __future__ import annotations

import os
import pytest

_ISOLATED_ENV = (
    "DATABASE_URL",
    "DATABASE_PUBLIC_URL",
    "CRM_BASE_URL",
    "INTERNAL_TASK_SECRET",
    "WEB_SEARCH_BASE_URL",
    "AI_PROVIDER",
    "CRM_CYCLE2_ISOLATED",
    "CRM_LIVE_WRITE_ACK",
    "EVOROVE_CLIENT_ZERO_BUSINESS_ID",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_MODEL",
)


@pytest.fixture(autouse=True)
def _isolate_external_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in set(_ISOLATED_ENV) | {key for key in os.environ if key.startswith("RESEARCH_")}:
        monkeypatch.delenv(name, raising=False)
