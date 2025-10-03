from fastapi import FastAPI, Query
from fastapi.responses import StreamingResponse, JSONResponse
import subprocess, time, yaml, uuid, re, os
from datetime import datetime
import requests

app = FastAPI(title="Deployment API")

# 📁 Konfiguration laden
try:
    with open("config.yaml") as f:
        config = yaml.safe_load(f)
except Exception as e:
    config = {"playbooks": [], "prometheus": {}}
    print(f"Fehler beim Laden der config.yaml: {e}")

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
        print(f"Fehler beim Pushen der Metriken: {e}")

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
def run_playbook_streamed(playbook, retries, run_id, inventory_file):
    for attempt in range(1, retries + 1):
        yield f"\n▶ Starte Playbook: {playbook} (Versuch {attempt}/{retries})\n"

        start = datetime.now()
        start_ts = time.time()

        cmd = ["ansible-playbook", playbook, "-i", inventory_file]
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)

        output_lines = []
        for line in process.stdout:
            output_lines.append(line)
            yield line

        process.stdout.close()
        process.wait()

        end = datetime.now()
        end_ts = time.time()
        duration = int(end_ts - start_ts)
        status = process.returncode
        error_msg = "" if status == 0 else "Fehler beim Playbook-Ausführung"

        full_output = "".join(output_lines)
        stats = parse_stats(full_output)
        hosts = extract_hosts(full_output)

        push_metrics(playbook, duration, status, attempt, stats, run_id)

        yield f"\nErgebnis: {playbook}\n"
        yield f"Startzeit: {start.strftime('%H:%M:%S')}\n"
        yield f"Endzeit:   {end.strftime('%H:%M:%S')}\n"
        yield f"Dauer (s): {duration}\n"
        yield f"Status:    {'✅ Erfolgreich' if status == 0 else '❌ Fehler'}\n"
        yield f"Versuch:   {attempt}\n"
        yield f"Hosts:     {', '.join(hosts) if hosts else '—'}\n"
        yield f"Tasks OK:      {stats.get('ok', 0)}\n"
        yield f"Tasks Changed: {stats.get('changed', 0)}\n"
        yield f"Tasks Skipped: {stats.get('skipped', 0)}\n"
        yield f"Tasks Failed:  {stats.get('failed', 0)}\n"

        if status == 0:
            yield f"\n✔ Playbook '{playbook}' erfolgreich abgeschlossen.\n"
            break
        else:
            yield f"\n✘ Fehler beim Playbook '{playbook}'\n"

# 🌐 Endpunkte

@app.get("/playbooks")
def list_playbooks():
    return [pb["file"] for pb in config.get("playbooks", [])]

@app.get("/run")
def run(playbook: str = Query(...), inventory: str = Query(...)):
    if not os.path.isfile(playbook):
        return JSONResponse(status_code=404, content={"error": f"Playbook '{playbook}' nicht gefunden."})
    if not os.path.isfile(inventory):
        return JSONResponse(status_code=404, content={"error": f"Inventory '{inventory}' nicht gefunden."})

    run_id = str(uuid.uuid4())
    retries = 1

    return StreamingResponse(
        run_playbook_streamed(playbook, retries, run_id, inventory),
        media_type="text/plain"
    )
