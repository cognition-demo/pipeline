"""Devin API client."""
from __future__ import annotations

import os
from typing import Any

import httpx

DEVIN_API_BASE = "https://api.devin.ai/v3"
DEVIN_APP_BASE = "https://app.devin.ai/sessions"


def session_url(session_id: str) -> str:
    """Browser URL for a Devin session id."""
    return f"{DEVIN_APP_BASE}/{session_id.removeprefix('devin-')}"

DEVIN_PROMPT_TEMPLATE = """\
Our nightly upstream sync ran and CI is now failing. We need you to \
investigate, find the root cause, and open a pull request with a fix.

## Repository

{repo_url} (fork of Apache Superset — base branch: master)

## Failing tests

{failing_tests}

## Upstream commits pulled in by this sync

{upstream_commits}

## What we need

1. Investigate why the tests are failing
2. Trace the failure back to its root cause — look at the upstream commits, \
read the relevant code on both sides
3. Determine the correct fix (do not just make the tests pass by deleting \
them or weakening assertions)
4. Create a branch, commit your fix, and open a pull request to master
"""

# Terminal states — session is done (successfully or not)
_TERMINAL_STATUSES = {"exit", "error"}


class SessionPoll:
    def __init__(self, status: str, status_detail: str | None, output: Any, done: bool, pr_url: str | None = None):
        self.status = status
        self.status_detail = status_detail
        self.output = output
        self.done = done
        self.pr_url = pr_url


class LiveDevinClient:
    def __init__(self, api_key: str, org_id: str):
        self._org_id = org_id
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    async def create_session(self, incident: Any) -> str:
        failing = "\n".join(f"  - {t}" for t in incident.failing_tests)
        commits = "\n".join(f"  - {c}" for c in incident.upstream_commits)
        prompt = DEVIN_PROMPT_TEMPLATE.format(
            failing_tests=failing,
            upstream_commits=commits,
            repo_url=f"https://github.com/{incident.repo}",
        )
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{DEVIN_API_BASE}/organizations/{self._org_id}/sessions",
                headers=self._headers,
                json={"prompt": prompt},
                timeout=30,
            )
            resp.raise_for_status()
            return resp.json()["session_id"]

    async def poll(self, session_id: str) -> SessionPoll:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{DEVIN_API_BASE}/organizations/{self._org_id}/sessions/{session_id}",
                headers=self._headers,
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()

        status = data.get("status", "running")
        status_detail = data.get("status_detail")
        done = status in _TERMINAL_STATUSES

        # PR URL lives in pull_requests array
        prs = data.get("pull_requests") or []
        pr_url = prs[0]["pr_url"] if prs else None

        # Structured output if requested; fall back to session URL for display
        output = data.get("structured_output") or data.get("url")

        return SessionPoll(status=status, status_detail=status_detail, output=output, done=done, pr_url=pr_url)


def make_client() -> LiveDevinClient:
    api_key = os.environ.get("DEVIN_API_KEY", "")
    org_id = os.environ.get("DEVIN_ORG_ID", "")
    if not api_key:
        raise ValueError("DEVIN_API_KEY must be set")
    if not org_id:
        raise ValueError("DEVIN_ORG_ID must be set")
    return LiveDevinClient(api_key=api_key, org_id=org_id)
