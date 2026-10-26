import asyncio
import os
import subprocess
import threading
import time

import pytest
from conftest import wait_until_gone
from fastapi import HTTPException
from fastapi.testclient import TestClient

import api
import db
from locking import WorkspaceLock
from runner import ABORTED_STATUS, NOT_STARTED_STATUS, TIMEOUT_STATUS

# Realistic ansible PLAY RECAP line
RECAP_LINE = "web01                      : ok=4 changed=1 unreachable=0 failed=0 skipped=2"

SITE = "playbooks/site.yaml"
OTHER = "playbooks/other.yaml"
INVENTORY = "inventories/home.yaml"


@pytest.fixture
def workspace(tmp_path, monkeypatch, isolated_db):
    base = tmp_path / "dpl"
    for rel in (SITE, OTHER, INVENTORY):
        (base / rel).parent.mkdir(parents=True, exist_ok=True)
        (base / rel).write_text("---\n")
    (tmp_path / "secret.yaml").write_text("geheim\n")
    cfg = {
        "base_path": str(base),
        "playbooks": [{"file": SITE, "retries": 1}, {"file": OTHER, "retries": 2}],
        "inventories": [INVENTORY],
        "ansible": {"timeout_seconds": 30},
        "git": {"timeout_seconds": 30},
        "limits": {"max_deployments": 5, "max_items_per_deployment": 3},
    }
    monkeypatch.setattr(api, "config", cfg)
    monkeypatch.setattr(api, "base_path", str(base))
    return base


@pytest.fixture(autouse=True)
def pushed(monkeypatch):
    calls = []
    monkeypatch.setattr(api, "push_metrics", lambda *args: calls.append(args) or True)
    return calls


@pytest.fixture
def client():
    return TestClient(api.app)


def create_deployment(client, name="test"):
    response = client.post("/deployment", params={"name": name})
    assert response.status_code == 200
    return response.json()["id"]


def add_item(client, deployment_id, playbook=SITE, inventory=INVENTORY, **extra):
    return client.put(f"/deployment/{deployment_id}", params={"playbook": playbook, "inventory": inventory, **extra})


def deployment_with(client, *playbooks):
    deployment_id = create_deployment(client)
    for playbook in playbooks:
        assert add_item(client, deployment_id, playbook).status_code == 200
    return deployment_id


def git(cwd, *args):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
                          cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


class TestParseStats:
    def test_full_match(self):
        stats = api.parse_stats(RECAP_LINE)
        assert stats["ok"] == 4
        assert stats["changed"] == 1
        assert stats["failed"] == 0
        assert stats["skipped"] == 2

    def test_no_match_returns_zeros(self):
        stats = api.parse_stats("nothing relevant here")
        assert stats == {"ok": 0, "changed": 0, "unreachable": 0, "skipped": 0, "failed": 0}

    def test_failed_tasks(self):
        line = "db01                       : ok=1 changed=0 unreachable=0 failed=2 skipped=0"
        assert api.parse_stats(line)["failed"] == 2

    def test_sums_all_hosts(self):
        output = (
            "web01                      : ok=5 changed=2 unreachable=0 failed=0 skipped=1 rescued=0 ignored=0\n"
            "web02                      : ok=1 changed=0 unreachable=0 failed=3 skipped=0 rescued=0 ignored=0\n"
            "web03                      : ok=0 changed=0 unreachable=1 failed=0 skipped=0 rescued=0 ignored=0"
        )
        assert api.parse_stats(output) == {"ok": 6, "changed": 2, "unreachable": 1, "failed": 3, "skipped": 1}
        assert api.extract_hosts(output) == ["web01", "web02", "web03"]

    def test_task_output_mentioning_counters_is_ignored(self):
        assert api.parse_stats("TASK [debug] msg: ok=7 changed=7 unreachable=0 failed=0 skipped=0")["ok"] == 0


