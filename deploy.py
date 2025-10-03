import subprocess, time, yaml, uuid, re
from datetime import datetime
from db import init_db, log_deployment
from metrics import push_metrics
from rich.console import Console
from rich.table import Table
from rich import box

console = Console()

def parse_stats(output):
    stats = {'ok': 0, 'changed': 0, 'skipped': 0, 'failed': 0}
    # Suche nach der Zeile mit Task-Zusammenfassung
    match = re.search(
        r"localhost\s+.*?ok=(\d+)\s+changed=(\d+)\s+unreachable=(\d+)\s+failed=(\d+)\s+skipped=(\d+)",
        output
    )
    if match:
        stats['ok'] = int(match.group(1))
        stats['changed'] = int(match.group(2))
        stats['failed'] = int(match.group(4))
        stats['skipped'] = int(match.group(5))
    return stats

def run_playbook(playbook, retries, run_id):
    for attempt in range(1, retries + 1):
        console.rule(f"[bold cyan]▶ Starte Playbook: {playbook} (Versuch {attempt}/{retries})")

        start = datetime.now()
        start_ts = time.time()

        result = subprocess.run(["ansible-playbook", playbook], capture_output=True, text=True)
        end_ts = time.time()
        end = datetime.now()

        duration = int(end_ts - start_ts)
        status = result.returncode
        error_msg = result.stderr.strip() if status != 0 else ""
        stats = parse_stats(result.stdout)

        log_deployment(playbook, start.isoformat(), end.isoformat(), duration, status, attempt)
        push_metrics(playbook, duration, status, attempt, stats, error_msg, run_id, config)

        table = Table(title=f"Ergebnis: {playbook}", box=box.SIMPLE)
        table.add_column("Feld", style="bold")
        table.add_column("Wert")
        table.add_row("Startzeit", start.strftime("%H:%M:%S"))
        table.add_row("Endzeit", end.strftime("%H:%M:%S"))
        table.add_row("Dauer (s)", str(duration))
        table.add_row("Status", "✅ Erfolgreich" if status == 0 else "❌ Fehler")
        table.add_row("Versuch", str(attempt))
        table.add_row("Tasks OK", str(stats.get('ok', 0)))
        table.add_row("Tasks Changed", str(stats.get('changed', 0)))
        table.add_row("Tasks Skipped", str(stats.get('skipped', 0)))
        table.add_row("Tasks Failed", str(stats.get('failed', 0)))

        console.print(table)

        if status != 0:
            console.print(f"[red]Fehler beim Playbook '{playbook}'[/red]")
            console.print(error_msg)
        else:
            console.print(f"[green]Playbook '{playbook}' erfolgreich abgeschlossen.[/green]")
            break

if __name__ == "__main__":
    init_db()
    with open("config.yaml") as f:
        config = yaml.safe_load(f)

    run_id = str(uuid.uuid4())
    console.rule(f"[bold magenta]🚀 Deployment gestartet (Run-ID: {run_id})")
    total_duration = 0

    for pb in config["playbooks"]:
        start = time.time()
        run_playbook(pb["file"], pb["retries"], run_id)
        total_duration += int(time.time() - start)

    console.rule(f"[bold green]✅ Alle Playbooks abgeschlossen (Gesamtdauer: {total_duration}s)")
