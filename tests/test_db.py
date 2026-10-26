import sqlite3
from datetime import datetime, timedelta

import pytest

import db


def tables(path):
    with sqlite3.connect(path) as conn:
        return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


class TestInitDb:
    def test_creates_tables(self, isolated_db):
        assert {"deployments", "deployment_items", "playbook_runs"} <= tables(isolated_db)

    def test_idempotent(self, isolated_db):
        db.init_db()
        db.init_db()

    def test_migrates_items_without_position(self, tmp_path, monkeypatch):
        path = str(tmp_path / "old.db")
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE deployments (id TEXT PRIMARY KEY, name TEXT)")
        conn.execute("CREATE TABLE deployment_items (deployment_id TEXT, playbook TEXT, inventory TEXT, tags TEXT, skip_tags TEXT)")
        conn.execute("INSERT INTO deployment_items VALUES ('d', 'a.yaml', 'i', NULL, NULL)")
        conn.execute("INSERT INTO deployment_items VALUES ('d', 'b.yaml', 'i', NULL, NULL)")
        conn.commit()
        conn.close()

        monkeypatch.setattr(db, "DB_PATH", path)
        db.init_db()

        with db.connect() as conn:
            rows = conn.execute("SELECT playbook, position FROM deployment_items ORDER BY position").fetchall()
        assert rows == [("a.yaml", 1), ("b.yaml", 2)]


class TestConnect:
    def test_commits_on_success(self, isolated_db):
        with db.connect() as conn:
            conn.execute("INSERT INTO deployments (id, name) VALUES ('1', 'a')")
        with db.connect() as conn:
            assert conn.execute("SELECT COUNT(*) FROM deployments").fetchone()[0] == 1

    def test_rolls_back_on_error(self, isolated_db):
        with pytest.raises(RuntimeError), db.connect() as conn:
            conn.execute("INSERT INTO deployments (id, name) VALUES ('1', 'a')")
            raise RuntimeError
        with db.connect() as conn:
            assert conn.execute("SELECT COUNT(*) FROM deployments").fetchone()[0] == 0


class TestRunState:
    def result(self, run_id):
        with db.connect() as conn:
            return conn.execute("SELECT result FROM deployment_runs WHERE run_id = ?", (run_id,)).fetchone()[0]

    def test_start_and_finish(self, isolated_db):
        db.start_run("r1", "dep")
        assert self.result("r1") == db.RUN_RUNNING
        db.finish_run("r1", db.RUN_SUCCESS)
        assert self.result("r1") == db.RUN_SUCCESS

    def test_finish_only_once(self, isolated_db):
        db.start_run("r1")
        db.finish_run("r1", db.RUN_FAILED)
        db.finish_run("r1", db.RUN_ABORTED)
        assert self.result("r1") == db.RUN_FAILED

    def test_mark_interrupted(self, isolated_db):
        db.start_run("r1")
        db.start_run("r2")
        db.finish_run("r2", db.RUN_SUCCESS)
        assert db.mark_interrupted_runs() == 1
        assert self.result("r1") == db.RUN_INTERRUPTED
        assert self.result("r2") == db.RUN_SUCCESS


class TestPruneRuns:
    def insert(self, run_id, started, result=db.RUN_SUCCESS):
        db.log_run(run_id, "pb", "inv", started, started, 1, 0, 1)
        with db.connect() as conn:
            conn.execute("INSERT INTO deployment_runs (run_id, started_at, result) VALUES (?, ?, ?)",
                         (run_id, started, result))

    def run_ids(self, table):
        with db.connect() as conn:
            return sorted(r[0] for r in conn.execute(f"SELECT run_id FROM {table}"))

    def test_deletes_only_old_runs(self, isolated_db):
        now = datetime.now().astimezone()
        self.insert("old", (now - timedelta(days=100)).isoformat())
        self.insert("new", (now - timedelta(days=10)).isoformat())
        assert db.prune_runs(90) == 1
        assert self.run_ids("playbook_runs") == ["new"]
        assert self.run_ids("deployment_runs") == ["new"]

    def test_running_run_kept(self, isolated_db):
        self.insert("running", (datetime.now().astimezone() - timedelta(days=100)).isoformat(), db.RUN_RUNNING)
        db.prune_runs(90)
        assert self.run_ids("deployment_runs") == ["running"]

    def test_disabled_with_zero(self, isolated_db):
        self.insert("old", (datetime.now().astimezone() - timedelta(days=1000)).isoformat())
        assert db.prune_runs(0) == 0
        assert self.run_ids("playbook_runs") == ["old"]


class TestLogRun:
    def test_inserts_correct_values(self, isolated_db):
        db.log_run("run-1", "site.yml", "inv.yaml", "2024-01-01T10:00:00", "2024-01-01T10:01:00", 60, 0, 1, "dep-1")
        with db.connect() as conn:
            row = conn.execute(
                "SELECT run_id, deployment_id, playbook, inventory, start_time, end_time, duration, status, attempt FROM playbook_runs"
            ).fetchone()
        assert row == ("run-1", "dep-1", "site.yml", "inv.yaml", "2024-01-01T10:00:00", "2024-01-01T10:01:00", 60, 0, 1)

    def test_without_deployment(self, isolated_db):
        db.log_run("run-2", "fail.yml", "inv", "s", "e", 5, 2, 3)
        with db.connect() as conn:
            assert conn.execute("SELECT deployment_id, status, attempt FROM playbook_runs").fetchone() == (None, 2, 3)
