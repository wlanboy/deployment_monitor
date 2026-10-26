import sqlite3

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
