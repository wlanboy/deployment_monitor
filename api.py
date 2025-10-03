from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import StreamingResponse
import subprocess, time, yaml, uuid, re, os, sqlite3
from datetime import datetime
import requests

app = FastAPI(title="Deployment API")

DB_PATH = "deploymentjobs.db"

# 📁 Konfiguration laden
try:
    with open("config.yaml") as f:
        config = yaml.safe_load(f)
        base_path = config.get("base_path", "")
except Exception  as e:
    config = {"playbooks": [], "prometheus": {}}
    print(f"Fehler beim Laden der config.yaml: {e}")

def resolve_path(relative_path: str) -> str:
    return os.path.join(base_path, relative_path) if base_path else relative_path

# 🧮 Metriken pushen
def push_metrics(playbook, duration, status, attempt, stats, run_id):
    job = config.get("prometheus", {}).get("job_name", "ansible_deployment")
    url = config.get("prometheus", {}).get("pushgateway_url", "http://localhost:9091")
    labels = f'playbook="{playbook}",run_id="{run_id}"'
    metrics = f"""
# TYPE deployment_duration_seconds gauge
deployment_duration_seconds{{{labels}}} {duration}
# TYPE deployment_errors_total counter
deployment_errors_total{{{labels}}} {1 if status != 0 else 0}
# TYPE deployment_retries_total counter
deployment_retries_total{{{labels}}} {attempt}
# TYPE deployment_failed_tasks_total counter
deployment_failed_tasks_total{{{labels}}} {stats.get("failed", 0)}
# TYPE deployment_changed_tasks_total counter
deployment_changed_tasks_total{{{labels}}} {stats.get("changed", 0)}
# TYPE deployment_skipped_tasks_total counter
deployment_skipped_tasks_total{{{labels}}} {stats.get("skipped", 0)}
# TYPE deployment_status gauge
deployment_status{{{labels}}} {status}
"""
    try:
        requests.post(f"{url}/metrics/job/{job}", data=metrics)
    except Exception as e:
        print(f"Prometheus push failed: {e}")

# 🧠 Stats extrahieren
def parse_stats(output):
    stats = {'ok': 0, 'changed': 0, 'skipped': 0, 'failed': 0}
    match = re.search(r"ok=(\d+)\s+changed=(\d+)\s+unreachable=(\d+)\s+failed=(\d+)\s+skipped=(\d+)", output)
    if match:
        stats['ok'] = int(match.group(1))
        stats['changed'] = int(match.group(2))
        stats['failed'] = int(match.group(4))
        stats['skipped'] = int(match.group(5))
    return stats

def extract_hosts(output):
    hosts = set()
    for line in output.splitlines():
        if re.match(r"^\s*\S+\s+:.*ok=", line):
            host = line.split(":")[0].strip()
            hosts.add(host)
    return sorted(hosts)

# 🔁 Playbook ausführen + streamen
def run_playbook_streamed(playbook, inventory, tags, skip_tags, run_id, retries=1):
    for attempt in range(1, retries + 1):
        yield f"\n▶ Starte Playbook: {playbook} (Versuch {attempt}/{retries})\n"
        if tags: yield f"Tags: {tags}\n"
        if skip_tags: yield f"Skip-Tags: {skip_tags}\n"

        start = datetime.now()
        start_ts = time.time()

        playbook_path = resolve_path(playbook)
        inventory_path = resolve_path(inventory)
        cmd = ["ansible-playbook", playbook_path, "-i", inventory_path]

        if tags: cmd += ["--tags", tags]
        if skip_tags: cmd += ["--skip-tags", skip_tags]

        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        output_lines = []
        for line in process.stdout:
            output_lines.append(line)
            yield line
        process.stdout.close()
        process.wait()

        end = datetime.now()
        duration = int(time.time() - start_ts)
        status = process.returncode
        full_output = "".join(output_lines)
        stats = parse_stats(full_output)
        hosts = extract_hosts(full_output)

        push_metrics(playbook, duration, status, attempt, stats, run_id)

        yield f"\nErgebnis: {playbook}\nStart: {start.strftime('%H:%M:%S')} | Dauer: {duration}s | Status: {'✅' if status == 0 else '❌'}\n"
        yield f"Hosts: {', '.join(hosts) if hosts else '—'}\n"

        if status == 0: break

