import logging
import os
import socket
import time

from prometheus_client import CollectorRegistry, Gauge, push_to_gateway

log = logging.getLogger(__name__)

PUSH_TIMEOUT_SECONDS = 5


def pushgateway_url(config):
    return os.getenv("PUSHGATEWAY_URL") or (config.get("prometheus") or {}).get("pushgateway_url", "http://localhost:9091")


def push_metrics(playbook, duration, status, attempt, stats, config) -> bool:
    """Metriken des letzten Laufs eines Playbooks pushen. Fehler werden geloggt, nie geworfen.

    Playbook und Instanz bilden den Grouping-Key: jede Kombination behält im
    Pushgateway genau ihren letzten Stand, ohne pro Lauf neue Zeitreihen zu erzeugen.
    Labels werden von prometheus_client escaped bzw. base64-kodiert.
    """
    prom = config.get("prometheus") or {}
    job = prom.get("job_name", "ansible_deployment")
    instance = prom.get("instance") or socket.gethostname()
    registry = CollectorRegistry()

    def gauge(name, documentation, value):
        Gauge(name, documentation, registry=registry).set(value)

    gauge('deployment_duration_seconds', 'Dauer des letzten Laufs in Sekunden', duration)
    gauge('deployment_status', 'Exit-Code des letzten Laufs (0 = Erfolg)', status)
    gauge('deployment_errors', '1 wenn der letzte Lauf fehlgeschlagen ist', 1 if status != 0 else 0)
    gauge('deployment_retries', 'Benötigte Versuche im letzten Lauf', attempt)
    gauge('deployment_ok_tasks', 'OK-Tasks im letzten Lauf', stats.get('ok', 0))
    gauge('deployment_changed_tasks', 'Geänderte Tasks im letzten Lauf', stats.get('changed', 0))
    gauge('deployment_skipped_tasks', 'Übersprungene Tasks im letzten Lauf', stats.get('skipped', 0))
    gauge('deployment_failed_tasks', 'Fehlgeschlagene Tasks im letzten Lauf', stats.get('failed', 0))
    gauge('deployment_unreachable_hosts', 'Nicht erreichbare Hosts im letzten Lauf', stats.get('unreachable', 0))
    gauge('deployment_task_count','Tasks gesamt im letzten Lauf',
          stats.get('ok', 0) + stats.get('changed', 0) + stats.get('skipped', 0) + stats.get('failed', 0))
    gauge('deployment_last_run_timestamp_seconds', 'Zeitpunkt des letzten Laufs (Unix-Zeit)', time.time())

    url = pushgateway_url(config)
    try:
        push_to_gateway(url, job=job, registry=registry,
                        grouping_key={'playbook': playbook, 'instance': instance},
                        timeout=PUSH_TIMEOUT_SECONDS)
    except Exception as e:  # noqa: BLE001 - metrics push must never crash the deployment
        log.warning("metrics_push_failed", extra={"pushgateway_url": url, "playbook": playbook, "error": str(e)})
        return False
    return True
