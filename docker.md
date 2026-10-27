# 🐳 Deployment API – Docker Setup

Diese Anleitung beschreibt, wie du die FastAPI-basierte Deployment-API in einem Docker-Container ausführst, inklusive Ansible-Integration und Zugriff auf SSH-Keys und Playbooks.

---

## 📦 1. Docker-Image bauen

Stelle sicher, dass folgende Dateien im Verzeichnis liegen:

- `Dockerfile`
- `requirements.txt`
- `api.py`
- `*.py` (api.py, db.py, metrics.py, …)
- `config.yaml`

Dann baue das Image:

```bash
docker build -t deployment-monitor  .
```

## 🔐 2. SSH-Keys und Playbooks einbinden
Die API benötigt Zugriff auf:
* deine SSH-Keys (~/.ssh) – besser ein eigener Deploy-Key statt aller Keys des Users
* deine Playbooks und Inventories (base_path aus config.yaml)
* passenden ansible_user in den Inventories, sonst nimmt ansible den user aus dem docker image

## 🚀 3. Container starten (lokal)
```bash
docker run -it --rm \
  -v ~/.ssh:/home/deployuser/.ssh:ro \
  -v /opt/dpl:/opt/dpl \
  -v deployment-db:/app/deploymentjobs \
  -e PUSHGATEWAY_URL=http://host.docker.internal:9091 \
  -e DEPLOYMENT_API_TOKEN \
  --add-host host.docker.internal:host-gateway \
  --read-only --tmpfs /tmp --tmpfs /home/deployuser/.ansible \
  --cap-drop ALL --security-opt no-new-privileges \
  -p 8000:8000 \
  deployment-monitor
```

`--read-only` macht das Root-Dateisystem schreibgeschützt; beschreibbar sind nur die Volumes (Datenbank, `/opt/dpl` für git) und die tmpfs-Mounts für Ansible. `-e DEPLOYMENT_API_TOKEN` übernimmt den Token aus der Shell (siehe README, Abschnitt Sicherheit).

Hinweis: `localhost` im Container ist der Container selbst. Das Pushgateway aus `prometheus/docker-compose.yaml` läuft auf dem Host und ist über `host.docker.internal` erreichbar.

## 🚀 4. Container starten mit docker compose und dockerhub image
```bash
echo "DEPLOYMENT_API_TOKEN=$(openssl rand -hex 32)" > .env
docker-compose up
```
Die Compose-Datei setzt dieselbe Härtung (read-only, tmpfs, `cap_drop: ALL`, `no-new-privileges`).

## (for info) Container taggen und publishen
```bash
docker login
# use browser -> https://login.docker.com/activate
docker tag deployment-monitor wlanboy/deployment-monitor
docker push wlanboy/deployment-monitor:latest
```