# 🗃️ SQLite Setup
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("CREATE TABLE IF NOT EXISTS deployments (id TEXT PRIMARY KEY, name TEXT)")
    c.execute("""CREATE TABLE IF NOT EXISTS deployment_items (
        deployment_id TEXT,
        playbook TEXT,
        inventory TEXT,
        tags TEXT,
        skip_tags TEXT
    )""")
    conn.commit()
    conn.close()

init_db()

# 🌐 Endpunkte

@app.get("/playbooks")
def list_playbooks():
    return [pb["file"] for pb in config.get("playbooks", [])]

@app.post("/deployment")
def create_deployment(name: str = Query(...)):
    deployment_id = str(uuid.uuid4())
    conn = sqlite3.connect(DB_PATH)
    conn.execute("INSERT INTO deployments (id, name) VALUES (?, ?)", (deployment_id, name))
    conn.commit()
    conn.close()
    return {"id": deployment_id, "name": name}

@app.get("/deployment/{deployment_id}")
def get_deployment(deployment_id: str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT name FROM deployments WHERE id = ?", (deployment_id,))
    row = c.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Deployment not found")
    name = row[0]
    c.execute("SELECT playbook, inventory, tags, skip_tags FROM deployment_items WHERE deployment_id = ?", (deployment_id,))
    items = [{"playbook": r[0], "inventory": r[1], "tags": r[2], "skip_tags": r[3]} for r in c.fetchall()]
    conn.close()
    return {"id": deployment_id, "name": name, "items": items}

@app.put("/deployment/{deployment_id}")
def add_playbook_to_deployment(deployment_id: str, playbook: str = Query(...), inventory: str = Query(...), tags: str = Query(None), skip_tags: str = Query(None)):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT 1 FROM deployments WHERE id = ?", (deployment_id,))
    if not c.fetchone():
        raise HTTPException(status_code=404, detail="Deployment not found")
    c.execute("INSERT INTO deployment_items (deployment_id, playbook, inventory, tags, skip_tags) VALUES (?, ?, ?, ?, ?)",
              (deployment_id, playbook, inventory, tags, skip_tags))
    conn.commit()
    conn.close()
    return {"status": "added", "deployment_id": deployment_id}

@app.get("/deployments")
def list_all_deployments():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id, name FROM deployments")
    deployments = [{"id": row[0], "name": row[1]} for row in c.fetchall()]
    conn.close()
    return deployments

@app.delete("/deployment/{deployment_id}")
def delete_deployment(deployment_id: str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT 1 FROM deployments WHERE id = ?", (deployment_id,))
    if not c.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Deployment not found")
    c.execute("DELETE FROM deployment_items WHERE deployment_id = ?", (deployment_id,))
    c.execute("DELETE FROM deployments WHERE id = ?", (deployment_id,))
    conn.commit()
    conn.close()
    return {"status": "deleted", "deployment_id": deployment_id}
            
@app.get("/rundeployment/{deployment_id}")
def run_deployment(deployment_id: str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT playbook, inventory, tags, skip_tags FROM deployment_items WHERE deployment_id = ?", (deployment_id,))
    items = c.fetchall()
    conn.close()
    if not items:
        raise HTTPException(status_code=404, detail="Deployment has no playbooks")

    run_id = str(uuid.uuid4())

    def stream_all():
        for item in items:
            playbook, inventory, tags, skip_tags = item
            if not os.path.isfile(resolve_path(playbook)):
                yield f"\n❌ Playbook nicht gefunden: {playbook}\n"
                continue
            if not os.path.isfile(resolve_path(inventory)):
                yield f"\n❌ Inventory nicht gefunden: {inventory}\n"
                continue
            yield from run_playbook_streamed(playbook, inventory, tags, skip_tags, run_id)

    return StreamingResponse(stream_all(), media_type="text/plain")
