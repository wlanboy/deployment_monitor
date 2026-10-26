from unittest.mock import patch

from prometheus_client import generate_latest

import metrics

CONFIG = {
    "prometheus": {
        "job_name": "test_job",
        "pushgateway_url": "http://localhost:9091",
        "instance": "test-host",
    }
}

STATS_OK = {"ok": 5, "changed": 2, "skipped": 1, "failed": 0}
STATS_FAILED = {"ok": 1, "changed": 0, "skipped": 0, "failed": 2}

EXPECTED_METRICS = {
    "deployment_duration_seconds", "deployment_status", "deployment_errors", "deployment_retries",
    "deployment_ok_tasks", "deployment_changed_tasks", "deployment_skipped_tasks", "deployment_failed_tasks",
    "deployment_task_count", "deployment_last_run_timestamp_seconds",
}


def pushed_samples(mock_push):
    registry = mock_push.call_args.kwargs["registry"]
    return {m.name: m.samples[0] for m in registry.collect()}


class TestPushMetrics:
    def test_calls_push_to_gateway(self):
        with patch("metrics.push_to_gateway") as mock_push:
            assert metrics.push_metrics("site.yml", 60, 0, 1, STATS_OK, CONFIG)
        mock_push.assert_called_once()

    def test_uses_configured_job_name(self):
        with patch("metrics.push_to_gateway") as mock_push:
            metrics.push_metrics("site.yml", 60, 0, 1, STATS_OK, CONFIG)
        assert mock_push.call_args.kwargs["job"] == "test_job"

    def test_uses_configured_pushgateway_url(self):
        with patch("metrics.push_to_gateway") as mock_push:
            metrics.push_metrics("site.yml", 60, 0, 1, STATS_OK, CONFIG)
        assert mock_push.call_args.args[0] == "http://localhost:9091"

    def test_env_overrides_pushgateway_url(self, monkeypatch):
        monkeypatch.setenv("PUSHGATEWAY_URL", "http://gateway:9091")
        with patch("metrics.push_to_gateway") as mock_push:
            metrics.push_metrics("site.yml", 60, 0, 1, STATS_OK, CONFIG)
        assert mock_push.call_args.args[0] == "http://gateway:9091"

    def test_has_timeout(self):
        with patch("metrics.push_to_gateway") as mock_push:
            metrics.push_metrics("site.yml", 60, 0, 1, STATS_OK, CONFIG)
        assert mock_push.call_args.kwargs["timeout"] == metrics.PUSH_TIMEOUT_SECONDS

    def test_grouping_key_per_playbook_without_run_id(self):
        with patch("metrics.push_to_gateway") as mock_push:
            metrics.push_metrics("playbooks/site.yml", 60, 0, 1, STATS_OK, CONFIG)
        assert mock_push.call_args.kwargs["grouping_key"] == {"playbook": "playbooks/site.yml", "instance": "test-host"}

    def test_pushes_expected_gauges(self):
        with patch("metrics.push_to_gateway") as mock_push:
            metrics.push_metrics("fail.yml", 10, 2, 3, STATS_FAILED, CONFIG)
        samples = pushed_samples(mock_push)
        assert set(samples) == EXPECTED_METRICS
        assert samples["deployment_status"].value == 2
        assert samples["deployment_errors"].value == 1
        assert samples["deployment_retries"].value == 3
        assert samples["deployment_failed_tasks"].value == 2
        assert samples["deployment_task_count"].value == 3

    def test_hostile_playbook_name_cannot_inject_metrics(self):
        playbook = 'x"} 1\ninjected_metric{a="b'
        with patch("metrics.push_to_gateway") as mock_push:
            metrics.push_metrics(playbook, 10, 0, 1, STATS_OK, CONFIG)
        exposition = generate_latest(mock_push.call_args.kwargs["registry"]).decode()
        assert "injected_metric" not in exposition
        assert mock_push.call_args.kwargs["grouping_key"]["playbook"] == playbook

    def test_empty_stats_handled(self):
        with patch("metrics.push_to_gateway") as mock_push:
            metrics.push_metrics("site.yml", 5, 0, 1, {}, CONFIG)
        assert pushed_samples(mock_push)["deployment_task_count"].value == 0

    def test_push_error_does_not_raise(self):
        with patch("metrics.push_to_gateway", side_effect=OSError("connection refused")):
            assert metrics.push_metrics("site.yml", 5, 1, 1, {}, CONFIG) is False

    def test_defaults_without_prometheus_config(self):
        with patch("metrics.push_to_gateway") as mock_push:
            metrics.push_metrics("site.yml", 5, 0, 1, {}, {})
        assert mock_push.call_args.kwargs["job"] == "ansible_deployment"
        assert mock_push.call_args.args[0] == "http://localhost:9091"
