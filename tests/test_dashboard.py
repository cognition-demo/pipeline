"""Tests for pipeline.dashboard.app (FastAPI endpoints)."""
from __future__ import annotations

import asyncio
import json
import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from pipeline.dashboard.app import (
    _extract_section,
    _parse_issue,
    _verify_secret,
    app,
    init_app,
)
from pipeline.state import StateStore

from .conftest import SAMPLE_INCIDENT


@pytest.fixture
def dashboard_client(tmp_path):
    """Sync fixture that initializes the store and provides a test client."""
    db_path = tmp_path / "dash_test.db"
    store = StateStore(db_path=db_path)
    loop = asyncio.new_event_loop()
    loop.run_until_complete(store.init())
    init_app(store)
    with TestClient(app) as client:
        yield client, store
    loop.close()


class TestHealthEndpoint:
    def test_returns_ok(self, dashboard_client):
        client, _ = dashboard_client
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}


class TestGetIncidents:
    def test_empty_list(self, dashboard_client):
        client, _ = dashboard_client
        resp = client.get("/api/incidents")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_returns_saved_incidents(self, dashboard_client):
        client, store = dashboard_client
        asyncio.new_event_loop().run_until_complete(store.save_incident(SAMPLE_INCIDENT))
        resp = client.get("/api/incidents")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["issue_number"] == 42


class TestGetMetrics:
    def test_empty_metrics(self, dashboard_client):
        client, _ = dashboard_client
        resp = client.get("/api/metrics")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_incidents"] == 0
        assert data["sessions_running"] == 0
        assert data["sessions_completed"] == 0
        assert data["avg_time_to_fix_seconds"] is None
        assert data["prs_opened"] == 0

    def test_metrics_with_completed_session(self, dashboard_client):
        client, store = dashboard_client
        loop = asyncio.new_event_loop()
        loop.run_until_complete(store.save_incident(SAMPLE_INCIDENT))
        loop.run_until_complete(store.upsert_session({
            "incident_id": 1,
            "session_id": "metrics-sess",
            "status": "completed",
            "pr_url": "https://github.com/org/repo/pull/5",
            "devin_summary": "Fixed",
            "started_at": "2025-01-15T10:00:00+00:00",
            "completed_at": "2025-01-15T10:03:00+00:00",
        }))
        resp = client.get("/api/metrics")
        data = resp.json()
        assert data["total_incidents"] == 1
        assert data["sessions_completed"] == 1
        assert data["avg_time_to_fix_seconds"] == 180
        assert data["prs_opened"] == 1

    def test_metrics_running_sessions(self, dashboard_client):
        client, store = dashboard_client
        loop = asyncio.new_event_loop()
        loop.run_until_complete(store.save_incident(SAMPLE_INCIDENT))
        loop.run_until_complete(store.upsert_session({
            "incident_id": 1,
            "session_id": "running-sess",
            "status": "running",
            "started_at": "2025-01-15T10:00:00+00:00",
            "completed_at": None,
        }))
        resp = client.get("/api/metrics")
        data = resp.json()
        assert data["sessions_running"] == 1
        assert data["sessions_completed"] == 0


class TestResetEndpoint:
    def test_reset_clears_data(self, dashboard_client):
        client, store = dashboard_client
        loop = asyncio.new_event_loop()
        loop.run_until_complete(store.save_incident(SAMPLE_INCIDENT))
        resp = client.post("/api/reset")
        assert resp.status_code == 200
        assert resp.json() == {"status": "reset"}
        rows = loop.run_until_complete(store.get_all())
        assert rows == []

    def test_reset_with_valid_secret(self, dashboard_client):
        client, store = dashboard_client
        asyncio.new_event_loop().run_until_complete(store.save_incident(SAMPLE_INCIDENT))
        with patch.dict(os.environ, {"GITHUB_WEBHOOK_SECRET": "mysecret"}):
            resp = client.post(
                "/api/reset", headers={"X-Webhook-Secret": "mysecret"}
            )
        assert resp.status_code == 200

    def test_reset_with_invalid_secret(self, dashboard_client):
        client, _ = dashboard_client
        with patch.dict(os.environ, {"GITHUB_WEBHOOK_SECRET": "mysecret"}):
            resp = client.post(
                "/api/reset", headers={"X-Webhook-Secret": "wrong"}
            )
        assert resp.status_code == 401


class TestDirectTrigger:
    def test_trigger_accepts_payload(self, dashboard_client):
        client, _ = dashboard_client
        payload = {
            "issue_number": 99,
            "issue_url": "https://github.com/org/repo/issues/99",
            "repo": "org/repo",
            "failing_tests": ["test_x"],
            "upstream_commits": ["abc fix"],
        }
        resp = client.post(
            "/webhook/trigger",
            content=json.dumps(payload),
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"status": "triggered"}


