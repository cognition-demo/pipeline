"""Tests for pipeline.state (SQLite-backed state store)."""
from __future__ import annotations

import pytest

from pipeline.state import StateStore

from .conftest import SAMPLE_INCIDENT


@pytest.fixture
async def initialized_store(store: StateStore) -> StateStore:
    await store.init()
    return store


@pytest.mark.asyncio
class TestStateStoreInit:
    async def test_creates_tables(self, store: StateStore):
        await store.init()
        import aiosqlite

        async with aiosqlite.connect(store._path) as db:
            rows = await (
                await db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                )
            ).fetchall()
        table_names = [r[0] for r in rows]
        assert "incidents" in table_names
        assert "sessions" in table_names

    async def test_init_is_idempotent(self, store: StateStore):
        await store.init()
        await store.init()  # should not raise


@pytest.mark.asyncio
class TestSaveIncident:
    async def test_returns_row_id(self, initialized_store: StateStore):
        row_id = await initialized_store.save_incident(SAMPLE_INCIDENT)
        assert isinstance(row_id, int)
        assert row_id >= 1

    async def test_multiple_inserts_increment_id(self, initialized_store: StateStore):
        id1 = await initialized_store.save_incident(SAMPLE_INCIDENT)
        id2 = await initialized_store.save_incident(SAMPLE_INCIDENT)
        assert id2 == id1 + 1

    async def test_default_created_at(self, initialized_store: StateStore):
        incident_no_date = {
            "issue_number": 1,
            "issue_url": "https://example.com/issues/1",
            "repo": "org/repo",
            "failing_tests": [],
            "upstream_commits": [],
        }
        row_id = await initialized_store.save_incident(incident_no_date)
        assert row_id >= 1


@pytest.mark.asyncio
class TestUpsertSession:
    async def test_insert_new_session(self, initialized_store: StateStore):
        await initialized_store.save_incident(SAMPLE_INCIDENT)
        session = {
            "incident_id": 1,
            "session_id": "sess-abc",
            "status": "running",
            "pr_url": None,
            "devin_summary": None,
            "started_at": "2025-01-15T10:00:00+00:00",
            "completed_at": None,
        }
        await initialized_store.upsert_session(session)
        rows = await initialized_store.get_all()
        assert any(r["session_id"] == "sess-abc" for r in rows)

    async def test_update_existing_session(self, initialized_store: StateStore):
        await initialized_store.save_incident(SAMPLE_INCIDENT)
        session = {
            "incident_id": 1,
            "session_id": "sess-xyz",
            "status": "running",
            "pr_url": None,
            "devin_summary": None,
            "started_at": "2025-01-15T10:00:00+00:00",
            "completed_at": None,
        }
        await initialized_store.upsert_session(session)

        # Update it
        session["status"] = "completed"
        session["pr_url"] = "https://github.com/org/repo/pull/99"
        session["completed_at"] = "2025-01-15T10:05:00+00:00"
        await initialized_store.upsert_session(session)

        rows = await initialized_store.get_all()
        matching = [r for r in rows if r["session_id"] == "sess-xyz"]
        assert len(matching) == 1
        assert matching[0]["status"] == "completed"
        assert matching[0]["pr_url"] == "https://github.com/org/repo/pull/99"


@pytest.mark.asyncio
class TestGetAll:
    async def test_empty_store(self, initialized_store: StateStore):
        rows = await initialized_store.get_all()
        assert rows == []

    async def test_incident_without_session(self, initialized_store: StateStore):
        await initialized_store.save_incident(SAMPLE_INCIDENT)
        rows = await initialized_store.get_all()
        assert len(rows) == 1
        assert rows[0]["issue_number"] == 42
        assert rows[0]["session_id"] is None

    async def test_failing_tests_deserialized(self, initialized_store: StateStore):
        await initialized_store.save_incident(SAMPLE_INCIDENT)
        rows = await initialized_store.get_all()
        assert isinstance(rows[0]["failing_tests"], list)
        assert len(rows[0]["failing_tests"]) == 1

    async def test_ordered_desc(self, initialized_store: StateStore):
        incident2 = {**SAMPLE_INCIDENT, "issue_number": 100}
        await initialized_store.save_incident(SAMPLE_INCIDENT)
        await initialized_store.save_incident(incident2)
        rows = await initialized_store.get_all()
        assert rows[0]["issue_number"] == 100
        assert rows[1]["issue_number"] == 42


@pytest.mark.asyncio
class TestReset:
    async def test_clears_all_data(self, initialized_store: StateStore):
        await initialized_store.save_incident(SAMPLE_INCIDENT)
        await initialized_store.upsert_session({
            "incident_id": 1,
            "session_id": "sess-reset",
            "status": "running",
            "started_at": None,
            "completed_at": None,
        })
        await initialized_store.reset()
        rows = await initialized_store.get_all()
        assert rows == []
