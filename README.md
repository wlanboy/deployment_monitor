# Ansible Deployment Monitor

Dieses Tool führt mehrere Ansible-Playbooks aus, misst deren Laufzeit und Status, speichert Metriken in SQLite und pusht sie an Prometheus Pushgateway.

## 📦 Features

- Ausführung mehrerer Playbooks mit Retry-Logik
- Zeitmessung, Exit-Code, Anzahl der Versuche
- Speicherung in `deployment.db` (SQLite)
- Push von Metriken an Prometheus Pushgateway
- REST-API zur Abfrage der Metriken via FastAPI (todo)

## ⚙️ Konfiguration (`config.yaml`)

```yaml
playbooks:
  - name: folder
    file: playbooks/folder.yaml
    retries: 1
  - name: file
    file: playbooks/file.yaml
    retries: 1

prometheus:
  job_name: ansible_deployment
  pushgateway_url: http://localhost:9091
```

## uv setup
```
curl -LsSf https://astral.sh/uv/install.sh | sh
uv sync
uv pip compile pyproject.toml -o requirements.txt
uv run deploy.py
```

## 🚀 Ausführung
```bash
python deploy.py
```
