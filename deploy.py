import subprocess, time, yaml
from datetime import datetime
from db import init_db, log_deployment
from metrics import push_metrics

def run_playbook(playbook, retries):
    for attempt in range(1, retries + 1):
        start = datetime.now()
        start_ts = time.time()

        result = subprocess.run(["ansible-playbook", playbook], capture_output=True)
        end_ts = time.time()
        end = datetime.now()

        duration = int(end_ts - start_ts)
        status = result.returncode

        log_deployment(playbook, start.isoformat(), end.isoformat(), duration, status, attempt)
        push_metrics(playbook, duration, status, attempt, config)

        if status == 0:
            break
        else:
            print(f"❌ Fehler in {playbook}, Versuch {attempt}/{retries}")

if __name__ == "__main__":
    init_db()
    with open("config.yaml") as f:
        config = yaml.safe_load(f)

    for pb in config["playbooks"]:
        run_playbook(pb["file"], pb["retries"])
