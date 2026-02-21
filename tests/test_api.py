from unittest.mock import patch

import api

# Realistic ansible PLAY RECAP line
RECAP_LINE = "web01                      : ok=4 changed=1 unreachable=0 failed=0 skipped=2"


class TestParseStats:
    def test_full_match(self):
        stats = api.parse_stats(RECAP_LINE)
        assert stats["ok"] == 4
        assert stats["changed"] == 1
        assert stats["failed"] == 0
        assert stats["skipped"] == 2

    def test_no_match_returns_zeros(self):
        stats = api.parse_stats("nothing relevant here")
        assert stats == {"ok": 0, "changed": 0, "skipped": 0, "failed": 0}

    def test_failed_tasks(self):
        line = "db01                       : ok=1 changed=0 unreachable=0 failed=2 skipped=0"
        assert api.parse_stats(line)["failed"] == 2


class TestExtractHosts:
    def test_extracts_hostname(self):
        hosts = api.extract_hosts(RECAP_LINE)
        assert hosts == ["web01"]

    def test_returns_sorted_list(self):
        output = (
            "z-node                     : ok=1 changed=0 unreachable=0 failed=0 skipped=0\n"
            "a-node                     : ok=2 changed=1 unreachable=0 failed=0 skipped=0"
        )
        assert api.extract_hosts(output) == ["a-node", "z-node"]

    def test_empty_output(self):
        assert api.extract_hosts("") == []

    def test_non_recap_lines_ignored(self):
        output = "PLAY [all] ****\nTASK [Gathering Facts] ****"
        assert api.extract_hosts(output) == []


class TestResolvePath:
    def test_no_base_path(self, monkeypatch):
        monkeypatch.setattr(api, "base_path", "")
        assert api.resolve_path("playbooks/site.yml") == "playbooks/site.yml"

    def test_with_base_path(self, monkeypatch):
        monkeypatch.setattr(api, "base_path", "/opt/ansible")
        result = api.resolve_path("playbooks/site.yml")
        assert result == "/opt/ansible/playbooks/site.yml"

    def test_base_path_joined_correctly(self, monkeypatch):
        monkeypatch.setattr(api, "base_path", "/srv")
        assert api.resolve_path("inventory/hosts") == "/srv/inventory/hosts"


class TestPushMetrics:
    def test_sends_post_request(self, monkeypatch):
        monkeypatch.setattr(api, "config", {"prometheus": {"job_name": "test", "pushgateway_url": "http://gw:9091"}})
        with patch("api.requests.post") as mock_post:
            api.push_metrics("site.yml", 30, 0, 1, {"ok": 5, "changed": 1, "skipped": 0, "failed": 0}, "run-1")
        mock_post.assert_called_once()

    def test_url_contains_job_name(self, monkeypatch):
        monkeypatch.setattr(api, "config", {"prometheus": {"job_name": "myjob", "pushgateway_url": "http://gw:9091"}})
        with patch("api.requests.post") as mock_post:
            api.push_metrics("site.yml", 10, 0, 1, {}, "run-2")
        url = mock_post.call_args[0][0]
        assert "myjob" in url

    def test_request_error_does_not_raise(self, monkeypatch):
        monkeypatch.setattr(api, "config", {"prometheus": {"job_name": "j", "pushgateway_url": "http://gw:9091"}})
        with patch("api.requests.post", side_effect=Exception("connection refused")):
            # push_metrics swallows the exception – must not propagate
            api.push_metrics("site.yml", 5, 1, 1, {}, "run-3")

    def test_metrics_body_contains_playbook_label(self, monkeypatch):
        monkeypatch.setattr(api, "config", {"prometheus": {"job_name": "j", "pushgateway_url": "http://gw:9091"}})
        with patch("api.requests.post") as mock_post:
            api.push_metrics("deploy.yml", 20, 0, 1, {"failed": 0, "changed": 2, "skipped": 1}, "run-4")
        body = mock_post.call_args[1]["data"]
        assert 'playbook="deploy.yml"' in body
