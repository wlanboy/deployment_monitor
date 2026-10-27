import asyncio
import fnmatch
import hmac
import logging
import os
import re
import signal
import subprocess
import time
import uuid
from collections.abc import AsyncGenerator, Callable, Iterable
from contextlib import aclosing
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any

import yaml
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.types import Receive, Scope, Send

import db
from locking import WorkspaceLock
from logging_setup import setup_logging
from metrics import push_metrics
from runner import (
    ABORTED_STATUS,
    NOT_STARTED_STATUS,
    TIMEOUT_STATUS,
    extract_hosts,
    is_recap_line,
    parse_stats,
)
from validation import (
    BranchNameError,
    PathNotAllowedError,
    redact,
    resolve_allowed_path,
    validate_branch,
)

setup_logging()
log = logging.getLogger("deployment_api")

bearer = HTTPBearer(auto_error=False)


def api_token() -> str:
    return os.getenv("DEPLOYMENT_API_TOKEN", "")


def require_token(request: Request,
                  credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]) -> None:
    """Ohne DEPLOYMENT_API_TOKEN ist die API offen, sonst nur mit "Authorization: Bearer <token>".

    Ein Authorization-Header erzwingt bei Cross-Origin-Requests einen CORS-Preflight –
    damit greifen weder CSRF über fremde Webseiten noch DNS-Rebinding.
    """
    expected = api_token()
    if not expected:
        return
    given = credentials.credentials if credentials else ""
    if not hmac.compare_digest(given.encode(), expected.encode()):
        log.warning("auth_failed", extra={"client": request.client.host if request.client else None,
                                          "path": request.url.path})
        raise HTTPException(status_code=401, detail="Fehlender oder ungültiger API-Token",
                            headers={"WWW-Authenticate": "Bearer"})


app = FastAPI(title="Deployment API", dependencies=[Depends(require_token)])

CONFIG_PATH = os.getenv("DEPLOYMENT_CONFIG", "config.yaml")

# Eingabe-Grenzen
NAME_PATTERN = r"^[\w .-]+$"
TAGS_PATTERN = r"^[\w.][\w.-]*(,[\w.][\w.-]*)*$"
STREAM_READ_LIMIT = 1024 * 1024
KILL_GRACE_SECONDS = 5


def ansible_env() -> dict[str, str]:
    # Kein __pycache__ neben Plugins im Repo – das wären ungetrackte Dateien (siehe worktree_changes)
    return {**os.environ, "ANSIBLE_NOCOLOR": "1", "PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1"}


def git_env() -> dict[str, str]:
    # Nie interaktiv nach Zugangsdaten fragen – sonst hängt der Request
    return {"GIT_SSH_COMMAND": "ssh -o BatchMode=yes", **os.environ, "GIT_TERMINAL_PROMPT": "0"}


# 📁 Konfiguration laden
def load_config(path: str) -> dict[str, Any]:
    try:
        with open(path) as f:
            loaded = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError) as e:
        log.error("config_load_failed", extra={"path": path, "error": str(e)})
        return {}
    if not isinstance(loaded, dict):
        log.error("config_invalid", extra={"path": path})
        return {}
    return loaded


config: dict[str, Any] = load_config(CONFIG_PATH)
base_path: str = config.get("base_path") or ""


def setting(section: str, key: str, default: Any) -> Any:
    return (config.get(section) or {}).get(key, default)


def workspace_root() -> str:
    return base_path or os.getcwd()


def allowed_playbooks() -> dict[str, int]:
    """Freigegebene Playbooks mit ihrer Anzahl Versuche aus der config.yaml."""
    return {
        pb["file"]: max(1, int(pb.get("retries", 1)))
        for pb in config.get("playbooks") or []
        if isinstance(pb, dict) and pb.get("file")
    }


def allowed_inventories() -> list[str]:
    return [str(inv) for inv in config.get("inventories") or []]


def branch_allowed(branch: str) -> bool:
    """Ohne git.allowed_branches ist jeder Branch erlaubt, sonst nur passende Glob-Muster."""
    patterns = setting("git", "allowed_branches", None)
    if patterns is None:
        return True
    return any(fnmatch.fnmatchcase(branch, str(pattern)) for pattern in patterns)


def lock_path() -> str:
    return os.path.join(os.path.dirname(db.DB_PATH) or ".", "workspace.lock")


