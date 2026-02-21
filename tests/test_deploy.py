from deploy import extract_hosts, parse_stats

# Realistic ansible PLAY RECAP line
RECAP_LINE = "web01                      : ok=5 changed=2 unreachable=0 failed=0 skipped=1"


class TestParseStats:
    def test_full_match(self):
        stats = parse_stats(RECAP_LINE)
        assert stats["ok"] == 5
        assert stats["changed"] == 2
        assert stats["failed"] == 0
        assert stats["skipped"] == 1

    def test_no_match_returns_zeros(self):
        stats = parse_stats("no recap information here")
        assert stats == {"ok": 0, "changed": 0, "skipped": 0, "failed": 0}

    def test_failed_tasks_counted(self):
        line = "db01                       : ok=1 changed=0 unreachable=0 failed=3 skipped=0"
        stats = parse_stats(line)
        assert stats["failed"] == 3

    def test_changed_tasks_counted(self):
        line = "app01                      : ok=4 changed=3 unreachable=0 failed=0 skipped=2"
        stats = parse_stats(line)
        assert stats["changed"] == 3
        assert stats["skipped"] == 2

    def test_empty_string(self):
        stats = parse_stats("")
        assert stats == {"ok": 0, "changed": 0, "skipped": 0, "failed": 0}


class TestExtractHosts:
    def test_single_host(self):
        hosts = extract_hosts(RECAP_LINE)
        assert hosts == ["web01"]

    def test_multiple_hosts_sorted(self):
        output = (
            "z-host                     : ok=1 changed=0 unreachable=0 failed=0 skipped=0\n"
            "a-host                     : ok=2 changed=1 unreachable=0 failed=0 skipped=0"
        )
        hosts = extract_hosts(output)
        assert hosts == ["a-host", "z-host"]

    def test_no_hosts(self):
        hosts = extract_hosts("PLAY [all] *****\nTASK [ping] ****")
        assert hosts == []

    def test_empty_string(self):
        assert extract_hosts("") == []

    def test_deduplicated(self):
        # Same host appearing twice should only show once
        output = (
            "web01                      : ok=5 changed=0 unreachable=0 failed=0 skipped=0\n"
            "web01                      : ok=3 changed=1 unreachable=0 failed=0 skipped=0"
        )
        hosts = extract_hosts(output)
        assert hosts == ["web01"]
