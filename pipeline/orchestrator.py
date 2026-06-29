"""Session lifecycle management — create, poll, persist."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from rich.console import Console

from pipeline.devin import LiveDevinClient, ReplayDevinClient
from pipeline.state import StateStore

logger = logging.getLogger(__name__)
console = Console()

MAX_CONSECUTIVE_ERRORS = 5


async def run_session(
    incident_id: int,
    incident: dict,
    client: LiveDevinClient | ReplayDevinClient,
    store: StateStore,
) -> None:
    started = datetime.now(timezone.utc).isoformat()
    session_id = await client.create_session(_IncidentProxy(incident))
    console.print(f"  [blue]→[/] Devin session [bold]{session_id}[/] started")

    await store.upsert_session({
        "incident_id": incident_id,
        "session_id": session_id,
        "status": "running",
        "started_at": started,
    })

    consecutive_errors = 0
    while True:
        try:
            poll = await client.poll(session_id)
        except Exception:
            consecutive_errors += 1
            logger.warning(
                "Poll error for session %s (%d/%d)",
                session_id, consecutive_errors, MAX_CONSECUTIVE_ERRORS,
                exc_info=True,
            )
            if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                logger.error("Too many consecutive poll errors for session %s; marking failed", session_id)
                await store.upsert_session({
                    "incident_id": incident_id,
                    "session_id": session_id,
                    "status": "failed",
                    "started_at": started,
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                })
                console.print(f"  [red]✗[/] Session {session_id} failed after {MAX_CONSECUTIVE_ERRORS} consecutive poll errors")
                return
            await asyncio.sleep(2 ** consecutive_errors)
            continue

        consecutive_errors = 0
        await store.upsert_session({
            "incident_id": incident_id,
            "session_id": session_id,
            "status": "completed" if poll.done else "running",
            "pr_url": poll.pr_url,
            "devin_summary": poll.output,
            "started_at": started,
            "completed_at": datetime.now(timezone.utc).isoformat() if poll.done else None,
        })
        if poll.done:
            if poll.pr_url:
                console.print(f"  [green]✓[/] PR opened: [underline]{poll.pr_url}[/]")
            else:
                console.print("  [yellow]![/] Session completed — no PR URL found")
            break
        await asyncio.sleep(0.5)


class _IncidentProxy:
    """Minimal duck-type so the Devin client can read incident fields."""
    def __init__(self, d: dict):
        self.failing_tests = d.get("failing_tests", [])
        self.upstream_commits = d.get("upstream_commits", [])
        self.repo = d.get("repo", "")
