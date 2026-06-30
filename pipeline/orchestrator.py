"""Session lifecycle management — create, poll, persist."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from rich.console import Console

from pipeline.devin import LiveDevinClient, session_url
from pipeline.state import StateStore

console = Console()


async def run_session(
    incident_id: int,
    incident: dict,
    client: LiveDevinClient,
    store: StateStore,
) -> None:
    started = datetime.now(timezone.utc).isoformat()
    session_id = await client.create_session(_IncidentProxy(incident))
    console.print(f"  [blue]→[/] Devin session started: [underline]{session_url(session_id)}[/]")

    await store.upsert_session({
        "incident_id": incident_id,
        "session_id": session_id,
        "status": "running",
        "started_at": started,
    })

    while True:
        poll = await client.poll(session_id)
        # Treat PR creation as done — the Devin session may stay open in
        # "awaiting instructions" (blocked) rather than reaching an exit state,
        # but the pipeline's job is complete once the PR exists.
        done = poll.done or bool(poll.pr_url)
        await store.upsert_session({
            "incident_id": incident_id,
            "session_id": session_id,
            "status": "completed" if done else "running",
            "pr_url": poll.pr_url,
            "devin_summary": poll.output,
            "started_at": started,
            "completed_at": datetime.now(timezone.utc).isoformat() if done else None,
        })
        if done:
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