class TestGitHubWebhook:
    def test_ignores_non_issues_event(self, dashboard_client):
        client, _ = dashboard_client
        payload = {"action": "opened", "pull_request": {}}
        resp = client.post(
            "/webhook/github",
            content=json.dumps(payload),
            headers={"X-GitHub-Event": "pull_request"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"ignored": True}

    def test_ignores_non_opened_action(self, dashboard_client):
        client, _ = dashboard_client
        payload = {"action": "closed", "issue": {"number": 1}}
        resp = client.post(
            "/webhook/github",
            content=json.dumps(payload),
            headers={"X-GitHub-Event": "issues"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"ignored": True}

    def test_ignores_issue_without_label(self, dashboard_client):
        client, _ = dashboard_client
        payload = {
            "action": "opened",
            "issue": {
                "number": 10,
                "html_url": "https://github.com/org/repo/issues/10",
                "body": "## Failing tests\n- test_a\n",
                "labels": [{"name": "bug"}],
            },
            "repository": {"full_name": "org/repo"},
        }
        resp = client.post(
            "/webhook/github",
            content=json.dumps(payload),
            headers={"X-GitHub-Event": "issues"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"ignored": True}

    def test_triggers_on_valid_sync_failure(self, dashboard_client):
        client, _ = dashboard_client
        payload = {
            "action": "opened",
            "issue": {
                "number": 55,
                "html_url": "https://github.com/org/repo/issues/55",
                "body": "## Failing tests\n- test_sync\n\n## Upstream commits\n- abc fix\n",
                "labels": [{"name": "upstream-sync-failure"}],
            },
            "repository": {"full_name": "org/repo"},
        }
        with patch.dict(os.environ, {"GITHUB_WEBHOOK_SECRET": ""}):
            resp = client.post(
                "/webhook/github",
                content=json.dumps(payload),
                headers={"X-GitHub-Event": "issues"},
            )
        assert resp.status_code == 200
        assert resp.json() == {"status": "triggered"}


class TestExtractSection:
    def test_extracts_failing_tests(self):
        body = """## Failing tests
- test_one
- test_two

## Other section
stuff
"""
        result = _extract_section(body, "failing tests")
        assert result == ["test_one", "test_two"]

    def test_extracts_upstream_commits(self):
        body = """## Upstream commits
- abc123 first commit
- def456 second commit
"""
        result = _extract_section(body, "upstream commits")
        assert result == ["abc123 first commit", "def456 second commit"]

    def test_returns_empty_for_missing_section(self):
        body = "## Other\ncontent"
        assert _extract_section(body, "failing tests") == []

    def test_case_insensitive(self):
        body = """## FAILING TESTS
- test_a
"""
        result = _extract_section(body, "failing tests")
        assert result == ["test_a"]

    def test_handles_empty_body(self):
        assert _extract_section("", "failing tests") == []


class TestParseIssue:
    def test_parses_standard_issue(self):
        issue = {
            "number": 42,
            "html_url": "https://github.com/org/repo/issues/42",
            "body": "## Failing tests\n- test_x\n\n## Upstream commits\n- abc fix\n",
        }
        repo = {"full_name": "org/repo"}
        result = _parse_issue(issue, repo)
        assert result["issue_number"] == 42
        assert result["issue_url"] == "https://github.com/org/repo/issues/42"
        assert result["repo"] == "org/repo"
        assert result["failing_tests"] == ["test_x"]
        assert result["upstream_commits"] == ["abc fix"]
        assert "created_at" in result

    def test_handles_empty_body(self):
        issue = {"number": 1, "html_url": "url", "body": ""}
        repo = {"full_name": "org/repo"}
        result = _parse_issue(issue, repo)
        assert result["failing_tests"] == []
        assert result["upstream_commits"] == []

    def test_handles_missing_repo(self):
        issue = {"number": 1, "html_url": "url", "body": ""}
        result = _parse_issue(issue, {})
        assert result["repo"] == ""


class TestVerifySecret:
    def test_no_secret_configured_passes(self):
        from unittest.mock import MagicMock

        request = MagicMock()
        with patch.dict(os.environ, {"GITHUB_WEBHOOK_SECRET": ""}):
            _verify_secret(request)  # should not raise

    def test_valid_secret_passes(self):
        from unittest.mock import MagicMock

        request = MagicMock()
        request.headers = {"X-Webhook-Secret": "correct"}
        with patch.dict(os.environ, {"GITHUB_WEBHOOK_SECRET": "correct"}):
            _verify_secret(request)  # should not raise

    def test_invalid_secret_raises(self):
        from fastapi import HTTPException
        from unittest.mock import MagicMock

        request = MagicMock()
        request.headers = {"X-Webhook-Secret": "wrong"}
        with patch.dict(os.environ, {"GITHUB_WEBHOOK_SECRET": "correct"}):
            with pytest.raises(HTTPException) as exc_info:
                _verify_secret(request)
            assert exc_info.value.status_code == 401
