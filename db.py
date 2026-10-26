import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

# Gemeinsame Datenbank für API und CLI
DB_PATH = os.getenv("DEPLOYMENT_DB_PATH", os.path.join("deploymentjobs", "deploymentjobs.db"))


@contextmanager
def connect(path: str | None = None) -> Iterator[sqlite3.Connection]:
    """Verbindung öffnen, bei Erfolg committen, bei Fehler zurückrollen und immer schließen."""
    path = path or DB_PATH
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init_db(path: str | None = None) -> None:
    with connect(path) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS deployments (id TEXT PRIMARY KEY, name TEXT NOT NULL)")
        conn.execute("""CREATE TABLE IF NOT EXISTS deployment_items (
            deployment_id TEXT NOT NULL REFERENCES deployments(id) ON DELETE CASCADE,
            position INTEGER NOT NULL DEFAULT 0,
            playbook TEXT NOT NULL,
            inventory TEXT NOT NULL,
            tags TEXT,
            skip_tags TEXT
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS playbook_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            deployment_id TEXT,
            playbook TEXT NOT NULL,
            inventory TEXT,
            start_time TEXT,
            end_time TEXT,
            duration INTEGER,
            status INTEGER,
            attempt INTEGER
        )""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_playbook_runs_run_id ON playbook_runs (run_id)")

        # Migration: ältere Datenbanken haben keine Reihenfolge-Spalte
        columns = {row[1] for row in conn.execute("PRAGMA table_info(deployment_items)")}
        if "position" not in columns:
            conn.execute("ALTER TABLE deployment_items ADD COLUMN position INTEGER NOT NULL DEFAULT 0")
            conn.execute("UPDATE deployment_items SET position = rowid")


def log_run(run_id, playbook, inventory, start, end, duration, status, attempt, deployment_id=None):
    with connect() as conn:
        conn.execute(
            """INSERT INTO playbook_runs
               (run_id, deployment_id, playbook, inventory, start_time, end_time, duration, status, attempt)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (run_id, deployment_id, playbook, inventory, start, end, duration, status, attempt),
        )