def resolve_or_reject(relative: str, allowed: Iterable[str], kind: str) -> str:
    try:
        return resolve_allowed_path(relative, allowed, workspace_root())
    except PathNotAllowedError as e:
        log.warning("path_rejected", extra={"kind": kind, "path": relative, "reason": str(e)})
        raise HTTPException(status_code=400, detail=f"{kind}: {e}") from None
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"{kind} nicht gefunden: {relative}") from None


def check_tags(value: str | None, kind: str) -> None:
    # Ältere Einträge stammen evtl. aus der Zeit vor der Validierung beim Anlegen
    if value is not None and not re.fullmatch(TAGS_PATTERN, value):
        raise HTTPException(status_code=400, detail=f"{kind} ungültig: {value!r}")


def acquire_workspace_lock() -> WorkspaceLock:
    lock = WorkspaceLock(lock_path())
    if not lock.acquire():
        raise HTTPException(status_code=409, detail="Es läuft bereits ein Deployment oder Git-Vorgang")
    return lock


# 🔁 Playbook ausführen + streamen
@dataclass
class PlannedItem:
    playbook: str
    inventory: str
    playbook_path: str
    inventory_path: str
    tags: str | None
    skip_tags: str | None
    retries: int


def _terminate(proc: asyncio.subprocess.Process) -> None:
    """Prozessgruppe (ansible + ssh-Kinder) beenden, ohne zu warten; SIGKILL folgt verzögert."""
    if proc.returncode is not None:
        return

    def signal_group(sig: int) -> None:
        if proc.returncode is None:
            try:
                os.killpg(proc.pid, sig)
            except ProcessLookupError:
                pass

    signal_group(signal.SIGTERM)
    asyncio.get_running_loop().call_later(KILL_GRACE_SECONDS, signal_group, signal.SIGKILL)


async def run_playbook_streamed(item: PlannedItem, run_id: str, deployment_id: str | None,
                                outcome: dict[str, int]) -> AsyncGenerator[str]:
    timeout = float(setting("ansible", "timeout_seconds", 3600))
    loop = asyncio.get_running_loop()
    status = ABORTED_STATUS

    for attempt in range(1, item.retries + 1):
        yield f"\n▶ Starte Playbook: {item.playbook} (Versuch {attempt}/{item.retries})\n"
        if item.tags:
            yield f"Tags: {item.tags}\n"
        if item.skip_tags:
            yield f"Skip-Tags: {item.skip_tags}\n"

        # "--opt=wert" statt "--opt wert": der Wert kann nie als eigene Option gelesen werden
        cmd = ["ansible-playbook", item.playbook_path, "-i", item.inventory_path]
        if item.tags:
            cmd.append(f"--tags={item.tags}")
        if item.skip_tags:
            cmd.append(f"--skip-tags={item.skip_tags}")

        start = datetime.now().astimezone()
        start_ts = time.monotonic()
        log_fields = {"run_id": run_id, "deployment_id": deployment_id, "playbook": item.playbook,
                      "inventory": item.inventory, "attempt": attempt}
        log.info("playbook_started", extra=log_fields)

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                start_new_session=True, limit=STREAM_READ_LIMIT, env=ansible_env(),
            )
        except OSError as e:
            status = NOT_STARTED_STATUS
            log.error("playbook_not_started", extra={**log_fields, "error": str(e)})
            db.log_run(run_id, item.playbook, item.inventory, start.isoformat(),
                       datetime.now().astimezone().isoformat(), 0, status, attempt, deployment_id)
            yield f"\n❌ ansible-playbook konnte nicht gestartet werden: {e.strerror or e}\n"
            break

        assert proc.stdout is not None
        # Nur die PLAY RECAP-Zeilen behalten – die restliche Ausgabe geht direkt an den Client
        recap_lines: list[str] = []
        deadline = loop.time() + timeout
        try:
            while True:
                try:
                    raw = await asyncio.wait_for(proc.stdout.readline(), deadline - loop.time())
                except ValueError:
                    yield "[Zeile zu lang, ausgelassen]\n"
                    continue
                if not raw:
                    break
                line = redact(raw.decode(errors="replace"))
                if is_recap_line(line):
                    recap_lines.append(line)
                yield line
            status = await asyncio.wait_for(proc.wait(), max(deadline - loop.time(), 0))
        except TimeoutError:
            _terminate(proc)
            await proc.wait()
            status = TIMEOUT_STATUS
            log.warning("playbook_timeout", extra={**log_fields, "timeout_seconds": timeout})
            yield f"\n⏱ Zeitüberschreitung nach {int(timeout)}s – Playbook abgebrochen\n"
        finally:
            if proc.returncode is None:
                # Client hat die Verbindung getrennt: ansible nicht weiterlaufen lassen
                _terminate(proc)
                log.warning("playbook_aborted", extra=log_fields)
                db.log_run(run_id, item.playbook, item.inventory, start.isoformat(),
                           datetime.now().astimezone().isoformat(), int(time.monotonic() - start_ts),
                           ABORTED_STATUS, attempt, deployment_id)

        duration = int(time.monotonic() - start_ts)
        end = datetime.now().astimezone()
        recap = "".join(recap_lines)
        stats = parse_stats(recap)
        hosts = extract_hosts(recap)

        db.log_run(run_id, item.playbook, item.inventory, start.isoformat(), end.isoformat(),
                   duration, status, attempt, deployment_id)
        await asyncio.to_thread(push_metrics, item.playbook, duration, status, attempt, stats, config)
        log.info("playbook_finished", extra={**log_fields, "status": status, "duration_seconds": duration, **stats})

        yield f"\nErgebnis: {item.playbook}\nStart: {start.strftime('%H:%M:%S')} | Dauer: {duration}s | Status: {'✅' if status == 0 else '❌'}\n"
        yield f"Hosts: {', '.join(hosts) if hosts else '—'}\n"
        if stats["unreachable"]:
            yield f"Nicht erreichbar: {stats['unreachable']} Host(s)\n"

        if status == 0:
            break

    outcome["status"] = status


