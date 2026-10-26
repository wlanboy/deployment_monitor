# Ansible Deployment Monitor

Dieses Tool führt mehrere Ansible-Playbooks aus, misst deren Laufzeit und Status, speichert Metriken in SQLite und pusht sie an Prometheus Pushgateway.

## 📦 Features

- Ausführung mehrerer Playbooks mit Retry-Logik
- Übergabe von Inventories
- Zeitmessung, Exit-Code, Anzahl der Versuche
- Speicherung von Deployments und Lauf-Historie in `deploymentjobs/deploymentjobs.db` (SQLite)
- Push von Metriken an Prometheus Pushgateway
- REST-API zur Steuerung von Playbooks und git branches
- Grafana-Dashboard zur Visualisierung

## ⚙️ Konfiguration (`config.yaml`)

```yaml
base_path: /opt/dpl

# Nur diese Playbooks und Inventories dürfen über die API verwendet werden
# (Pfade relativ zu base_path, müssen innerhalb von base_path liegen).
playbooks:
  - name: folder
    file: playbooks/folder.yaml
    retries: 1
  - name: file
    file: playbooks/file.yaml
    retries: 1

inventories:
  - inventories/home/home.yaml
  - inventories/test/test.yaml

ansible:
  timeout_seconds: 3600     # danach wird ansible-playbook beendet

git:
  timeout_seconds: 60

limits:
  max_deployments: 1000
  max_items_per_deployment: 50

prometheus:
  job_name: ansible_deployment
  pushgateway_url: http://localhost:9091
  # instance: mein-host     # optional, Standard: Hostname
```

Playbooks oder Inventories, die nicht in der Whitelist stehen oder (per `..`, absolutem Pfad oder Symlink) außerhalb von `base_path` liegen, werden mit HTTP 400 abgelehnt.

### Umgebungsvariablen
| Variable | Standard | Bedeutung |
|---|---|---|
| `DEPLOYMENT_CONFIG` | `config.yaml` | Pfad zur Konfiguration |
| `DEPLOYMENT_DB_PATH` | `deploymentjobs/deploymentjobs.db` | SQLite-Datenbank (API und CLI) |
| `PUSHGATEWAY_URL` | aus `config.yaml` | überschreibt `prometheus.pushgateway_url` |
| `LOG_LEVEL` | `INFO` | Log-Level; Logs gehen als JSON-Zeilen nach stderr |
* Erzeuge dann einen Ordner /opt/dpl, kopiere die Beispiele und initialisiere ein git repro daraus
```bash
sudo mkdir -p /opt/dpl
sudo chown -R $USER:$USER /opt/dpl
sudo chmod -R 775 /opt/dpl
cp -r inventories /opt/dpl/
cp -r playbooks /opt/dpl/
cd /opt/dpl
git init
git branch -M main
git add .
git commit -m "Initial commit of /opt/dpl structure"
git checkout -b feature-test
```

## uv setup
```
curl -LsSf https://astral.sh/uv/install.sh | sh
uv sync
uv pip compile pyproject.toml -o requirements.txt
uv run deploy.py
```

## 🚀 Ausführung (CLI)
```bash
python deploy.py -i inventories/home/home.yaml
# optional: -c pfad/zur/config.yaml
```
Der Exit-Code ist `1`, sobald ein Playbook (nach allen Versuchen) fehlschlägt.

## 🌐 Prometheus
Siehe: https://github.com/wlanboy/deployment_monitor/tree/main/prometheus/readme.md

## 📈 Grafana Dashboard
Siehe: http://localhost:3000/d/deployments/deployments?orgId=1&from=now-15m&to=now&timezone=browser&var-datasource=bezwwgua3ke80f&refresh=30s

![Grafana Dashboard](dashboards/dashboard.png)

#

## 🚀 Starten der API
```bash
.venv/bin/uvicorn api:app --reload --port 8000
```

## 🚀 Starten der API über docker compose
Siehe: https://github.com/wlanboy/deployment_monitor/tree/main/docker.md
```bash
docker-compose up
```

## Swagger API
* http://localhost:8000/docs
![Swagger API](images/swagger-api.png)

