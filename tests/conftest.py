"""Shared test fixtures."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import pytest_asyncio

from pipeline.state import StateStore


@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    return tmp_path / "test.db"


@pytest.fixture
def store(tmp_db: Path) -> StateStore:
    return StateStore(db_path=tmp_db)


@pytest_asyncio.fixture
async def initialized_store(store: StateStore) -> StateStore:
    await store.init()
    return store


SAMPLE_INCIDENT = {
    "issue_number": 42,
    "issue_url": "https://github.com/cognition-demo/superset/issues/42",
    "repo": "cognition-demo/superset",
    "failing_tests": [
        "tests/unit_tests/extensions/test_tenant_embed.py::test_tenant_rls_rule_is_schema_compatible",
    ],
    "upstream_commits": [
        "0fd244b fix(security): reject unknown fields on guest-token RLS rules (#41217)",
    ],
    "created_at": "2025-01-15T10:00:00+00:00",
}