async def stream_deployment(plan: list[PlannedItem], run_id: str, deployment_id: str) -> AsyncGenerator[str]:
    continue_on_failure = bool(setting("deployment", "continue_on_failure", False))
    failed: list[str] = []
    for index, item in enumerate(plan):
        outcome: dict[str, int] = {}
        async with aclosing(run_playbook_streamed(item, run_id, deployment_id, outcome)) as stream:
            async for chunk in stream:
                yield chunk
        if outcome.get("status") != 0:
            failed.append(item.playbook)
            skipped = [i.playbook for i in plan[index + 1:]]
            if skipped and not continue_on_failure:
                log.warning("deployment_stopped", extra={"run_id": run_id, "skipped_playbooks": skipped})
                yield f"\n⏭ Abbruch nach Fehler – übersprungen: {', '.join(skipped)}\n"
                break

    result = db.RUN_FAILED if failed else db.RUN_SUCCESS
    db.finish_run(run_id, result)
    db.prune_runs(int(setting("history", "retention_days", 90)))
    log.info("deployment_run_finished", extra={"run_id": run_id, "deployment_id": deployment_id,
                                                "result": result, "failed_playbooks": failed})
    # Maschinenlesbares Ergebnis: der HTTP-Status steht beim Streamen schon vorher fest
    yield f"\nRESULT={result} run_id={run_id}\n"


class ClosingStreamingResponse(StreamingResponse):
    """Schließt den Stream-Generator nach Ende oder Abbruch und ruft danach on_close auf."""

    def __init__(self, content: AsyncGenerator[str], on_close: Callable[[], None], **kwargs: Any):
        super().__init__(content, **kwargs)
        self._content = content
        self._on_close = on_close

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            try:
                await self._content.aclose()
            finally:
                self._on_close()


def recover_interrupted_runs() -> None:
    # Nur wenn gerade nichts läuft – sonst gehört der "running"-Run einem anderen Worker
    lock = WorkspaceLock(lock_path())
    if not lock.acquire():
        return
    try:
        count = db.mark_interrupted_runs()
        if count:
            log.warning("runs_marked_interrupted", extra={"count": count})
    finally:
        lock.release()


db.init_db()
recover_interrupted_runs()
db.prune_runs(int(setting("history", "retention_days", 90)))
if setting("git", "allowed_branches", None) is None:
    log.warning("git_branches_unrestricted", extra={"hint": "git.allowed_branches in der config.yaml setzen"})
if not api_token():
    log.warning("api_unauthenticated", extra={"hint": "DEPLOYMENT_API_TOKEN setzen"})

# 🌐 Endpunkte


@app.get("/playbooks")
def list_playbooks():
    return list(allowed_playbooks())


@app.get("/inventories")
def list_inventories():
    return allowed_inventories()


