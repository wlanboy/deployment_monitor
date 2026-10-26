import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime

# Gemeinsame Datenbank für API und CLI
DB_PATH = os.getenv("DEPLOYMENT_DB_PATH", os.path.join("deploymentjobs", "deploymentjobs.db"))

# Ergebnis eines Deployment-Laufs (Tabelle deployment_runs)
RUN_RUNNING = "running"
RUN_SUCCESS = "success"
RUN_FAILED = "failed"
RUN_ABORTED = "aborted"
RUN_INTERRUPTED = "interrupted"


@contextmanager
def connect(path: str | None = None, immediate: bool = False) -> Iterator[sqlite3.Connection]:
    """Verbindung öffnen, bei Erfolg committen, bei Fehler zurückrollen und immer schließen.

    immediate=True holt die Schreibsperre sofort, damit Lesen-dann-Schreiben (z.B. Limit
    prüfen und einfügen) nicht mit parallelen Requests verzahnt wird.
    """
    path = path or DB_PATH
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        with conn:
            if immediate:
                conn.execute("BEGIN IMMEDIATE")
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
        conn.execute("""CREATE TABLE IF NOT EXISTS deployment_runs (
            run_id TEXT PRIMARY KEY,
            deployment_id TEXT,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            result TEXT NOT NULL
        )""")

        # Migration: ältere Datenbanken haben keine Reihenfolge-Spalte
        columns = {row[1] for row in conn.execute("PRAGMA table_info(deployment_items)")}
        if "position" not in columns:
            conn.execute("ALTER TABLE deployment_items ADD COLUMN position INTEGER NOT NULL DEFAULT 0")
            conn.execute("UPDATE deployment_items SET position = rowid")


def _now():
    return datetime.now().astimezone().isoformat()


def start_run(run_id, deployment_id=None):
    with connect() as conn:
        conn.execute(
            "INSERT INTO deployment_runs (run_id, deployment_id, started_at, result) VALUES (?, ?, ?, ?)",
            (run_id, deployment_id, _now(), RUN_RUNNING),
        )


def finish_run(run_id, result):
    """Ergebnis setzen – nur solange der Run noch läuft, ein gesetztes Ergebnis bleibt bestehen."""
    with connect() as conn:
        conn.execute(
            "UPDATE deployment_runs SET result = ?, finished_at = ? WHERE run_id = ? AND result = ?",
            (result, _now(), run_id, RUN_RUNNING),
        )


def mark_interrupted_runs():
    """Runs, die beim letzten Beenden noch liefen (Absturz, Neustart), als unterbrochen markieren."""
    with connect() as conn:
        return conn.execute(
            "UPDATE deployment_runs SET result = ?, finished_at = ? WHERE result = ?",
            (RUN_INTERRUPTED, _now(), RUN_RUNNING),
        ).rowcount


def prune_runs(retention_days):
    """Lauf-Historie älter als retention_days löschen (0 oder kleiner = nie)."""
    if retention_days <= 0:
        return 0
    cutoff = f"-{int(retention_days)} days"
    with connect() as conn:
        deleted = conn.execute(
            "DELETE FROM playbook_runs WHERE julianday(start_time) < julianday('now', ?)", (cutoff,)
        ).rowcount
        conn.execute(
            "DELETE FROM deployment_runs WHERE result != ? AND julianday(started_at) < julianday('now', ?)",
            (RUN_RUNNING, cutoff),
        )
    return deleted


def log_run(run_id, playbook, inventory, start, end, duration, status, attempt, deployment_id=None):
    with connect() as conn:
        conn.execute(
            """INSERT INTO playbook_runs
               (run_id, deployment_id, playbook, inventory, start_time, end_time, duration, status, attempt)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (run_id, deployment_id, playbook, inventory, start, end, duration, status, attempt),
        )
