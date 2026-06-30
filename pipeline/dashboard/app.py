"""FastAPI app: GitHub webhook receiver + dashboard API + static UI."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
import re
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from pipeline.state import StateStore

app = FastAPI(title="Sync Incident Pipeline")
_store: StateStore | None = None

STATIC_DIR = str(__import__("pathlib").Path(__file__).parent / "static")


def init_app(store: StateStore) -> None:
    global _store
    _store = store


# ── Static UI ────────────────────────────────────────────────────────────────

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(f"{STATIC_DIR}/index.html")


# ── API ──────────────────────────────────────────────────────────────────────

@app.get("/api/incidents")
async def get_incidents() -> list[dict]:
    assert _store
    return await _store.get_all()


@app.get("/api/metrics")
async def get_metrics() -> dict[str, Any]:
    assert _store
    rows = await _store.get_all()
    sessions = [r for r in rows if r.get("session_id")]
    completed = [s for s in sessions if s.get("status") == "completed"]
    fix_times = []
    for s in completed:
        if s.get("started_at") and s.get("completed_at"):
            start = datetime.fromisoformat(s["started_at"])
            end = datetime.fromisoformat(s["completed_at"])
            fix_times.append(int((end - start).total_seconds()))
    return {
        "total_incidents": len(rows),
        "sessions_running": len([s for s in sessions if s.get("status") == "running"]),
        "sessions_completed": len(completed),
        "avg_time_to_fix_seconds": int(sum(fix_times) / len(fix_times)) if fix_times else None,
        "prs_opened": len([s for s in completed if s.get("pr_url")]),
    }


@app.get("/health")
async def health() -> dict:
    return {"ok": True}


@app.post("/api/reset")
async def reset_state(request: Request) -> dict:
    """Clear all pipeline state. Protected by WEBHOOK_SECRET."""
    assert _store
    _verify_secret(request)
    await _store.reset()
    return {"status": "reset"}


# ── Webhooks ─────────────────────────────────────────────────────────────────

def _verify_secret(request: Request) -> None:
    secret = os.environ.get("WEBHOOK_SECRET", "")
    if not secret:
        return
    provided = request.headers.get("X-Webhook-Secret", "")
    if not hmac.compare_digest(provided, secret):
        raise HTTPException(status_code=401, detail="Invalid secret")


def _fire_session(incident: dict) -> None:
    """Save incident and start a Devin session as a background task."""
    from pipeline.devin import make_client
    from pipeline.orchestrator import run_session

    async def _run() -> None:
        assert _store
        incident_id = await _store.save_incident(incident)
        client = make_client()
        await run_session(incident_id, incident, client, _store)

    asyncio.create_task(_run())


@app.post("/webhook/trigger")
async def direct_trigger(request: Request) -> dict:
    """Called by the nightly sync GitHub Action with full incident context."""
    assert _store
    _verify_secret(request)

    import json as _json
    payload = _json.loads(await request.body())
    incident = {
        "issue_number": payload.get("issue_number", 0),
        "issue_url": payload.get("issue_url", ""),
        "repo": payload.get("repo", ""),
        "failing_tests": payload.get("failing_tests", []),
        "upstream_commits": payload.get("upstream_commits", []),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    _fire_session(incident)
    return {"status": "triggered"}


@app.post("/webhook/github")
async def github_webhook(request: Request) -> dict:
    """Receive native GitHub issue-opened webhook events (alternative trigger)."""
    assert _store

    secret = os.environ.get("WEBHOOK_SECRET", "")
    body = await request.body()
    if secret:
        sig = request.headers.get("X-Hub-Signature-256", "")
        mac = hmac.new(secret.encode(), body, hashlib.sha256)
        expected = "sha256=" + mac.hexdigest()
        if not hmac.compare_digest(sig, expected):
            raise HTTPException(status_code=401, detail="Invalid signature")

    import json as _json
    payload = _json.loads(body)
    event = request.headers.get("X-GitHub-Event", "")

    if event != "issues" or payload.get("action") != "opened":
        return {"ignored": True}

    issue = payload["issue"]
    labels = [lbl["name"] for lbl in issue.get("labels", [])]
    if "upstream-sync-failure" not in labels:
        return {"ignored": True}

    _fire_session(_parse_issue(issue, payload.get("repository", {})))
    return {"status": "triggered"}


def _parse_issue(issue: dict, repo: dict) -> dict:
    body = issue.get("body", "")
    return {
        "issue_number": issue["number"],
        "issue_url": issue["html_url"],
        "repo": repo.get("full_name", ""),
        "failing_tests": _extract_section(body, "failing tests"),
        "upstream_commits": _extract_section(body, "upstream commits"),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def _extract_section(body: str, section: str) -> list[str]:
    pattern = rf"##\s*{re.escape(section)}\s*\n(.*?)(?=\n##|\Z)"
    m = re.search(pattern, body, re.IGNORECASE | re.DOTALL)
    if not m:
        return []
    return [line.lstrip("- ").strip() for line in m.group(1).strip().splitlines() if line.strip()]
