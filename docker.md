# 🐳 Deployment API – Docker Setup

Diese Anleitung beschreibt, wie du die FastAPI-basierte Deployment-API in einem Docker-Container ausführst, inklusive Ansible-Integration und Zugriff auf SSH-Keys und Playbooks.

---

## 📦 1. Docker-Image bauen

Stelle sicher, dass folgende Dateien im Verzeichnis liegen:

- `Dockerfile`
- `requirements.txt`
- `api.py`
- `config.yaml`

Dann baue das Image:

```bash
docker build -t deployment-monitor  .
```

## 🔐 2. SSH-Keys und Playbooks einbinden
Die API benötigt Zugriff auf:
* deine SSH-Keys (~/.ssh)
* deine Playbooks und Inventories (base_path aus config.yaml)
* passenden ansible_user in den Inventories, sonst nimmt ansible den user aus dem docker image

## 🚀 3. Container starten (lokal)
```bash
docker run -it --rm \
  -v ~/.ssh:/home/deployuser/.ssh:ro \
  -v /opt/dpl:/opt/dpl \
  -e PUSHGATEWAY_URL=http://localhost:9091 \
  -p 8000:8000 \
  deployment-monitor
```

## 🚀 4. Container starten mit docker compose und dockerhub image
```bash
docker-compose up
```

## (for info) Container taggen und publishen
```bash
docker login
# use browser -> https://login.docker.com/activate
docker tag deployment-monitor wlanboy/deployment-monitor
docker push wlanboy/deployment-monitor:latest
```