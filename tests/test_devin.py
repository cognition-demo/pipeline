"""Tests for pipeline.devin (Devin API client — live and replay modes)."""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pipeline.devin import (
    LiveDevinClient,
    ReplayDevinClient,
    SessionPoll,
    _extract_pr_url,
    make_client,
)


class TestExtractPrUrl:
    def test_extracts_valid_pr_url(self):
        text = "Done. PR: https://github.com/org/repo/pull/43 opened."
        assert _extract_pr_url(text) == "https://github.com/org/repo/pull/43"

    def test_returns_none_for_empty(self):
        assert _extract_pr_url(None) is None
        assert _extract_pr_url("") is None

    def test_returns_none_when_no_match(self):
        assert _extract_pr_url("No PR here, just text") is None

    def test_extracts_first_url_from_multiple(self):
        text = (
            "See https://github.com/org/repo/pull/1 and "
            "https://github.com/org/repo/pull/2"
        )
        assert _extract_pr_url(text) == "https://github.com/org/repo/pull/1"

    def test_does_not_match_non_github_urls(self):
        text = "See https://gitlab.com/org/repo/pull/5"
        assert _extract_pr_url(text) is None


class TestSessionPoll:
    def test_attributes(self):
        poll = SessionPoll(status="running", output="Working...", done=False, pr_url=None)
        assert poll.status == "running"
        assert poll.output == "Working..."
        assert poll.done is False
        assert poll.pr_url is None

    def test_done_with_pr(self):
        poll = SessionPoll(
            status="stopped",
            output="Done",
            done=True,
            pr_url="https://github.com/org/repo/pull/10",
        )
        assert poll.done is True
        assert poll.pr_url == "https://github.com/org/repo/pull/10"


class TestMakeClient:
    def test_replay_mode(self):
        client = make_client("replay")
        assert isinstance(client, ReplayDevinClient)

    def test_live_mode_without_key_raises(self):
        with patch.dict(os.environ, {"DEVIN_API_KEY": ""}, clear=False):
            with pytest.raises(ValueError, match="DEVIN_API_KEY"):
                make_client("live")

    def test_live_mode_with_key(self):
        with patch.dict(os.environ, {"DEVIN_API_KEY": "test-key"}, clear=False):
            client = make_client("live")
            assert isinstance(client, LiveDevinClient)


class TestReplayDevinClient:
    @pytest.mark.asyncio
    async def test_create_session_returns_id(self):
        client = ReplayDevinClient(speed_multiplier=1000.0)

        class FakeIncident:
            failing_tests = ["test_a"]
            upstream_commits = ["abc fix"]
            repo = "org/repo"

        sid = await client.create_session(FakeIncident())
        assert sid == "replay-session-0001"

    @pytest.mark.asyncio
    async def test_create_multiple_sessions(self):
        client = ReplayDevinClient(speed_multiplier=1000.0)

        class FakeIncident:
            failing_tests = []
            upstream_commits = []
            repo = "org/repo"

        s1 = await client.create_session(FakeIncident())
        s2 = await client.create_session(FakeIncident())
        assert s1 == "replay-session-0001"
        assert s2 == "replay-session-0002"

    @pytest.mark.asyncio
    async def test_poll_advances_through_fixture(self):
        client = ReplayDevinClient(speed_multiplier=1000.0)

        class FakeIncident:
            failing_tests = ["test_a"]
            upstream_commits = ["abc fix"]
            repo = "org/repo"

        sid = await client.create_session(FakeIncident())

        polls = []
        for _ in range(20):
            p = await client.poll(sid)
            polls.append(p)
            if p.done:
                break

        assert polls[-1].done is True
        assert polls[-1].pr_url is not None
        assert "pull/" in polls[-1].pr_url

    @pytest.mark.asyncio
    async def test_poll_stays_on_last_frame(self):
        client = ReplayDevinClient(speed_multiplier=1000.0)

        class FakeIncident:
            failing_tests = []
            upstream_commits = []
            repo = "org/repo"

        sid = await client.create_session(FakeIncident())

        for _ in range(50):
            p = await client.poll(sid)
            if p.done:
                break

        # Polling again stays on last frame
        p2 = await client.poll(sid)
        assert p2.done is True


class TestLiveDevinClient:
    @pytest.mark.asyncio
    async def test_create_session_formats_prompt(self):
        client = LiveDevinClient(api_key="test-key")

        class FakeIncident:
            failing_tests = ["test_schema"]
            upstream_commits = ["0fd244b fix(security)"]
            repo = "org/superset"

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"session_id": "live-001"}
        mock_resp.raise_for_status = MagicMock()

        mock_client_instance = AsyncMock()
        mock_client_instance.post = AsyncMock(return_value=mock_resp)
        mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client_instance.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client_instance):
            sid = await client.create_session(FakeIncident())

        assert sid == "live-001"
        call_args = mock_client_instance.post.call_args
        body = call_args.kwargs.get("json") or call_args[1].get("json")
        assert "test_schema" in body["prompt"]
        assert "org/superset" in body["prompt"]

    @pytest.mark.asyncio
    async def test_poll_returns_session_poll(self):
        client = LiveDevinClient(api_key="test-key")

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "status_enum": "stopped",
            "structured_output": "Fixed. PR: https://github.com/org/repo/pull/7",
        }
        mock_resp.raise_for_status = MagicMock()

        mock_client_instance = AsyncMock()
        mock_client_instance.get = AsyncMock(return_value=mock_resp)
        mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client_instance.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client_instance):
            poll = await client.poll("session-123")

        assert poll.done is True
        assert poll.status == "stopped"
        assert poll.pr_url == "https://github.com/org/repo/pull/7"

    @pytest.mark.asyncio
    async def test_poll_running_not_done(self):
        client = LiveDevinClient(api_key="test-key")

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "status_enum": "running",
            "summary": "Still working...",
        }
        mock_resp.raise_for_status = MagicMock()

        mock_client_instance = AsyncMock()
        mock_client_instance.get = AsyncMock(return_value=mock_resp)
        mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client_instance.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client_instance):
            poll = await client.poll("session-456")

        assert poll.done is False
        assert poll.status == "running"
        assert poll.output == "Still working..."
