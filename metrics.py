import socket
from prometheus_client import CollectorRegistry, Gauge, push_to_gateway

def push_metrics(playbook, duration, status, retries, config):
    registry = CollectorRegistry()
    job = config["prometheus"]["job_name"]
    instance = socket.gethostname()  # aktueller Hostname

    Gauge('deployment_duration_seconds', 'Duration of deployment', ['playbook', 'instance'], registry=registry)\
        .labels(playbook=playbook, instance=instance).set(duration)

    Gauge('deployment_status', '0=success, 1=failure', ['playbook', 'instance'], registry=registry)\
        .labels(playbook=playbook, instance=instance).set(status)

    Gauge('deployment_retries_total', 'Number of retries', ['playbook', 'instance'], registry=registry)\
        .labels(playbook=playbook, instance=instance).set(retries)

    push_to_gateway(config["prometheus"]["pushgateway_url"], job=job, registry=registry)
