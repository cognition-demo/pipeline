"""SQLite-backed state store for sync incidents and Devin sessions."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

DB_PATH = Path(__file__).parent.parent / "data" / "pipeline.db"


class StateStore:
    def __init__(self, db_path: Path = DB_PATH):
        self._path = db_path

    async def init(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self._path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS incidents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    issue_number INTEGER,
                    issue_url TEXT,
                    repo TEXT,
                    failing_tests TEXT,
                    upstream_commits TEXT,
                    created_at TEXT
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    incident_id INTEGER,
                    session_id TEXT,
                    status TEXT DEFAULT 'pending',
                    pr_url TEXT,
                    devin_summary TEXT,
                    started_at TEXT,
                    completed_at TEXT
                )
            """)
            await db.commit()

    async def save_incident(self, incident: dict) -> int:
        async with aiosqlite.connect(self._path) as db:
            cur = await db.execute(
                """INSERT INTO incidents
                   (issue_number, issue_url, repo, failing_tests, upstream_commits, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    incident["issue_number"],
                    incident["issue_url"],
                    incident["repo"],
                    json.dumps(incident["failing_tests"]),
                    json.dumps(incident["upstream_commits"]),
                    incident.get("created_at", datetime.now(timezone.utc).isoformat()),
                ),
            )
            await db.commit()
            return cur.lastrowid

    async def upsert_session(self, session: dict) -> None:
        async with aiosqlite.connect(self._path) as db:
            existing = await (await db.execute(
                "SELECT id FROM sessions WHERE session_id = ?", (session["session_id"],)
            )).fetchone()
            if existing:
                await db.execute(
                    """UPDATE sessions SET status=?, pr_url=?, devin_summary=?,
                       started_at=?, completed_at=? WHERE session_id=?""",
                    (
                        session.get("status", "pending"),
                        session.get("pr_url"),
                        session.get("devin_summary"),
                        session.get("started_at"),
                        session.get("completed_at"),
                        session["session_id"],
                    ),
                )
            else:
                await db.execute(
                    """INSERT INTO sessions
                       (incident_id, session_id, status, pr_url, devin_summary, started_at, completed_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        session["incident_id"],
                        session["session_id"],
                        session.get("status", "pending"),
                        session.get("pr_url"),
                        session.get("devin_summary"),
                        session.get("started_at"),
                        session.get("completed_at"),
                    ),
                )
            await db.commit()

    async def get_all(self) -> list[dict]:
        async with aiosqlite.connect(self._path) as db:
            db.row_factory = aiosqlite.Row
            rows = await (await db.execute("""
                SELECT i.issue_number, i.issue_url, i.repo,
                       i.failing_tests, i.upstream_commits, i.created_at,
                       s.session_id, s.status, s.pr_url, s.devin_summary,
                       s.started_at, s.completed_at
                FROM incidents i
                LEFT JOIN sessions s ON s.incident_id = i.id
                ORDER BY i.id DESC
            """)).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["failing_tests"] = json.loads(d["failing_tests"] or "[]")
            d["upstream_commits"] = json.loads(d["upstream_commits"] or "[]")
            result.append(d)
        return result

    async def reset(self) -> None:
        async with aiosqlite.connect(self._path) as db:
            await db.execute("DELETE FROM incidents")
            await db.execute("DELETE FROM sessions")
            await db.commit()
