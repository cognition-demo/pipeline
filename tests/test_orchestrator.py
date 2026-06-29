"""Tests for pipeline.orchestrator."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from pipeline.devin import ReplayDevinClient, SessionPoll
from pipeline.orchestrator import _IncidentProxy, run_session
from pipeline.state import StateStore

from .conftest import SAMPLE_INCIDENT


class TestIncidentProxy:
    def test_reads_dict_fields(self):
        d = {
            "failing_tests": ["t1", "t2"],
            "upstream_commits": ["abc"],
            "repo": "org/repo",
        }
        proxy = _IncidentProxy(d)
        assert proxy.failing_tests == ["t1", "t2"]
        assert proxy.upstream_commits == ["abc"]
        assert proxy.repo == "org/repo"

    def test_defaults_for_missing_keys(self):
        proxy = _IncidentProxy({})
        assert proxy.failing_tests == []
        assert proxy.upstream_commits == []
        assert proxy.repo == ""


@pytest.mark.asyncio
class TestRunSession:
    async def test_completes_session(self, store: StateStore):
        await store.init()
        incident_id = await store.save_incident(SAMPLE_INCIDENT)

        client = ReplayDevinClient(speed_multiplier=1000.0)

        with patch("pipeline.orchestrator.console"):
            await run_session(incident_id, SAMPLE_INCIDENT, client, store)

        rows = await store.get_all()
        session_row = [r for r in rows if r["session_id"] is not None]
        assert len(session_row) == 1
        assert session_row[0]["status"] == "completed"
        assert session_row[0]["pr_url"] is not None

    async def test_session_without_pr(self, store: StateStore):
        await store.init()
        incident_id = await store.save_incident(SAMPLE_INCIDENT)

        # Create a mock client that returns done but no PR
        client = AsyncMock()
        client.create_session = AsyncMock(return_value="mock-sess-001")
        client.poll = AsyncMock(
            return_value=SessionPoll(
                status="stopped", output="Completed but no PR", done=True, pr_url=None
            )
        )

        with patch("pipeline.orchestrator.console"):
            await run_session(incident_id, SAMPLE_INCIDENT, client, store)

        rows = await store.get_all()
        session_row = [r for r in rows if r["session_id"] is not None]
        assert len(session_row) == 1
        assert session_row[0]["status"] == "completed"
        assert session_row[0]["pr_url"] is None

    async def test_polls_until_done(self, store: StateStore):
        await store.init()
        incident_id = await store.save_incident(SAMPLE_INCIDENT)

        # Returns running twice, then done
        poll_results = [
            SessionPoll(status="running", output="step 1", done=False),
            SessionPoll(status="running", output="step 2", done=False),
            SessionPoll(
                status="stopped",
                output="Done. PR: https://github.com/org/repo/pull/1",
                done=True,
                pr_url="https://github.com/org/repo/pull/1",
            ),
        ]
        client = AsyncMock()
        client.create_session = AsyncMock(return_value="poll-sess-001")
        client.poll = AsyncMock(side_effect=poll_results)

        with patch("pipeline.orchestrator.console"):
            await run_session(incident_id, SAMPLE_INCIDENT, client, store)

        assert client.poll.call_count == 3
