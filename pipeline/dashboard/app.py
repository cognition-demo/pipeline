"""FastAPI app: GitHub webhook receiver + dashboard API + static UI."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from pipeline.state import StateStore

logger = logging.getLogger(__name__)

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

def _get_store() -> StateStore:
    if _store is None:
        raise HTTPException(status_code=503, detail="Store not initialised")
    return _store


@app.get("/api/incidents")
async def get_incidents() -> list[dict]:
    return await _get_store().get_all()


@app.get("/api/metrics")
async def get_metrics() -> dict[str, Any]:
    rows = await _get_store().get_all()
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
    """Clear all pipeline state. Always requires GITHUB_WEBHOOK_SECRET."""
    store = _get_store()
    _require_secret(request)
    await store.reset()
    return {"status": "reset"}


# ── Webhooks ─────────────────────────────────────────────────────────────────

def _require_secret(request: Request) -> None:
    """Reject the request unless GITHUB_WEBHOOK_SECRET is set and matches."""
    secret = os.environ.get("GITHUB_WEBHOOK_SECRET", "")
    if not secret:
        raise HTTPException(
            status_code=403,
            detail="GITHUB_WEBHOOK_SECRET is not configured; endpoint disabled",
        )
    provided = request.headers.get("X-Webhook-Secret", "")
    if not hmac.compare_digest(provided, secret):
        raise HTTPException(status_code=401, detail="Invalid secret")


def _verify_webhook_secret(request: Request) -> None:
    """Soft verification for webhook endpoints — warns but allows when unset."""
    secret = os.environ.get("GITHUB_WEBHOOK_SECRET", "")
    if not secret:
        logger.warning("GITHUB_WEBHOOK_SECRET is not set — webhook accepted without verification")
        return
    provided = request.headers.get("X-Webhook-Secret", "")
    if not hmac.compare_digest(provided, secret):
        raise HTTPException(status_code=401, detail="Invalid secret")


def _fire_session(incident: dict) -> None:
    """Save incident and start a Devin session as a background task."""
    from pipeline.devin import make_client
    from pipeline.orchestrator import run_session

    store = _get_store()

    async def _run() -> None:
        incident_id = await store.save_incident(incident)
        mode = os.environ.get("PIPELINE_MODE", "replay")
        client = make_client(mode)
        await run_session(incident_id, incident, client, store)

    asyncio.create_task(_run())


class _TriggerPayload:
    """Minimal validation for the /webhook/trigger payload."""

    REQUIRED = ("issue_number", "issue_url", "repo")

    @classmethod
    def parse(cls, raw: bytes) -> dict:
        import json as _json
        try:
            payload = _json.loads(raw)
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=400, detail="Invalid JSON") from exc
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="Expected JSON object")
        missing = [k for k in cls.REQUIRED if k not in payload]
        if missing:
            raise HTTPException(status_code=400, detail=f"Missing fields: {', '.join(missing)}")
        if not isinstance(payload.get("issue_number"), int):
            raise HTTPException(status_code=400, detail="issue_number must be an integer")
        for list_field in ("failing_tests", "upstream_commits"):
            val = payload.get(list_field, [])
            if not isinstance(val, list) or not all(isinstance(v, str) for v in val):
                raise HTTPException(status_code=400, detail=f"{list_field} must be a list of strings")
        return {
            "issue_number": payload["issue_number"],
            "issue_url": str(payload["issue_url"]),
            "repo": str(payload["repo"]),
            "failing_tests": payload.get("failing_tests", []),
            "upstream_commits": payload.get("upstream_commits", []),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }


@app.post("/webhook/trigger")
async def direct_trigger(request: Request) -> dict:
    """Called by the nightly sync GitHub Action with full incident context."""
    _get_store()
    _verify_webhook_secret(request)
    incident = _TriggerPayload.parse(await request.body())
    _fire_session(incident)
    return {"status": "triggered"}


@app.post("/webhook/github")
async def github_webhook(request: Request) -> dict:
    """Receive native GitHub issue-opened webhook events (alternative trigger)."""
    _get_store()

    secret = os.environ.get("GITHUB_WEBHOOK_SECRET", "")
    body = await request.body()
    if secret:
        sig = request.headers.get("X-Hub-Signature-256", "")
        mac = hmac.new(secret.encode(), body, hashlib.sha256)
        expected = "sha256=" + mac.hexdigest()
        if not hmac.compare_digest(sig, expected):
            raise HTTPException(status_code=401, detail="Invalid signature")
    else:
        logger.warning("GITHUB_WEBHOOK_SECRET is not set — webhook accepted without signature verification")

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
