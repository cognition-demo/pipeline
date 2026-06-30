"""CLI entry point."""
from __future__ import annotations

import asyncio
import os

import click
import uvicorn
from rich.console import Console

from pipeline.dashboard.app import app as dashboard_app, init_app
from pipeline.devin import make_client
from pipeline.orchestrator import run_session
from pipeline.state import StateStore

console = Console()

TARGET_REPO = os.environ.get("TARGET_REPO", "cognition-demo/superset")

DEMO_INCIDENT = {
    "issue_number": 42,
    "issue_url": f"https://github.com/{TARGET_REPO}/issues/42",
    "repo": TARGET_REPO,
    "failing_tests": [
        "tests/unit_tests/extensions/test_tenant_embed.py::test_tenant_rls_rule_is_schema_compatible",
        "tests/unit_tests/extensions/test_tenant_embed.py::test_guest_token_payload_rls_is_schema_compatible",
        "tests/unit_tests/extensions/test_tenant_provisioner.py::test_valid_payload_loads_successfully",
        "tests/unit_tests/extensions/test_tenant_provisioner.py::test_provision_tenant_returns_provisioned_status",
    ],
    "upstream_commits": [
        "0fd244b fix(security): reject unknown fields on guest-token RLS rules (#41217)",
        "919bd35 chore(deps): bump marshmallow from 3.26.2 to 4.3.0 (#39751)",
    ],
}


@click.group()
def main() -> None:
    """Pipeline — Devin-powered upstream sync incident responder."""


@main.command()
@click.option("--host", default="0.0.0.0")
@click.option("--port", default=8765, type=int)
def run(host: str, port: int) -> None:
    """Simulate a sync incident and watch Devin fix it."""
    asyncio.run(_run(host, port))


async def _run(host: str, port: int) -> None:
    store = StateStore()
    await store.init()
    await store.reset()
    init_app(store)

    config = uvicorn.Config(dashboard_app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    server_task = asyncio.create_task(server.serve())

    console.print("\n[bold blue]Pipeline[/]")
    console.print(f"Dashboard: [underline blue]http://localhost:{port}[/]\n")

    console.print("[bold]Incident:[/] upstream sync failure detected")
    console.print(f"  Issue: {DEMO_INCIDENT['issue_url']}")
    for t in DEMO_INCIDENT["failing_tests"]:
        console.print(f"  [red]✗[/] {t}")
    console.print()

    incident_id = await store.save_incident(DEMO_INCIDENT)
    client = make_client()

    console.print("[bold]Triggering Devin...[/]")
    await run_session(incident_id, DEMO_INCIDENT, client, store)

    console.print("\n[bold green]Done.[/] Dashboard still running — Ctrl+C to stop.\n")
    await server_task


@main.command()
@click.option("--host", default="0.0.0.0")
@click.option("--port", default=lambda: int(os.environ.get("PORT", "8765")), type=int)
def dashboard(host: str, port: int) -> None:
    """Serve the dashboard and webhook receiver. Default entrypoint for Railway."""
    store = StateStore()
    asyncio.run(store.init())
    init_app(store)
    uvicorn.run(dashboard_app, host=host, port=port, log_level="info")