@app.post("/deployment")
def create_deployment(name: str = Query(..., min_length=1, max_length=100, pattern=NAME_PATTERN)):
    max_deployments = int(setting("limits", "max_deployments", 1000))
    deployment_id = str(uuid.uuid4())
    with db.connect(immediate=True) as conn:
        (count,) = conn.execute("SELECT COUNT(*) FROM deployments").fetchone()
        if count >= max_deployments:
            raise HTTPException(status_code=409, detail=f"Maximal {max_deployments} Deployments erlaubt")
        conn.execute("INSERT INTO deployments (id, name) VALUES (?, ?)", (deployment_id, name))
    log.info("deployment_created", extra={"deployment_id": deployment_id, "deployment_name": name})
    return {"id": deployment_id, "name": name}


@app.get("/deployment/{deployment_id}")
def get_deployment(deployment_id: str):
    with db.connect() as conn:
        row = conn.execute("SELECT name FROM deployments WHERE id = ?", (deployment_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Deployment not found")
        rows = conn.execute(
            "SELECT playbook, inventory, tags, skip_tags FROM deployment_items WHERE deployment_id = ? ORDER BY position, rowid",
            (deployment_id,),
        ).fetchall()
    items = [{"playbook": r[0], "inventory": r[1], "tags": r[2], "skip_tags": r[3]} for r in rows]
    return {"id": deployment_id, "name": row[0], "items": items}


@app.put("/deployment/{deployment_id}")
def add_playbook_to_deployment(
    deployment_id: str,
    playbook: str = Query(..., max_length=255),
    inventory: str = Query(..., max_length=255),
    tags: str | None = Query(None, max_length=200, pattern=TAGS_PATTERN),
    skip_tags: str | None = Query(None, max_length=200, pattern=TAGS_PATTERN),
):
    resolve_or_reject(playbook, allowed_playbooks(), "Playbook")
    resolve_or_reject(inventory, allowed_inventories(), "Inventory")
    max_items = int(setting("limits", "max_items_per_deployment", 50))
    # IMMEDIATE: Zählen und Einfügen laufen ohne parallele Schreiber dazwischen
    with db.connect(immediate=True) as conn:
        if not conn.execute("SELECT 1 FROM deployments WHERE id = ?", (deployment_id,)).fetchone():
            raise HTTPException(status_code=404, detail="Deployment not found")
        count, last_position = conn.execute(
            "SELECT COUNT(*), COALESCE(MAX(position), 0) FROM deployment_items WHERE deployment_id = ?",
            (deployment_id,),
        ).fetchone()
        if count >= max_items:
            raise HTTPException(status_code=409, detail=f"Maximal {max_items} Playbooks pro Deployment erlaubt")
        conn.execute(
            "INSERT INTO deployment_items (deployment_id, position, playbook, inventory, tags, skip_tags) VALUES (?, ?, ?, ?, ?, ?)",
            (deployment_id, last_position + 1, playbook, inventory, tags, skip_tags),
        )
    log.info("deployment_item_added", extra={"deployment_id": deployment_id, "playbook": playbook, "inventory": inventory})
    return {"status": "added", "deployment_id": deployment_id}


@app.get("/deployments")
def list_all_deployments():
    with db.connect() as conn:
        rows = conn.execute("SELECT id, name FROM deployments").fetchall()
    return [{"id": row[0], "name": row[1]} for row in rows]


@app.delete("/deployment/{deployment_id}")
def delete_deployment(deployment_id: str):
    with db.connect() as conn:
        if not conn.execute("SELECT 1 FROM deployments WHERE id = ?", (deployment_id,)).fetchone():
            raise HTTPException(status_code=404, detail="Deployment not found")
        conn.execute("DELETE FROM deployment_items WHERE deployment_id = ?", (deployment_id,))
        conn.execute("DELETE FROM deployments WHERE id = ?", (deployment_id,))
    log.info("deployment_deleted", extra={"deployment_id": deployment_id})
    return {"status": "deleted", "deployment_id": deployment_id}


def current_branch() -> str | None:
    if not os.path.isdir(os.path.join(workspace_root(), ".git")):
        return None
    rc, output = run_git_command(["symbolic-ref", "--short", "HEAD"])
    return output.strip() if rc == 0 else "HEAD"


def worktree_changes() -> str:
    # Auch ungetrackte Dateien zählen: ein Playbook aus einem anderen Branch könnte sonst z.B.
    # filter_plugins/*.py hinterlassen, die beim nächsten Deployment mitgeladen werden
    return run_git_command(["status", "--porcelain", "--untracked-files=all"])[1]


def allowed_signers_file() -> str | None:
    path = setting("git", "allowed_signers_file", None)
    if not path:
        return None
    full = os.path.realpath(str(path))
    root = os.path.realpath(workspace_root())
    # Läge die Datei im Repo, könnte jeder Branch seine eigenen Schlüssel freigeben
    if os.path.commonpath([root, full]) == root:
        log.error("allowed_signers_in_workspace", extra={"path": full})
        raise HTTPException(status_code=500, detail="git.allowed_signers_file darf nicht in base_path liegen")
    return full


def verify_head_signature(has_repo: bool) -> None:
    if not has_repo:
        raise HTTPException(status_code=409, detail="Signierte Commits verlangt, aber kein Git-Repository im base_path")
    signers = allowed_signers_file()
    config_args = ["-c", f"gpg.ssh.allowedSignersFile={signers}"] if signers else []
    rc, output = run_git_command([*config_args, "verify-commit", "HEAD"])
    if rc != 0:
        log.warning("git_signature_invalid", extra={"returncode": rc})
        raise HTTPException(status_code=409, detail={"signature": output or "Commit ist nicht signiert"})


def plan_deployment(deployment_id: str) -> list[PlannedItem]:
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT playbook, inventory, tags, skip_tags FROM deployment_items WHERE deployment_id = ? ORDER BY position, rowid",
            (deployment_id,),
        ).fetchall()
    if not rows:
        raise HTTPException(status_code=404, detail="Deployment has no playbooks")

    branch = current_branch()
    if branch is not None and not branch_allowed(branch):
        log.warning("branch_not_allowed", extra={"branch": branch, "deployment_id": deployment_id})
        raise HTTPException(status_code=409, detail=f"Ausgecheckter Branch {branch!r} ist nicht freigegeben")
    if branch is not None:
        changes = worktree_changes()
        if changes.strip():
            log.warning("git_worktree_dirty", extra={"deployment_id": deployment_id})
            raise HTTPException(status_code=409, detail={"local_changes": changes})
    if setting("git", "require_signed_commits", False):
        verify_head_signature(branch is not None)

    playbooks = allowed_playbooks()
    inventories = allowed_inventories()
    plan = []
    for playbook, inventory, tags, skip_tags in rows:
        check_tags(tags, "Tags")
        check_tags(skip_tags, "Skip-Tags")
        plan.append(PlannedItem(
            playbook=playbook,
            inventory=inventory,
            playbook_path=resolve_or_reject(playbook, playbooks, "Playbook"),
            inventory_path=resolve_or_reject(inventory, inventories, "Inventory"),
            tags=tags,
            skip_tags=skip_tags,
            retries=playbooks[playbook],
        ))
    return plan


@app.post("/rundeployment/{deployment_id}")
async def run_deployment(deployment_id: str):
    # Erst sperren, dann prüfen: so kann kein Checkout die geprüften Dateien noch austauschen
    lock = acquire_workspace_lock()
    try:
        # Alles vor dem Start prüfen, damit Fehler als echter HTTP-Status ankommen
        plan = await asyncio.to_thread(plan_deployment, deployment_id)
        run_id = str(uuid.uuid4())
        db.start_run(run_id, deployment_id)
    except BaseException:
        lock.release()
        raise

    def close() -> None:
        try:
            db.finish_run(run_id, db.RUN_ABORTED)  # no-op, wenn der Run regulär beendet wurde
        finally:
            lock.release()

    log.info("deployment_run_started", extra={"run_id": run_id, "deployment_id": deployment_id, "items": len(plan)})
    return ClosingStreamingResponse(
        stream_deployment(plan, run_id, deployment_id),
        close,
        media_type="text/plain",
        headers={"X-Run-Id": run_id, "X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )


@app.get("/runs/{run_id}")
def get_run(run_id: str):
    with db.connect() as conn:
        run = conn.execute(
            "SELECT deployment_id, started_at, finished_at, result FROM deployment_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        rows = conn.execute(
            """SELECT deployment_id, playbook, inventory, start_time, end_time, duration, status, attempt
               FROM playbook_runs WHERE run_id = ? ORDER BY id""",
            (run_id,),
        ).fetchall()
    if not run and not rows:
        raise HTTPException(status_code=404, detail="Run not found")
    attempts = [
        {"playbook": r[1], "inventory": r[2], "start_time": r[3], "end_time": r[4],
         "duration": r[5], "status": r[6], "attempt": r[7]}
        for r in rows
    ]
    if run:
        deployment_id, started_at, finished_at, result = run
    else:
        # Runs aus älteren Versionen ohne Eintrag in deployment_runs: je Playbook zählt der letzte Versuch
        last_status = {a["playbook"]: a["status"] for a in attempts}
        result = db.RUN_SUCCESS if all(s == 0 for s in last_status.values()) else db.RUN_FAILED
        deployment_id, started_at, finished_at = rows[0][0], rows[0][3], rows[-1][4]
    return {"run_id": run_id, "deployment_id": deployment_id, "result": result,
            "started_at": started_at, "finished_at": finished_at, "attempts": attempts}


def run_git_command(args: list[str]) -> tuple[int, str]:
    timeout = float(setting("git", "timeout_seconds", 60))
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=workspace_root(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
            env=git_env(),
            check=False,
        )
    except subprocess.TimeoutExpired:
        log.warning("git_timeout", extra={"git_args": args, "timeout_seconds": timeout})
        return TIMEOUT_STATUS, f"git {args[0]}: Zeitüberschreitung nach {int(timeout)}s"
    return proc.returncode, redact(proc.stdout)


def require_git_repo() -> None:
    if not os.path.isdir(os.path.join(workspace_root(), ".git")):
        raise HTTPException(status_code=400, detail="Kein Git-Repository im base_path gefunden")


@app.get("/git/status")
def git_status():
    require_git_repo()
    _, output = run_git_command(["status"])
    return {"status": output}


@app.post("/git/checkout/{branch:path}")
def git_checkout(branch: str):
    require_git_repo()
    try:
        validate_branch(branch)
    except BranchNameError as e:
        log.warning("branch_rejected", extra={"branch": branch})
        raise HTTPException(status_code=400, detail=str(e)) from None
    if run_git_command(["check-ref-format", "--branch", branch])[0] != 0:
        log.warning("branch_rejected", extra={"branch": branch})
        raise HTTPException(status_code=400, detail=f"Ungültiger Branch-Name: {branch!r}")
    if not branch_allowed(branch):
        log.warning("branch_not_allowed", extra={"branch": branch})
        raise HTTPException(status_code=403, detail=f"Branch {branch!r} ist nicht freigegeben")

    remote = str(setting("git", "remote", "origin"))
    lock = acquire_workspace_lock()
    try:
        fetch_output = ""
        has_remote = remote in run_git_command(["remote"])[1].split()
        if has_remote:
            fetch_rc, fetch_output = run_git_command(["fetch", "--prune", remote])
            if fetch_rc != 0:
                log.warning("git_fetch_failed", extra={"returncode": fetch_rc})
                raise HTTPException(status_code=502, detail={"fetch": fetch_output})

        if setting("git", "clean_on_checkout", False):
            # Entfernt auch ignorierte Dateien (-x) – nichts Lokales darf in den neuen Branch wandern
            clean_rc, clean_output = run_git_command(["clean", "-ffdx"])
            if clean_rc != 0:
                log.warning("git_clean_failed", extra={"returncode": clean_rc})
                raise HTTPException(status_code=500, detail={"fetch": fetch_output, "clean": clean_output})

        # Lokale Änderungen würden mitgenommen oder den Checkout blockieren
        changes = worktree_changes()
        if changes.strip():
            log.warning("git_worktree_dirty", extra={"branch": branch})
            raise HTTPException(status_code=409, detail={"fetch": fetch_output, "local_changes": changes})

        # Gibt es den Branch auf dem Remote, wird der lokale Branch auf genau diesen Stand gesetzt –
        # ein einfacher Checkout bliebe auf einem älteren lokalen Stand stehen.
        # "--" sorgt dafür, dass git den Namen nur als Branch und nie als Pfad interpretiert.
        remote_ref = f"refs/remotes/{remote}/{branch}"
        if has_remote and run_git_command(["rev-parse", "--verify", "--quiet", remote_ref])[0] == 0:
            checkout_args = ["checkout", "-B", branch, "--track", f"{remote}/{branch}", "--"]
        else:
            checkout_args = ["checkout", branch, "--"]
        checkout_rc, checkout_output = run_git_command(checkout_args)
        if checkout_rc != 0:
            log.warning("git_checkout_failed", extra={"branch": branch, "returncode": checkout_rc})
            raise HTTPException(status_code=400, detail={"fetch": fetch_output, "checkout": checkout_output})
        commit = run_git_command(["rev-parse", "HEAD"])[1].strip()
    finally:
        lock.release()
    log.info("git_checkout", extra={"branch": branch, "commit": commit})
    return {
        "fetch": fetch_output,
        "checkout": checkout_output,
        "commit": commit,
    }
