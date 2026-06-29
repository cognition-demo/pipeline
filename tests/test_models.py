"""Tests for pipeline.models."""
from __future__ import annotations

from datetime import datetime, timezone

from pipeline.models import (
    DevinSession,
    PipelineRun,
    SessionStatus,
    SyncIncident,
)


class TestSessionStatus:
    def test_enum_values(self):
        assert SessionStatus.PENDING == "pending"
        assert SessionStatus.RUNNING == "running"
        assert SessionStatus.COMPLETED == "completed"
        assert SessionStatus.FAILED == "failed"

    def test_string_comparison(self):
        assert SessionStatus.PENDING == "pending"
        assert str(SessionStatus.RUNNING) == "running"


class TestSyncIncident:
    def test_create_minimal(self):
        incident = SyncIncident(
            issue_number=42,
            issue_url="https://github.com/org/repo/issues/42",
            repo="org/repo",
            failing_tests=["test_one"],
            upstream_commits=["abc123 some commit"],
            created_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        )
        assert incident.issue_number == 42
        assert incident.repo == "org/repo"
        assert incident.failing_tests == ["test_one"]
        assert incident.upstream_commits == ["abc123 some commit"]

    def test_multiple_tests_and_commits(self):
        incident = SyncIncident(
            issue_number=99,
            issue_url="https://github.com/org/repo/issues/99",
            repo="org/repo",
            failing_tests=["test_a", "test_b", "test_c"],
            upstream_commits=["aaa first", "bbb second"],
            created_at=datetime(2025, 6, 15, tzinfo=timezone.utc),
        )
        assert len(incident.failing_tests) == 3
        assert len(incident.upstream_commits) == 2


class TestDevinSession:
    def test_defaults(self):
        session = DevinSession(incident_id=1, session_id="sess-001")
        assert session.status == SessionStatus.PENDING
        assert session.pr_url is None
        assert session.devin_summary is None
        assert session.started_at is None
        assert session.completed_at is None
        assert session.time_to_fix_seconds is None

    def test_time_to_fix_computation(self):
        start = datetime(2025, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        end = datetime(2025, 1, 1, 10, 5, 30, tzinfo=timezone.utc)
        session = DevinSession(
            incident_id=1,
            session_id="sess-002",
            status=SessionStatus.COMPLETED,
            started_at=start,
            completed_at=end,
        )
        assert session.time_to_fix_seconds == 330  # 5 min 30 sec

    def test_time_to_fix_none_when_incomplete(self):
        session = DevinSession(
            incident_id=1,
            session_id="sess-003",
            status=SessionStatus.RUNNING,
            started_at=datetime(2025, 1, 1, 10, 0, 0, tzinfo=timezone.utc),
        )
        assert session.time_to_fix_seconds is None

    def test_with_pr_url(self):
        session = DevinSession(
            incident_id=1,
            session_id="sess-004",
            pr_url="https://github.com/org/repo/pull/5",
        )
        assert session.pr_url == "https://github.com/org/repo/pull/5"


class TestPipelineRun:
    def test_with_session(self):
        incident = SyncIncident(
            issue_number=1,
            issue_url="https://github.com/org/repo/issues/1",
            repo="org/repo",
            failing_tests=["test_x"],
            upstream_commits=["abc fix"],
            created_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        )
        session = DevinSession(incident_id=1, session_id="s-1")
        run = PipelineRun(incident=incident, session=session)
        assert run.incident.issue_number == 1
        assert run.session.session_id == "s-1"

    def test_without_session(self):
        incident = SyncIncident(
            issue_number=2,
            issue_url="https://github.com/org/repo/issues/2",
            repo="org/repo",
            failing_tests=[],
            upstream_commits=[],
            created_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        )
        run = PipelineRun(incident=incident)
        assert run.session is None
