from prometheus_client import CollectorRegistry, Gauge, push_to_gateway

def push_metrics(playbook, duration, status, retries, config):
    registry = CollectorRegistry()
    job = config["prometheus"]["job_name"]

    Gauge('deployment_duration_seconds', 'Duration of deployment', ['playbook'], registry=registry)\
        .labels(playbook=playbook).set(duration)

    Gauge('deployment_status', '0=success, 1=failure', ['playbook'], registry=registry)\
        .labels(playbook=playbook).set(status)

    Gauge('deployment_retries_total', 'Number of retries', ['playbook'], registry=registry)\
        .labels(playbook=playbook).set(retries)

    push_to_gateway(config["prometheus"]["pushgateway_url"], job=job, registry=registry)
