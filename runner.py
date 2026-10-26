import re

# Exit-Codes, die nicht von ansible-playbook selbst kommen
TIMEOUT_STATUS = 124
ABORTED_STATUS = -1


def parse_stats(output):
    stats = {'ok': 0, 'changed': 0, 'skipped': 0, 'failed': 0}
    match = re.search(r"ok=(\d+)\s+changed=(\d+)\s+unreachable=(\d+)\s+failed=(\d+)\s+skipped=(\d+)", output)
    if match:
        stats['ok'] = int(match.group(1))
        stats['changed'] = int(match.group(2))
        stats['failed'] = int(match.group(4))
        stats['skipped'] = int(match.group(5))
    return stats


def extract_hosts(output):
    hosts = set()
    for line in output.splitlines():
        if re.match(r"^\s*\S+\s+:.*ok=", line):
            host = line.split(":")[0].strip()
            hosts.add(host)
    return sorted(hosts)
