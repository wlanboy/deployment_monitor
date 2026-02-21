from unittest.mock import patch

import metrics

CONFIG = {
    "prometheus": {
        "job_name": "test_job",
        "pushgateway_url": "http://localhost:9091",
    }
}

STATS_OK = {"ok": 5, "changed": 2, "skipped": 1, "failed": 0}
STATS_FAILED = {"ok": 1, "changed": 0, "skipped": 0, "failed": 2}


class TestPushMetrics:
    def test_calls_push_to_gateway(self):
        with patch("metrics.push_to_gateway") as mock_push:
            metrics.push_metrics("site.yml", 60, 0, 1, STATS_OK, "", "run-1", CONFIG)
        mock_push.assert_called_once()

    def test_uses_configured_job_name(self):
        with patch("metrics.push_to_gateway") as mock_push:
            metrics.push_metrics("site.yml", 60, 0, 1, STATS_OK, "", "run-1", CONFIG)
        _, kwargs = mock_push.call_args
        assert kwargs["job"] == "test_job"

    def test_uses_configured_pushgateway_url(self):
        with patch("metrics.push_to_gateway") as mock_push:
            metrics.push_metrics("site.yml", 60, 0, 1, STATS_OK, "", "run-1", CONFIG)
        args, _ = mock_push.call_args
        assert args[0] == "http://localhost:9091"

    def test_error_msg_gauge_registered_when_present(self):
        # push_to_gateway must be called even with a non-empty error message
        with patch("metrics.push_to_gateway") as mock_push:
            metrics.push_metrics("fail.yml", 10, 1, 2, STATS_FAILED, "Task failed!", "run-2", CONFIG)
        mock_push.assert_called_once()

    def test_no_error_gauge_when_empty_msg(self):
        # Should not raise even without an error message
        with patch("metrics.push_to_gateway"):
            metrics.push_metrics("site.yml", 30, 0, 1, STATS_OK, "", "run-3", CONFIG)

    def test_empty_stats_handled(self):
        with patch("metrics.push_to_gateway") as mock_push:
            metrics.push_metrics("site.yml", 5, 0, 1, {}, "", "run-4", CONFIG)
        mock_push.assert_called_once()