class TestExtractHosts:
    def test_extracts_hostname(self):
        hosts = api.extract_hosts(RECAP_LINE)
        assert hosts == ["web01"]

    def test_returns_sorted_list(self):
        output = (
            "z-node                     : ok=1 changed=0 unreachable=0 failed=0 skipped=0\n"
            "a-node                     : ok=2 changed=1 unreachable=0 failed=0 skipped=0"
        )
        assert api.extract_hosts(output) == ["a-node", "z-node"]

    def test_empty_output(self):
        assert api.extract_hosts("") == []

    def test_non_recap_lines_ignored(self):
        output = "PLAY [all] ****\nTASK [Gathering Facts] ****"
        assert api.extract_hosts(output) == []


class TestConfig:
    def test_missing_config_returns_empty_dict(self, tmp_path):
        assert api.load_config(str(tmp_path / "missing.yaml")) == {}

    def test_invalid_yaml_returns_empty_dict(self, tmp_path):
        path = tmp_path / "broken.yaml"
        path.write_text("playbooks: [unclosed\n")
        assert api.load_config(str(path)) == {}

    def test_workspace_root_falls_back_to_cwd(self, monkeypatch):
        monkeypatch.setattr(api, "base_path", "")
        assert api.workspace_root() == os.getcwd()

    def test_lists_whitelists(self, client, workspace):
        assert client.get("/playbooks").json() == [SITE, OTHER]
        assert client.get("/inventories").json() == [INVENTORY]


class TestPathTraversal:
    def test_whitelisted_item_accepted(self, client, workspace):
        assert add_item(client, create_deployment(client)).status_code == 200

    @pytest.mark.parametrize("playbook", [
        "../secret.yaml",
        "playbooks/../../secret.yaml",
        "/etc/passwd",
        "playbooks/./site.yaml",
        "playbooks/unknown.yaml",
    ])
    def test_non_whitelisted_playbook_rejected(self, client, workspace, playbook):
        response = add_item(client, create_deployment(client), playbook=playbook)
        assert response.status_code == 400

    @pytest.mark.parametrize("inventory", ["../secret.yaml", "/etc/hosts", "inventories/other.yaml"])
    def test_non_whitelisted_inventory_rejected(self, client, workspace, inventory):
        response = add_item(client, create_deployment(client), inventory=inventory)
        assert response.status_code == 400

    @pytest.mark.parametrize("entry", ["../secret.yaml", "/etc/hostname"])
    def test_whitelisted_path_outside_base_rejected(self, client, workspace, entry):
        api.config["playbooks"].append({"file": entry})
        response = add_item(client, create_deployment(client), playbook=entry)
        assert response.status_code == 400
        assert "außerhalb" in response.json()["detail"]

    def test_symlink_outside_base_rejected(self, client, workspace):
        (workspace / "playbooks" / "link.yaml").symlink_to(workspace.parent / "secret.yaml")
        api.config["playbooks"].append({"file": "playbooks/link.yaml"})
        response = add_item(client, create_deployment(client), playbook="playbooks/link.yaml")
        assert response.status_code == 400

    def test_whitelisted_but_missing_returns_404(self, client, workspace):
        api.config["playbooks"].append({"file": "playbooks/missing.yaml"})
        response = add_item(client, create_deployment(client), playbook="playbooks/missing.yaml")
        assert response.status_code == 404

    def test_run_rechecks_whitelist(self, client, workspace, fake_ansible):
        deployment_id = deployment_with(client, SITE)
        api.config["playbooks"] = [{"file": OTHER}]
        response = client.post(f"/rundeployment/{deployment_id}")
        assert response.status_code == 400


class TestInputLimits:
    @pytest.mark.parametrize("name", ["x" * 101, "<script>", "a;b", ""])
    def test_invalid_deployment_name(self, client, workspace, name):
        assert client.post("/deployment", params={"name": name}).status_code == 422

    def test_max_deployments(self, client, workspace):
        for i in range(5):
            create_deployment(client, f"d{i}")
        assert client.post("/deployment", params={"name": "zuviel"}).status_code == 409

    def test_max_items_per_deployment(self, client, workspace):
        deployment_id = deployment_with(client, SITE, SITE, SITE)
        assert add_item(client, deployment_id).status_code == 409

    @pytest.mark.parametrize("tags", ["--check", "a;b", "a b", "x,", "-x", "a,,b"])
    def test_invalid_tags(self, client, workspace, tags):
        deployment_id = create_deployment(client)
        assert add_item(client, deployment_id, tags=tags).status_code == 422
        assert add_item(client, deployment_id, skip_tags=tags).status_code == 422

    def test_valid_tags(self, client, workspace):
        deployment_id = create_deployment(client)
        assert add_item(client, deployment_id, tags="create,check", skip_tags="slow_tasks").status_code == 200

    def test_overlong_playbook_rejected(self, client, workspace):
        assert add_item(client, create_deployment(client), playbook="p" * 256).status_code == 422


