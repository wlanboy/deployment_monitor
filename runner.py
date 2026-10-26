import re

# Exit-Codes, die nicht von ansible-playbook selbst kommen
TIMEOUT_STATUS = 124
NOT_STARTED_STATUS = 127
ABORTED_STATUS = -1

# Eine Zeile pro Host im PLAY RECAP, z.B.
# "web01  : ok=4  changed=1  unreachable=0  failed=0  skipped=2  rescued=0  ignored=0"
_RECAP_LINE = re.compile(
    r"^\s*(\S+)\s+:\s+ok=(\d+)\s+changed=(\d+)\s+unreachable=(\d+)\s+failed=(\d+)\s+skipped=(\d+)",
    re.MULTILINE,
)


def is_recap_line(line):
    return _RECAP_LINE.match(line) is not None


def parse_stats(output):
    """Task-Zähler über alle Hosts im PLAY RECAP summieren; unreachable zählt Hosts."""
    stats = {'ok': 0, 'changed': 0, 'unreachable': 0, 'failed': 0, 'skipped': 0}
    for match in _RECAP_LINE.finditer(output):
        stats['ok'] += int(match.group(2))
        stats['changed'] += int(match.group(3))
        stats['unreachable'] += int(match.group(4))
        stats['failed'] += int(match.group(5))
        stats['skipped'] += int(match.group(6))
    return stats


def extract_hosts(output):
    return sorted({match.group(1) for match in _RECAP_LINE.finditer(output)})
