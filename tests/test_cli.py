"""Tests for pipeline.cli (CLI entry point)."""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch, MagicMock

import pytest
from click.testing import CliRunner

from pipeline.cli import DEMO_INCIDENT, main


class TestDemoIncident:
    def test_has_required_fields(self):
        assert "issue_number" in DEMO_INCIDENT
        assert "issue_url" in DEMO_INCIDENT
        assert "repo" in DEMO_INCIDENT
        assert "failing_tests" in DEMO_INCIDENT
        assert "upstream_commits" in DEMO_INCIDENT

    def test_issue_number(self):
        assert DEMO_INCIDENT["issue_number"] == 42

    def test_repo(self):
        assert DEMO_INCIDENT["repo"] == "cognition-demo/superset"

    def test_failing_tests_not_empty(self):
        assert len(DEMO_INCIDENT["failing_tests"]) > 0

    def test_upstream_commits_not_empty(self):
        assert len(DEMO_INCIDENT["upstream_commits"]) > 0


class TestMainGroup:
    def test_help(self):
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "Pipeline" in result.output

    def test_run_help(self):
        runner = CliRunner()
        result = runner.invoke(main, ["run", "--help"])
        assert result.exit_code == 0
        assert "--mode" in result.output
        assert "--host" in result.output
        assert "--port" in result.output

    def test_dashboard_help(self):
        runner = CliRunner()
        result = runner.invoke(main, ["dashboard", "--help"])
        assert result.exit_code == 0
        assert "--host" in result.output
        assert "--port" in result.output


class TestRunCommand:
    def test_run_sets_pipeline_mode(self):
        runner = CliRunner()
        with patch("pipeline.cli.asyncio.run") as mock_run:
            result = runner.invoke(main, ["run", "--mode", "replay"])
        assert os.environ.get("PIPELINE_MODE") == "replay"
        mock_run.assert_called_once()

    def test_run_defaults_to_replay(self):
        runner = CliRunner()
        with patch.dict(os.environ, {"PIPELINE_MODE": "replay"}, clear=False):
            with patch("pipeline.cli.asyncio.run") as mock_run:
                result = runner.invoke(main, ["run", "--mode", "replay"])
        assert result.exit_code == 0
        mock_run.assert_called_once()


class TestDashboardCommand:
    def test_dashboard_calls_uvicorn(self):
        runner = CliRunner()
        with patch("pipeline.cli.asyncio.run") as mock_arun:
            with patch("pipeline.cli.uvicorn.run") as mock_uvicorn:
                result = runner.invoke(main, ["dashboard", "--port", "9999"])
        mock_arun.assert_called_once()  # store.init()
        mock_uvicorn.assert_called_once()
        call_kwargs = mock_uvicorn.call_args
        assert call_kwargs[1]["port"] == 9999