class TestDeploymentCrud:
    def test_items_keep_insertion_order(self, client, workspace):
        deployment_id = deployment_with(client, OTHER, SITE, OTHER)
        items = client.get(f"/deployment/{deployment_id}").json()["items"]
        assert [i["playbook"] for i in items] == [OTHER, SITE, OTHER]

    def test_delete_removes_items(self, client, workspace):
        deployment_id = deployment_with(client, SITE)
        assert client.delete(f"/deployment/{deployment_id}").status_code == 200
        assert client.get(f"/deployment/{deployment_id}").status_code == 404
        with db.connect() as conn:
            assert conn.execute("SELECT COUNT(*) FROM deployment_items").fetchone()[0] == 0

    def test_add_to_unknown_deployment(self, client, workspace):
        assert add_item(client, "unknown").status_code == 404


class TestRunDeployment:
    def test_get_not_allowed(self, client, workspace):
        assert client.get(f"/rundeployment/{deployment_with(client, SITE)}").status_code == 405

    def test_unknown_deployment(self, client, workspace):
        assert client.post("/rundeployment/unknown").status_code == 404

    def test_success_streams_output_and_result(self, client, workspace, fake_ansible, pushed):
        deployment_id = create_deployment(client)
        add_item(client, deployment_id, tags="create", skip_tags="check")

        response = client.post(f"/rundeployment/{deployment_id}")

        assert response.status_code == 200
        run_id = response.headers["X-Run-Id"]
        body = response.text
        assert f"ARGS: {workspace / SITE} -i {workspace / INVENTORY} --tags=create --skip-tags=check" in body
        assert body.rstrip().endswith(f"RESULT=success run_id={run_id}")
        assert [call[0] for call in pushed] == [SITE]

        run = client.get(f"/runs/{run_id}").json()
        assert run["result"] == "success"
        assert run["deployment_id"] == deployment_id
        assert run["finished_at"] is not None
        assert run["attempts"][0]["status"] == 0

    def test_streaming_headers(self, client, workspace, fake_ansible):
        response = client.post(f"/rundeployment/{deployment_with(client, SITE)}")
        assert response.headers["X-Accel-Buffering"] == "no"
        assert response.headers["Cache-Control"] == "no-cache"

    def test_stats_summed_over_hosts(self, client, workspace, fake_ansible, monkeypatch, pushed):
        monkeypatch.setenv("FAKE_ANSIBLE_OUTPUT",
                           "web02                      : ok=1 changed=0 unreachable=1 failed=3 skipped=0")
        response = client.post(f"/rundeployment/{deployment_with(client, SITE)}")
        assert "Hosts: localhost, web02" in response.text
        assert "Nicht erreichbar: 1 Host(s)" in response.text
        stats = pushed[0][4]
        assert stats == {"ok": 3, "changed": 1, "unreachable": 1, "failed": 3, "skipped": 0}

    def test_stops_after_failure(self, client, workspace, fake_ansible, monkeypatch):
        monkeypatch.setenv("FAKE_ANSIBLE_EXIT", "2")
        response = client.post(f"/rundeployment/{deployment_with(client, SITE, OTHER)}")
        assert [line for line in response.text.splitlines() if line.startswith("ARGS:")] == [
            f"ARGS: {workspace / SITE} -i {workspace / INVENTORY}"]
        assert f"übersprungen: {OTHER}" in response.text
        assert "RESULT=failed" in response.text

    def test_continue_on_failure(self, client, workspace, fake_ansible, monkeypatch):
        api.config["deployment"] = {"continue_on_failure": True}
        monkeypatch.setenv("FAKE_ANSIBLE_EXIT", "2")
        response = client.post(f"/rundeployment/{deployment_with(client, SITE, OTHER)}")
        assert len([line for line in response.text.splitlines() if line.startswith("ARGS:")]) == 3
        assert "übersprungen" not in response.text

    def test_missing_ansible_binary(self, client, workspace, monkeypatch, tmp_path):
        empty = tmp_path / "empty-bin"
        empty.mkdir()
        monkeypatch.setenv("PATH", str(empty))
        response = client.post(f"/rundeployment/{deployment_with(client, OTHER)}")
        assert response.status_code == 200
        assert "konnte nicht gestartet werden" in response.text
        assert "Versuch 2/2" not in response.text
        assert "RESULT=failed" in response.text
        run = client.get(f"/runs/{response.headers['X-Run-Id']}").json()
        assert [a["status"] for a in run["attempts"]] == [NOT_STARTED_STATUS]

    def test_stored_tags_are_rechecked(self, client, workspace, fake_ansible):
        deployment_id = create_deployment(client)
        with db.connect() as conn:
            conn.execute(
                "INSERT INTO deployment_items (deployment_id, position, playbook, inventory, tags) VALUES (?, 1, ?, ?, ?)",
                (deployment_id, SITE, INVENTORY, "--check"),
            )
        assert client.post(f"/rundeployment/{deployment_id}").status_code == 400

    def test_validation_happens_under_lock(self, client, workspace, fake_ansible):
        deployment_id = deployment_with(client, SITE)
        api.config["playbooks"] = []
        lock = WorkspaceLock(api.lock_path())
        assert lock.acquire()
        try:
            # Gesperrt: 409, bevor überhaupt geprüft wird
            assert client.post(f"/rundeployment/{deployment_id}").status_code == 409
        finally:
            lock.release()
        assert client.post(f"/rundeployment/{deployment_id}").status_code == 400
        # Nach dem Validierungsfehler ist der Lock wieder frei
        assert lock.acquire()
        lock.release()

    def test_failure_reported(self, client, workspace, fake_ansible, monkeypatch):
        monkeypatch.setenv("FAKE_ANSIBLE_EXIT", "2")
        response = client.post(f"/rundeployment/{deployment_with(client, SITE)}")
        assert "RESULT=failed" in response.text
        run = client.get(f"/runs/{response.headers['X-Run-Id']}").json()
        assert run["result"] == "failed"
        assert run["attempts"][0]["status"] == 2

    def test_retries_from_config(self, client, workspace, fake_ansible, monkeypatch, tmp_path):
        monkeypatch.setenv("FAKE_ANSIBLE_FAIL_ONCE", str(tmp_path / "failed-once"))
        response = client.post(f"/rundeployment/{deployment_with(client, OTHER)}")
        assert "Versuch 2/2" in response.text
        assert "RESULT=success" in response.text
        run = client.get(f"/runs/{response.headers['X-Run-Id']}").json()
        assert [a["status"] for a in run["attempts"]] == [2, 0]
        assert run["result"] == "success"

    def test_runs_items_in_order(self, client, workspace, fake_ansible):
        response = client.post(f"/rundeployment/{deployment_with(client, OTHER, SITE)}")
        lines = [line for line in response.text.splitlines() if line.startswith("ARGS:")]
        assert OTHER in lines[0] and SITE in lines[1]

    def test_output_is_redacted(self, client, workspace, fake_ansible, monkeypatch):
        monkeypatch.setenv("FAKE_ANSIBLE_OUTPUT", "clone https://deploy:hunter2@git.example.com/x password=hunter2")
        response = client.post(f"/rundeployment/{deployment_with(client, SITE)}")
        assert "hunter2" not in response.text
        assert "https://***@git.example.com/x" in response.text

    def test_timeout_kills_playbook(self, client, workspace, fake_ansible, monkeypatch, tmp_path):
        api.config["ansible"]["timeout_seconds"] = 0.5
        monkeypatch.setenv("FAKE_ANSIBLE_SLEEP", "20")
        monkeypatch.setenv("FAKE_ANSIBLE_CHILD_PIDFILE", str(tmp_path / "child.pid"))
        started = time.monotonic()
        response = client.post(f"/rundeployment/{deployment_with(client, SITE)}")
        assert time.monotonic() - started < 10
        assert "Zeitüberschreitung" in response.text
        assert "RESULT=failed" in response.text
        run = client.get(f"/runs/{response.headers['X-Run-Id']}").json()
        assert run["attempts"][0]["status"] == TIMEOUT_STATUS
        assert wait_until_gone(int((tmp_path / "child.pid").read_text()))

    def test_conflict_while_locked(self, client, workspace, fake_ansible):
        deployment_id = deployment_with(client, SITE)
        lock = WorkspaceLock(api.lock_path())
        assert lock.acquire()
        try:
            assert client.post(f"/rundeployment/{deployment_id}").status_code == 409
        finally:
            lock.release()

    def test_lock_released_after_run(self, client, workspace, fake_ansible):
        deployment_id = deployment_with(client, SITE)
        assert client.post(f"/rundeployment/{deployment_id}").status_code == 200
        assert client.post(f"/rundeployment/{deployment_id}").status_code == 200
        lock = WorkspaceLock(api.lock_path())
        assert lock.acquire()
        lock.release()

    def test_unknown_run(self, client, workspace):
        assert client.get("/runs/unknown").status_code == 404

    def test_closing_stream_terminates_ansible(self, workspace, fake_ansible, monkeypatch, tmp_path):
        """Bricht der Client ab, darf ansible-playbook nicht weiterlaufen."""
        pidfile = tmp_path / "ansible.pid"
        monkeypatch.setenv("FAKE_ANSIBLE_PIDFILE", str(pidfile))
        monkeypatch.setenv("FAKE_ANSIBLE_CHILD_PIDFILE", str(tmp_path / "child.pid"))
        monkeypatch.setenv("FAKE_ANSIBLE_SLEEP", "30")
        plan = [api.PlannedItem(SITE, INVENTORY, str(workspace / SITE), str(workspace / INVENTORY), None, None, 1)]

        async def scenario():
            stream = api.stream_deployment(plan, "run-abort", "dep-abort")
            async for chunk in stream:
                if chunk.startswith("ARGS:"):
                    break
            child_pidfile = tmp_path / "child.pid"
            for _ in range(50):
                if child_pidfile.exists() and child_pidfile.read_text().strip():
                    break
                await asyncio.sleep(0.1)
            await stream.aclose()
            pid = int(pidfile.read_text())
            for _ in range(50):
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    return True
                await asyncio.sleep(0.1)
            return False

        assert asyncio.run(scenario())
        assert wait_until_gone(int((tmp_path / "child.pid").read_text()))
        with db.connect() as conn:
            (status,) = conn.execute("SELECT status FROM playbook_runs WHERE run_id = 'run-abort'").fetchone()
        assert status == ABORTED_STATUS


