import argparse
import logging
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime

import yaml
from rich import box
from rich.console import Console
from rich.table import Table

import db
from locking import WorkspaceLock
from logging_setup import setup_logging
from metrics import push_metrics
from runner import TIMEOUT_STATUS, extract_hosts, parse_stats
from validation import redact

console = Console()
log = logging.getLogger("deployment_cli")


def run_playbook(playbook, retries, run_id, inventory_file, config):
    """Playbook mit bis zu `retries` Versuchen ausführen; liefert den Exit-Code des letzten Versuchs."""
    timeout = (config.get("ansible") or {}).get("timeout_seconds", 3600)
    status = TIMEOUT_STATUS
    for attempt in range(1, retries + 1):
        console.rule(f"[bold cyan]▶ Starte Playbook: {playbook} (Versuch {attempt}/{retries})")

        start = datetime.now().astimezone()
        start_ts = time.time()

        cmd = ["ansible-playbook", playbook, "-i", inventory_file]
        console.print(cmd)
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=timeout)
            status = result.returncode
            stdout = redact(result.stdout)
            error_msg = redact(result.stderr.strip()) if status != 0 else ""
        except subprocess.TimeoutExpired:
            status = TIMEOUT_STATUS
            stdout = ""
            error_msg = f"Zeitüberschreitung nach {timeout}s"
        end_ts = time.time()
        end = datetime.now().astimezone()

        duration = int(end_ts - start_ts)
        stats = parse_stats(stdout)
        hosts = extract_hosts(stdout)

        db.log_run(run_id, playbook, inventory_file, start.isoformat(), end.isoformat(), duration, status, attempt)
        push_metrics(playbook, duration, status, attempt, stats, config)
        log.info("playbook_finished", extra={"run_id": run_id, "playbook": playbook, "status": status,
                                             "attempt": attempt, "duration_seconds": duration})

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
        table.add_row("Hosts", ", ".join(hosts) if hosts else "—")

        console.print(table)

        if status != 0:
            console.print(f"[red]Fehler beim Playbook '{playbook}'[/red]")
            console.print(error_msg)
        else:
            console.print(f"[green]Playbook '{playbook}' erfolgreich abgeschlossen.[/green]")
            break
    return status


def main(argv=None):
    parser = argparse.ArgumentParser(description="Deployment Runner")
    parser.add_argument("--inventory", "-i", required=True, help="Pfad zur Inventar-Datei")
    parser.add_argument("--config", "-c", default="config.yaml", help="Pfad zur config.yaml")
    args = parser.parse_args(argv)

    setup_logging()
    inventory_file = args.inventory
    if not os.path.isfile(inventory_file):
        console.print(f"[red]Inventar-Datei nicht gefunden: {inventory_file}[/red]")
        return 1

    with open(args.config) as f:
        config = yaml.safe_load(f) or {}
    db.init_db()

    lock = WorkspaceLock(os.path.join(os.path.dirname(db.DB_PATH) or ".", "workspace.lock"))
    if not lock.acquire():
        console.print("[red]Es läuft bereits ein Deployment oder Git-Vorgang.[/red]")
        return 1

    try:
        run_id = str(uuid.uuid4())
        console.rule(f"[bold magenta]🚀 Deployment gestartet (Run-ID: {run_id})")
        total_duration = 0
        failed = []

        for pb in config.get("playbooks") or []:
            start = time.time()
            if run_playbook(pb["file"], max(1, int(pb.get("retries", 1))), run_id, inventory_file, config) != 0:
                failed.append(pb["file"])
            total_duration += int(time.time() - start)
    finally:
        lock.release()

    if failed:
        console.rule(f"[bold red]❌ Fehlgeschlagen: {', '.join(failed)} (Gesamtdauer: {total_duration}s)")
        return 1
    console.rule(f"[bold green]✅ Alle Playbooks abgeschlossen (Gesamtdauer: {total_duration}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
