"""Pydantic models for the upstream-sync incident pipeline."""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, computed_field


class SessionStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class SyncIncident(BaseModel):
    """Parsed from a GitHub issue created by the nightly sync workflow."""
    issue_number: int
    issue_url: str
    repo: str
    failing_tests: list[str]
    upstream_commits: list[str]
    created_at: datetime


class DevinSession(BaseModel):
    incident_id: int
    session_id: str
    status: SessionStatus = SessionStatus.PENDING
    pr_url: str | None = None
    devin_summary: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None

    @computed_field
    @property
    def time_to_fix_seconds(self) -> int | None:
        if self.started_at and self.completed_at:
            return int((self.completed_at - self.started_at).total_seconds())
        return None


class PipelineRun(BaseModel):
    incident: SyncIncident
    session: DevinSession | None = None