@pytest.fixture
def repo(workspace):
    git(workspace, "init", "-q", "-b", "main")
    git(workspace, "add", ".")
    git(workspace, "commit", "-q", "-m", "init")
    git(workspace, "branch", "feature/x")
    git(workspace, "branch", "release-1.0")
    return workspace


def current_branch(repo):
    return git(repo, "rev-parse", "--abbrev-ref", "HEAD")


class TestGit:
    def test_status(self, client, repo):
        response = client.get("/git/status")
        assert response.status_code == 200
        assert "main" in response.json()["status"]

    def test_no_repository(self, client, workspace):
        assert client.get("/git/status").status_code == 400
        assert client.post("/git/checkout/main").status_code == 400

    @pytest.mark.parametrize("branch", ["feature/x", "release-1.0"])
    def test_checkout_valid_branch(self, client, repo, branch):
        response = client.post(f"/git/checkout/{branch}")
        assert response.status_code == 200
        assert current_branch(repo) == branch

    def test_checkout_unknown_branch(self, client, repo):
        assert client.post("/git/checkout/does-not-exist").status_code == 400
        assert current_branch(repo) == "main"

    @pytest.mark.parametrize("branch", [
        "-f",
        "--force",
        "--orphan=evil",
        "-b evil",
        "--detach",
        "a..b",
        "@{-1}",
        "foo.lock",
        "feature/",
        ".hidden",
        "feature/.x",
        "with space",
        "x;id",
        "$(id)",
        "b" * 201,
    ])
    def test_branch_injection_rejected(self, client, repo, branch):
        response = client.post(f"/git/checkout/{branch}")
        assert response.status_code == 400
        assert current_branch(repo) == "main"
        assert "evil" not in git(repo, "branch", "--list")

    def test_checkout_conflict_while_locked(self, client, repo):
        lock = WorkspaceLock(api.lock_path())
        assert lock.acquire()
        try:
            assert client.post("/git/checkout/feature/x").status_code == 409
        finally:
            lock.release()
        assert current_branch(repo) == "main"

    def test_fetch_output_is_redacted(self, client, repo):
        git(repo, "remote", "add", "origin", "https://deploy:hunter2@127.0.0.1:9/repo.git")
        response = client.post("/git/checkout/feature/x")
        assert response.status_code == 502
        assert "hunter2" not in response.text
        assert current_branch(repo) == "main"


