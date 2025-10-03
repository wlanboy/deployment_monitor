import subprocess, time, yaml
from datetime import datetime
from db import init_db, log_deployment
from metrics import push_metrics
from rich.console import Console
from rich.table import Table
from rich import box

console = Console()

def run_playbook(playbook, retries):
    for attempt in range(1, retries + 1):
        console.rule(f"[bold cyan]▶ Starte Playbook: {playbook} (Versuch {attempt}/{retries})")

        start = datetime.now()
        start_ts = time.time()

        result = subprocess.run(["ansible-playbook", playbook], capture_output=True, text=True)
        end_ts = time.time()
        end = datetime.now()

        duration = int(end_ts - start_ts)
        status = result.returncode

        log_deployment(playbook, start.isoformat(), end.isoformat(), duration, status, attempt)
        push_metrics(playbook, duration, status, attempt, config)

        # Ausgabe des Ergebnisses
        table = Table(title=f"Ergebnis: {playbook}", box=box.SIMPLE)
        table.add_column("Feld", style="bold")
        table.add_column("Wert")

        table.add_row("Startzeit", start.strftime("%H:%M:%S"))
        table.add_row("Endzeit", end.strftime("%H:%M:%S"))
        table.add_row("Dauer (s)", str(duration))
        table.add_row("Status", "✅ Erfolgreich" if status == 0 else "❌ Fehler")
        table.add_row("Versuch", str(attempt))

        console.print(table)

        if status != 0:
            console.print(f"[red]Fehler beim Playbook '{playbook}' (Versuch {attempt})[/red]")
            console.print(result.stderr.strip())
        else:
            console.print(f"[green]Playbook '{playbook}' erfolgreich abgeschlossen.[/green]")
            break

if __name__ == "__main__":
    init_db()
    with open("config.yaml") as f:
        config = yaml.safe_load(f)

    console.rule("[bold magenta]🚀 Deployment gestartet")
    for pb in config["playbooks"]:
        run_playbook(pb["file"], pb["retries"])
    console.rule("[bold green]✅ Alle Playbooks abgeschlossen")