## 🚀 Beispiele
```bash
# liste playbooks
curl http://localhost:8000/playbooks

# liste inventories
curl http://localhost:8000/inventories
```
Playbooks werden über Deployments ausgeführt, siehe [Deployment Verwaltung](#deployment-verwaltung-nur-rest-api).

## Ausgabe playbooks
```
["playbooks/folder.yaml","playbooks/file.yaml"]
```

## Ausgabe eines Deployment-Laufs
```
▶ Starte Playbook: playbooks/folder.yaml (Versuch 1/1)
Tags: create

PLAY [Create and verify folder in home directory] ******************************

TASK [Gathering Facts] *********************************************************
ok: [localhost]

TASK [Create folder with 0755 permissions] *************************************
ok: [localhost]

TASK [Check folder permissions] ************************************************
ok: [localhost]

TASK [Fail if permissions are not 0755] ****************************************
skipping: [localhost]

PLAY RECAP *********************************************************************
localhost                  : ok=3    changed=0    unreachable=0    failed=0    skipped=1    rescued=0    ignored=0   


Ergebnis: playbooks/folder.yaml
Start: 14:09:19 | Dauer: 5s | Status: ✅
Hosts: localhost

RESULT=success run_id=0f6c1c2e-8a7e-4f57-a4c5-2f7d1b9e3a10
```

## Deployment Verwaltung (nur rest api)
### 🧱 1. Deployment anlegen
```bash
curl -X POST "http://localhost:8000/deployment?name=ordnerunddatei"

# aus der Antwort die uuid nehmen
{"id":"76aaf757-761d-45a1-9136-ab05c02b7185","name":"ordnerunddatei"}
```

### ➕ 2. Playbooks hinzufügen
#### 🧩 Schritt 1: folder.yaml mit Tags und Skip-Tags
```bash
curl -X PUT "http://localhost:8000/deployment/76aaf757-761d-45a1-9136-ab05c02b7185?playbook=playbooks/folder.yaml&inventory=inventories/home/home.yaml&tags=create&skip_tags=check"

# Antwort
{"status":"added","deployment_id":"76aaf757-761d-45a1-9136-ab05c02b7185"}
```

#### 🧩 Schritt 2: file.yaml ohne Tags
```bash
curl -X PUT "http://localhost:8000/deployment/76aaf757-761d-45a1-9136-ab05c02b7185?playbook=playbooks/file.yaml&inventory=inventories/home/home.yaml"

# Antwort
{"status":"added","deployment_id":"76aaf757-761d-45a1-9136-ab05c02b7185"}
```

### 📦 3. Deployment anzeigen
```bash
curl "http://localhost:8000/deployment/76aaf757-761d-45a1-9136-ab05c02b7185"

# Antwort
{"id":"76aaf757-761d-45a1-9136-ab05c02b7185","name":"ordnerunddatei",
"items":[
{"playbook":"playbooks/folder.yaml","inventory":"inventories/home/home.yaml","tags":"create","skip_tags":"check"},{"playbook":"playbooks/file.yaml","inventory":"inventories/home/home.yaml","tags":null,"skip_tags":null}
]}
```

### 🚀 4. Deployment ausführen
```bash
curl -X POST "http://localhost:8000/rundeployment/76aaf757-761d-45a1-9136-ab05c02b7185"
```
* Die Ausgabe wird gestreamt. Die letzte Zeile ist maschinenlesbar: `RESULT=success|failed run_id=<uuid>`.
* Die Run-ID steht zusätzlich im Header `X-Run-Id`.
* Ungültige oder fehlende Playbooks/Inventories werden **vor** dem Start mit HTTP 400/404 abgelehnt.
* Es läuft immer nur ein Deployment oder Git-Vorgang gleichzeitig; ein weiterer Aufruf bekommt HTTP 409.
* Bricht der Client die Verbindung ab, wird `ansible-playbook` beendet.

### 📜 5. Ergebnis eines Laufs abfragen
```bash
curl "http://localhost:8000/runs/<run_id>"

# Antwort
{"run_id":"...","deployment_id":"...","result":"success",
 "attempts":[{"playbook":"playbooks/folder.yaml","inventory":"inventories/home/home.yaml",
              "start_time":"...","end_time":"...","duration":2,"status":0,"attempt":1}]}
```
`status` ist der Exit-Code von ansible-playbook; `124` = Zeitüberschreitung, `-1` = abgebrochen.

## 📋 Git-Status anzeigen
```bash
curl "http://localhost:8000/git/status"
```

## 🔀 Branch wechseln
```bash
curl -X POST "http://localhost:8000/git/checkout/feature-test"
curl -X POST "http://localhost:8000/git/checkout/feature/neues-playbook"
```
Branch-Namen werden validiert (keine Optionen wie `-f`, kein `..` usw.); ungültige Namen und fehlgeschlagene Checkouts liefern HTTP 400, ein fehlgeschlagenes `git fetch` HTTP 502. Zugangsdaten in URLs werden in allen Ausgaben maskiert.

## ⚠️ Sicherheit
Die API hat **keine Authentifizierung**. Wer Port 8000 erreicht, kann Playbooks mit den eingebundenen SSH-Keys ausführen. Betreibe sie nur in einem vertrauenswürdigen Netz bzw. hinter einem Reverse-Proxy mit Authentifizierung und TLS.