class TestRunState:
    def test_running_while_streaming(self, workspace, fake_ansible, monkeypatch):
        monkeypatch.setenv("FAKE_ANSIBLE_SLEEP", "30")
        plan = [api.PlannedItem(SITE, INVENTORY, str(workspace / SITE), str(workspace / INVENTORY), None, None, 1)]
        db.start_run("run-live", "dep-live")

        async def scenario():
            stream = api.stream_deployment(plan, "run-live", "dep-live")
            async for chunk in stream:
                if chunk.startswith("ARGS:"):
                    break
            state = api.get_run("run-live")["result"]
            await stream.aclose()
            return state

        assert asyncio.run(scenario()) == db.RUN_RUNNING

    def test_disconnect_marks_run_aborted_and_releases_lock(self, isolated_db):
        """Bricht der Client ab, wird der Generator geschlossen und on_close aufgerufen."""
        db.start_run("run-gone")
        lock = WorkspaceLock(api.lock_path())
        assert lock.acquire()
        closed = []

        async def body():
            yield "erste Zeile\n"
            yield "zweite Zeile\n"
            closed.append("nicht erreicht")

        def on_close():
            db.finish_run("run-gone", db.RUN_ABORTED)
            lock.release()

        async def receive():
            await asyncio.sleep(10)
            return {"type": "http.disconnect"}

        async def send(message):
            if message.get("body"):
                raise OSError("client gone")

        response = api.ClosingStreamingResponse(body(), on_close)
        scope = {"type": "http", "asgi": {"spec_version": "2.4"}}
        with pytest.raises(Exception):  # noqa: B017 - ClientDisconnect
            asyncio.run(response(scope, receive, send))

        assert closed == []
        assert api.get_run("run-gone")["result"] == db.RUN_ABORTED
        assert lock.acquire()
        lock.release()

    def test_interrupted_runs_marked_on_startup(self, isolated_db):
        db.start_run("run-crashed")
        api.recover_interrupted_runs()
        assert api.get_run("run-crashed")["result"] == db.RUN_INTERRUPTED

    def test_running_run_untouched_while_locked(self, isolated_db):
        db.start_run("run-other-worker")
        lock = WorkspaceLock(api.lock_path())
        assert lock.acquire()
        try:
            api.recover_interrupted_runs()
        finally:
            lock.release()
        assert api.get_run("run-other-worker")["result"] == db.RUN_RUNNING

    def test_finished_result_not_overwritten(self, isolated_db):
        db.start_run("run-done")
        db.finish_run("run-done", db.RUN_SUCCESS)
        db.finish_run("run-done", db.RUN_ABORTED)
        assert api.get_run("run-done")["result"] == db.RUN_SUCCESS


