import pytest
import yaml
from conftest import wait_until_gone

import db
import deploy
import metrics
from deploy import extract_hosts, parse_stats
from runner import NOT_STARTED_STATUS

# Realistic ansible PLAY RECAP line
RECAP_LINE = "web01                      : ok=5 changed=2 unreachable=0 failed=0 skipped=1"


class TestParseStats:
    def test_full_match(self):
        stats = parse_stats(RECAP_LINE)
        assert stats["ok"] == 5
        assert stats["changed"] == 2
        assert stats["failed"] == 0
        assert stats["skipped"] == 1

    def test_no_match_returns_zeros(self):
        stats = parse_stats("no recap information here")
        assert stats == {"ok": 0, "changed": 0, "unreachable": 0, "skipped": 0, "failed": 0}

    def test_failed_tasks_counted(self):
        line = "db01                       : ok=1 changed=0 unreachable=0 failed=3 skipped=0"
        stats = parse_stats(line)
        assert stats["failed"] == 3

    def test_changed_tasks_counted(self):
        line = "app01                      : ok=4 changed=3 unreachable=0 failed=0 skipped=2"
        stats = parse_stats(line)
        assert stats["changed"] == 3
        assert stats["skipped"] == 2

    def test_empty_string(self):
        stats = parse_stats("")
        assert stats == {"ok": 0, "changed": 0, "unreachable": 0, "skipped": 0, "failed": 0}


class TestExtractHosts:
    def test_single_host(self):
        hosts = extract_hosts(RECAP_LINE)
        assert hosts == ["web01"]

    def test_multiple_hosts_sorted(self):
        output = (
            "z-host                     : ok=1 changed=0 unreachable=0 failed=0 skipped=0\n"
            "a-host                     : ok=2 changed=1 unreachable=0 failed=0 skipped=0"
        )
        hosts = extract_hosts(output)
        assert hosts == ["a-host", "z-host"]

    def test_no_hosts(self):
        hosts = extract_hosts("PLAY [all] *****\nTASK [ping] ****")
        assert hosts == []

    def test_empty_string(self):
        assert extract_hosts("") == []

    def test_deduplicated(self):
        # Same host appearing twice should only show once
        output = (
            "web01                      : ok=5 changed=0 unreachable=0 failed=0 skipped=0\n"
            "web01                      : ok=3 changed=1 unreachable=0 failed=0 skipped=0"
        )
        hosts = extract_hosts(output)
        assert hosts == ["web01"]


class TestMain:
    @pytest.fixture
    def setup(self, tmp_path, monkeypatch, isolated_db, fake_ansible):
        monkeypatch.setattr(deploy, "push_metrics", lambda *args: True)
        inventory = tmp_path / "inventory.yaml"
        inventory.write_text("all: {}\n")
        config = tmp_path / "config.yaml"
        config.write_text(yaml.safe_dump({"playbooks": [{"file": "site.yaml", "retries": 2}, {"file": "other.yaml"}]}))
        return ["-i", str(inventory), "-c", str(config)]

    def runs(self):
        with db.connect() as conn:
            return conn.execute("SELECT playbook, status, attempt FROM playbook_runs ORDER BY id").fetchall()

    def run_results(self):
        with db.connect() as conn:
            return [r[0] for r in conn.execute("SELECT result FROM deployment_runs")]

    def test_success_exit_code(self, setup):
        assert deploy.main(setup) == 0
        assert [r[0] for r in self.runs()] == ["site.yaml", "other.yaml"]
        assert self.run_results() == [db.RUN_SUCCESS]

    def test_failure_stops_deployment(self, setup, monkeypatch):
        monkeypatch.setenv("FAKE_ANSIBLE_EXIT", "2")
        assert deploy.main(setup) == 1
        # Beide Versuche von site.yaml, other.yaml wird übersprungen
        assert self.runs() == [("site.yaml", 2, 1), ("site.yaml", 2, 2)]
        assert self.run_results() == [db.RUN_FAILED]

    def test_continue_on_failure(self, setup, monkeypatch, tmp_path):
        config = tmp_path / "config.yaml"
        config.write_text(yaml.safe_dump({"playbooks": [{"file": "a.yaml"}, {"file": "b.yaml"}],
                                          "deployment": {"continue_on_failure": True}}))
        monkeypatch.setenv("FAKE_ANSIBLE_EXIT", "2")
        assert deploy.main(["-i", setup[1], "-c", str(config)]) == 1
        assert [r[0] for r in self.runs()] == ["a.yaml", "b.yaml"]

    def test_missing_ansible_binary(self, setup, monkeypatch, tmp_path):
        empty = tmp_path / "empty-bin"
        empty.mkdir()
        monkeypatch.setenv("PATH", str(empty))
        assert deploy.main(setup) == 1
        assert self.runs() == [("site.yaml", NOT_STARTED_STATUS, 1)]

    def test_missing_inventory(self, setup, tmp_path):
        assert deploy.main(["-i", str(tmp_path / "missing.yaml")]) == 1

    def test_timeout_kills_process_group(self, setup, tmp_path, monkeypatch):
        config = tmp_path / "config.yaml"
        config.write_text(yaml.safe_dump({"playbooks": [{"file": "site.yaml"}], "ansible": {"timeout_seconds": 0.5}}))
        monkeypatch.setenv("FAKE_ANSIBLE_SLEEP", "20")
        monkeypatch.setenv("FAKE_ANSIBLE_CHILD_PIDFILE", str(tmp_path / "child.pid"))
        assert deploy.main(["-i", setup[1], "-c", str(config)]) == 1
        assert wait_until_gone(int((tmp_path / "child.pid").read_text()))

    def test_interrupt_marks_run_aborted(self, setup, monkeypatch):
        def interrupted(*args):
            raise KeyboardInterrupt
        monkeypatch.setattr(deploy, "run_playbook", interrupted)
        with pytest.raises(KeyboardInterrupt):
            deploy.main(setup)
        assert self.run_results() == [db.RUN_ABORTED]

    def test_push_failure_does_not_crash(self, setup, monkeypatch):
        monkeypatch.setattr(deploy, "push_metrics", metrics.push_metrics)
        monkeypatch.setenv("PUSHGATEWAY_URL", "http://127.0.0.1:9")
        assert deploy.main(setup) == 0
