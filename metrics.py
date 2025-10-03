import socket
from prometheus_client import CollectorRegistry, Gauge, push_to_gateway

def push_metrics(playbook, duration, status, retries, stats, error_msg, run_id, config):
    registry = CollectorRegistry()
    job = config["prometheus"]["job_name"]
    instance = socket.gethostname()

    labels = {'playbook': playbook, 'instance': instance, 'run_id': run_id}

    Gauge('deployment_duration_seconds', 'Duration of deployment', labels.keys(), registry=registry)\
        .labels(**labels).set(duration)

    Gauge('deployment_status', '0=success, 1=failure', labels.keys(), registry=registry)\
        .labels(**labels).set(status)

    Gauge('deployment_retries_total', 'Number of retries', labels.keys(), registry=registry)\
        .labels(**labels).set(retries)

    Gauge('deployment_task_count_total', 'Total tasks', labels.keys(), registry=registry)\
        .labels(**labels).set(stats.get('ok', 0) + stats.get('changed', 0) + stats.get('skipped', 0) + stats.get('failed', 0))

    Gauge('deployment_failed_tasks_total', 'Failed tasks', labels.keys(), registry=registry)\
        .labels(**labels).set(stats.get('failed', 0))

    Gauge('deployment_changed_tasks_total', 'Changed tasks', labels.keys(), registry=registry)\
        .labels(**labels).set(stats.get('changed', 0))

    Gauge('deployment_skipped_tasks_total', 'Skipped tasks', labels.keys(), registry=registry)\
        .labels(**labels).set(stats.get('skipped', 0))

    Gauge('deployment_errors_total', 'Errors detected', labels.keys(), registry=registry)\
        .labels(**labels).set(1 if status != 0 else 0)

    if error_msg:
        Gauge('deployment_last_error_message', 'Last error message hash', labels.keys(), registry=registry)\
            .labels(**labels).set(abs(hash(error_msg)) % 10_000_000)

    push_to_gateway(config["prometheus"]["pushgateway_url"], job=job, registry=registry)