class TestConcurrentLimits:
    def test_parallel_creates_respect_limit(self, workspace):
        results = []

        def create(i):
            try:
                api.create_deployment(f"d{i}")
                results.append("ok")
            except HTTPException as e:
                results.append(e.status_code)

        threads = [threading.Thread(target=create, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert results.count("ok") == 5
        assert results.count(409) == 15

    def test_parallel_items_get_unique_positions(self, workspace):
        api.config["limits"]["max_items_per_deployment"] = 50
        deployment_id = api.create_deployment("parallel")["id"]
        threads = [threading.Thread(target=api.add_playbook_to_deployment, args=(deployment_id, SITE, INVENTORY, None, None))
                   for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        with db.connect() as conn:
            positions = [r[0] for r in conn.execute("SELECT position FROM deployment_items")]
        assert sorted(positions) == list(range(1, 21))


@pytest.fixture
def remote_repo(repo, tmp_path):
    """repo mit Remote "origin"; zusätzlich ein zweiter Clone zum Pushen neuer Commits."""
    origin = tmp_path / "origin.git"
    git(tmp_path, "init", "-q", "--bare", str(origin))
    git(repo, "remote", "add", "origin", str(origin))
    git(repo, "push", "-q", "origin", "main", "feature/x", "release-1.0")
    upstream = tmp_path / "upstream"
    git(tmp_path, "clone", "-q", str(origin), str(upstream))
    return repo, upstream


def push_commit(upstream, branch, message):
    git(upstream, "checkout", "-q", branch)
    git(upstream, "commit", "-q", "--allow-empty", "-m", message)
    git(upstream, "push", "-q", "origin", branch)
    return git(upstream, "rev-parse", "HEAD")


class TestGitRemote:
    def test_checkout_gets_latest_remote_commit(self, client, remote_repo):
        repo, upstream = remote_repo
        new_commit = push_commit(upstream, "feature/x", "neu")
        response = client.post("/git/checkout/feature/x")
        assert response.status_code == 200
        assert response.json()["commit"] == new_commit
        assert git(repo, "rev-parse", "HEAD") == new_commit

    def test_existing_stale_local_branch_is_updated(self, client, remote_repo):
        repo, upstream = remote_repo
        assert client.post("/git/checkout/feature/x").status_code == 200
        assert client.post("/git/checkout/main").status_code == 200
        new_commit = push_commit(upstream, "feature/x", "v2")
        assert client.post("/git/checkout/feature/x").status_code == 200
        assert git(repo, "rev-parse", "HEAD") == new_commit
        assert git(repo, "rev-parse", "--abbrev-ref", "@{u}") == "origin/feature/x"

    def test_local_only_branch_still_works(self, client, remote_repo):
        repo, _ = remote_repo
        git(repo, "branch", "feature-local")
        assert client.post("/git/checkout/feature-local").status_code == 200
        assert current_branch(repo) == "feature-local"

    def test_dirty_worktree_rejected(self, client, remote_repo):
        repo, _ = remote_repo
        (repo / SITE).write_text("- hosts: all\n")
        response = client.post("/git/checkout/feature/x")
        assert response.status_code == 409
        assert SITE in response.json()["detail"]["local_changes"]
        assert current_branch(repo) == "main"


class TestBranchWhitelist:
    @pytest.fixture(autouse=True)
    def restrict(self, workspace):
        api.config["git"]["allowed_branches"] = ["main", "release-*"]

    def test_allowed_branch(self, client, repo):
        assert client.post("/git/checkout/release-1.0").status_code == 200

    def test_disallowed_branch(self, client, repo):
        assert client.post("/git/checkout/feature/x").status_code == 403
        assert current_branch(repo) == "main"

    def test_run_refused_on_disallowed_branch(self, client, repo, fake_ansible):
        deployment_id = deployment_with(client, SITE)
        git(repo, "checkout", "-q", "feature/x")
        response = client.post(f"/rundeployment/{deployment_id}")
        assert response.status_code == 409
        assert "feature/x" in response.json()["detail"]

    def test_run_refused_on_detached_head(self, client, repo, fake_ansible):
        deployment_id = deployment_with(client, SITE)
        git(repo, "checkout", "-q", "--detach")
        assert client.post(f"/rundeployment/{deployment_id}").status_code == 409

    def test_run_allowed_on_allowed_branch(self, client, repo, fake_ansible):
        assert client.post(f"/rundeployment/{deployment_with(client, SITE)}").status_code == 200

    def test_empty_list_allows_nothing(self, client, repo):
        api.config["git"]["allowed_branches"] = []
        assert client.post("/git/checkout/main").status_code == 403
