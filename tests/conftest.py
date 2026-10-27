import os
import stat
import sys
import tempfile
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Ensure the project root is on the path so test modules can import api, db, etc.
sys.path.insert(0, ROOT)

# api.py legt beim Import die Datenbank an – nie im Arbeitsverzeichnis des Repos
os.environ["DEPLOYMENT_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="deployment-monitor-tests-"), "deploymentjobs.db")
os.environ["DEPLOYMENT_CONFIG"] = os.path.join(ROOT, "config.yaml")
os.environ.pop("PUSHGATEWAY_URL", None)
os.environ.pop("DEPLOYMENT_API_TOKEN", None)

import db

# Ersatz für ansible-playbook: gibt Argumente und eine PLAY RECAP-Zeile aus.
# Verhalten über Umgebungsvariablen steuerbar.
FAKE_ANSIBLE = """#!/bin/sh
[ -n "$FAKE_ANSIBLE_PIDFILE" ] && echo $$ > "$FAKE_ANSIBLE_PIDFILE"
echo "ARGS: $*"
[ -n "$FAKE_ANSIBLE_OUTPUT" ] && echo "$FAKE_ANSIBLE_OUTPUT"
if [ -n "$FAKE_ANSIBLE_SLEEP" ]; then
  # Kindprozess wie bei ansible-Workern/ssh – muss beim Abbruch mit beendet werden
  sleep "$FAKE_ANSIBLE_SLEEP" &
  [ -n "$FAKE_ANSIBLE_CHILD_PIDFILE" ] && echo $! > "$FAKE_ANSIBLE_CHILD_PIDFILE"
  wait
fi
if [ -n "$FAKE_ANSIBLE_FAIL_ONCE" ] && [ ! -e "$FAKE_ANSIBLE_FAIL_ONCE" ]; then
  touch "$FAKE_ANSIBLE_FAIL_ONCE"
  echo "localhost                  : ok=1 changed=0 unreachable=0 failed=1 skipped=0"
  exit 2
fi
echo "localhost                  : ok=2 changed=1 unreachable=0 failed=0 skipped=0"
exit "${FAKE_ANSIBLE_EXIT:-0}"
"""


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    path = str(tmp_path / "data" / "deploymentjobs.db")
    monkeypatch.setattr(db, "DB_PATH", path)
    db.init_db()
    return path


@pytest.fixture
def fake_ansible(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "ansible-playbook"
    script.write_text(FAKE_ANSIBLE)
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    for var in ("FAKE_ANSIBLE_EXIT", "FAKE_ANSIBLE_SLEEP", "FAKE_ANSIBLE_OUTPUT",
                "FAKE_ANSIBLE_FAIL_ONCE", "FAKE_ANSIBLE_PIDFILE", "FAKE_ANSIBLE_CHILD_PIDFILE"):
        monkeypatch.delenv(var, raising=False)
    return script


def wait_until_gone(pid, seconds=10.0):
    """True, sobald der Prozess nicht mehr existiert."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.1)
    return False
