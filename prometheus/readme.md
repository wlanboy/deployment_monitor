# Prometheus + Pushgateway Monitoring Setup

Dieses Setup stellt eine einfache Monitoring-Infrastruktur bereit, bestehend aus:

- **Prometheus** zur Metrik-Speicherung und Visualisierung
- **Pushgateway** zur Annahme von Push-Metriken aus Batch-Jobs (z. B. Ansible-Deployments)

## 📦 Komponenten

- `prom/prometheus` (Port: 9090)
- `prom/pushgateway` (Port: 9091)

## 🚀 Schnellstart mit Docker Compose

```bash
docker-compose up -d
```

## 📤 Metriken pushen
Beispiel:

```bash
echo "deployment_duration_seconds{playbook=\"setup\"} 42" \
  | curl --data-binary @- http://localhost:9091/metrics/job/ansible_deployment
```

## 📊 Zugriff
* Prometheus UI: http://localhost:9090
* Pushgateway UI: http://localhost:9091