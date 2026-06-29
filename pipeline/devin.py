"""Devin API client — live and replay modes."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import httpx

DEVIN_API_BASE = "https://api.devin.ai/v1"
FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "sessions"

DEVIN_PROMPT_TEMPLATE = """\
You are working on a fork of Apache Superset that includes a custom \
multi-tenant embedding feature. A nightly upstream sync just completed and \
the CI test suite is failing.

## Failing tests

{failing_tests}

## Error message

    marshmallow.exceptions.ValidationError: {{'tenant_id': ['Unknown field.']}}

## Upstream commits introduced in this sync

{upstream_commits}

## Your task

1. Examine the upstream change in superset/security/api.py (commit 0fd244b)
2. Read the custom feature in superset/extensions/tenant_embed.py
3. Understand why the upstream change was made (security: preventing silent \
scope widening when unknown fields are present in RLS rules)
4. Fix build_tenant_rls_rule in tenant_embed.py to be compatible with the \
new strict schema — remove the tenant_id field from the RLS rule dict and \
use Python logging for audit correlation instead
5. Update tests/unit_tests/extensions/test_tenant_embed.py to reflect the fix
6. Open a pull request with your changes

Repository: {repo_url}
Branch: main

Key files to look at:
- superset/extensions/tenant_embed.py
- superset/security/api.py (pay attention to RlsRuleSchema)
- tests/unit_tests/extensions/test_tenant_embed.py
"""


class SessionPoll(BaseModel if False else object):
    def __init__(self, status: str, output: str | None, done: bool, pr_url: str | None = None):
        self.status = status
        self.output = output
        self.done = done
        self.pr_url = pr_url


class LiveDevinClient:
    def __init__(self, api_key: str):
        self._headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

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
                f"{DEVIN_API_BASE}/sessions",
                headers=self._headers,
                json={"prompt": prompt},
                timeout=30,
            )
            resp.raise_for_status()
            return resp.json()["session_id"]

    async def poll(self, session_id: str) -> SessionPoll:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{DEVIN_API_BASE}/session/{session_id}",
                headers=self._headers,
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
        status = data.get("status_enum", "running")
        output = data.get("structured_output") or data.get("summary")
        done = status in ("blocked", "stopped")
        pr_url = _extract_pr_url(output)
        return SessionPoll(status=status, output=output, done=done, pr_url=pr_url)


class ReplayDevinClient:
    """Plays back pre-recorded Devin poll sequences from fixtures."""

    def __init__(self, speed_multiplier: float = 10.0, fixture: str = "tenant-auth-fix"):
        self._speed = speed_multiplier
        self._fixture = fixture
        self._sequences: dict[str, list[dict]] = {}
        self._cursors: dict[str, int] = {}
        self._counter = 0

    async def create_session(self, incident: Any) -> str:
        self._counter += 1
        sid = f"replay-session-{self._counter:04d}"
        path = FIXTURES_DIR / f"{self._fixture}.json"
        with open(path) as f:
            self._sequences[sid] = json.load(f)
        self._cursors[sid] = 0
        return sid

    async def poll(self, session_id: str) -> SessionPoll:
        sequence = self._sequences[session_id]
        idx = self._cursors[session_id]
        frame = sequence[idx]
        delay = frame.get("delay_seconds", 2) / self._speed
        await asyncio.sleep(delay)
        if idx < len(sequence) - 1:
            self._cursors[session_id] += 1
        status = frame.get("status", "running")
        output = frame.get("output")
        done = frame.get("done", False)
        pr_url = frame.get("pr_url") or _extract_pr_url(output)
        return SessionPoll(status=status, output=output, done=done, pr_url=pr_url)


def make_client(mode: str) -> LiveDevinClient | ReplayDevinClient:
    if mode == "live":
        api_key = os.environ.get("DEVIN_API_KEY", "")
        if not api_key:
            raise ValueError("DEVIN_API_KEY must be set for live mode")
        return LiveDevinClient(api_key=api_key)
    return ReplayDevinClient()


def _extract_pr_url(text: str | None) -> str | None:
    if not text:
        return None
    import re
    m = re.search(r"https://github\.com/[^\s]+/pull/\d+", text)
    return m.group(0) if m else None
