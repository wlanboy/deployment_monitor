import argparse
import logging
import os
import signal
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
from runner import NOT_STARTED_STATUS, TIMEOUT_STATUS, extract_hosts, parse_stats
from validation import redact

console = Console()
log = logging.getLogger("deployment_cli")

KILL_GRACE_SECONDS = 5


def _kill_group(proc):
    """Ganze Prozessgruppe beenden (ansible + Worker + ssh), erst SIGTERM, dann SIGKILL."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            return
        try:
            proc.wait(timeout=KILL_GRACE_SECONDS)
            return
        except subprocess.TimeoutExpired:
            continue


def run_command(cmd, timeout):
    """Kommando in eigener Prozessgruppe ausführen; liefert (status, stdout, fehlermeldung)."""
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                start_new_session=True)
    except OSError as e:
        return NOT_STARTED_STATUS, "", f"ansible-playbook konnte nicht gestartet werden: {e.strerror or e}"
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        try:
            stdout, _ = proc.communicate(timeout=KILL_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            stdout = ""
        return TIMEOUT_STATUS, stdout or "", f"Zeitüberschreitung nach {timeout}s"
    except BaseException:
        # Eigene Prozessgruppe bekommt Ctrl+C vom Terminal nicht mit – selbst beenden
        _kill_group(proc)
        raise
    return proc.returncode, stdout, stderr.strip() if proc.returncode != 0 else ""


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
        status, stdout, error_msg = run_command(cmd, timeout)
        stdout = redact(stdout)
        error_msg = redact(error_msg)
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
        table.add_row("Hosts Unreachable", str(stats.get('unreachable', 0)))
        table.add_row("Hosts", ", ".join(hosts) if hosts else "—")

        console.print(table)

        if status == 0:
            console.print(f"[green]Playbook '{playbook}' erfolgreich abgeschlossen.[/green]")
            break
        console.print(f"[red]Fehler beim Playbook '{playbook}'[/red]")
        console.print(error_msg)
        if status == NOT_STARTED_STATUS:
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
    continue_on_failure = bool((config.get("deployment") or {}).get("continue_on_failure", False))
    db.init_db()

    lock = WorkspaceLock(os.path.join(os.path.dirname(db.DB_PATH) or ".", "workspace.lock"))
    if not lock.acquire():
        console.print("[red]Es läuft bereits ein Deployment oder Git-Vorgang.[/red]")
        return 1

    run_id = str(uuid.uuid4())
    db.start_run(run_id)
    playbooks = config.get("playbooks") or []
    total_duration = 0
    failed = []
    try:
        console.rule(f"[bold magenta]🚀 Deployment gestartet (Run-ID: {run_id})")
        for index, pb in enumerate(playbooks):
            start = time.time()
            status = run_playbook(pb["file"], max(1, int(pb.get("retries", 1))), run_id, inventory_file, config)
            total_duration += int(time.time() - start)
            if status != 0:
                failed.append(pb["file"])
                skipped = [p["file"] for p in playbooks[index + 1:]]
                if skipped and not continue_on_failure:
                    console.print(f"[yellow]⏭ Abbruch nach Fehler – übersprungen: {', '.join(skipped)}[/yellow]")
                    break
        db.finish_run(run_id, db.RUN_FAILED if failed else db.RUN_SUCCESS)
    finally:
        db.finish_run(run_id, db.RUN_ABORTED)  # no-op, wenn oben bereits ein Ergebnis gesetzt wurde
        lock.release()
    db.prune_runs(int((config.get("history") or {}).get("retention_days", 90)))

    if failed:
        console.rule(f"[bold red]❌ Fehlgeschlagen: {', '.join(failed)} (Gesamtdauer: {total_duration}s)")
        return 1
    console.rule(f"[bold green]✅ Alle Playbooks abgeschlossen (Gesamtdauer: {total_duration}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
