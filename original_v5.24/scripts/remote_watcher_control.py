# -*- coding: utf-8 -*-
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

try:
    import psutil
except ImportError:
    raise SystemExit("psutil is required: python -m pip install --user psutil")

ROOT = Path(r"C:\Users\130\OneDrive\ArticleDownloader")
SCRIPTS = ROOT / "scripts"
WATCHER = SCRIPTS / "watch_queue.py"
REMOTE = ROOT / "remote_control"
LOGS = ROOT / "logs"
LOGFILE = LOGS / "remote_control.log"
STATUS = REMOTE / "status.txt"

CREATE_NO_WINDOW = 0x08000000

COMMANDS = {
    "START.flag": "START",
    "STOP.flag": "STOP",
    "RESTART.flag": "RESTART",
    "STATUS.flag": "STATUS",
}

def log(msg: str):
    LOGS.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        with LOGFILE.open("a", encoding="utf-8") as f:
            f.write(f"[{stamp}] {msg}\n")
    except Exception:
        pass

def cmdline(proc) -> str:
    try:
        return " ".join(proc.cmdline() or [])
    except Exception:
        return ""

def watcher_processes():
    out = []
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            c = " ".join(p.info.get("cmdline") or [])
            if "ArticleDownloader" in c and "watch_queue.py" in c:
                out.append(p)
        except Exception:
            pass
    return out

def downloader_processes():
    out = []
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            c = " ".join(p.info.get("cmdline") or [])
            if (
                "ArticleDownloader" in c
                and "article_downloader.py" in c
                and "remote_watcher_control.py" not in c
            ):
                out.append(p)
        except Exception:
            pass
    return out

def pythonw_path() -> str:
    exe = Path(sys.executable)
    candidate = exe.with_name("pythonw.exe")
    if candidate.exists():
        return str(candidate)
    return "pythonw.exe"

def terminate_many(procs, label: str):
    procs = list(procs)
    if not procs:
        return
    for p in procs:
        try:
            p.terminate()
        except Exception:
            pass
    deadline = time.time() + 5
    while time.time() < deadline:
        alive = []
        for p in procs:
            try:
                if p.is_running():
                    alive.append(p)
            except Exception:
                pass
        if not alive:
            break
        time.sleep(0.2)
    for p in procs:
        try:
            if p.is_running():
                p.kill()
        except Exception:
            pass
    log(f"Stopped {label}: {len(procs)}")

def start_watcher():
    if watcher_processes():
        log("START ignored: watcher already running")
        return
    if not WATCHER.exists():
        log(f"ERROR: watcher not found: {WATCHER}")
        return
    subprocess.Popen(
        [pythonw_path(), str(WATCHER)],
        cwd=str(ROOT),
        creationflags=CREATE_NO_WINDOW,
        close_fds=True,
    )
    time.sleep(1)
    log("Watcher START requested")

def stop_all():
    # Stop active downloader first so STOP is immediate.
    terminate_many(downloader_processes(), "article_downloader.py")
    terminate_many(watcher_processes(), "watch_queue.py")
    log("STOP completed")

def restart_all():
    stop_all()
    time.sleep(1)
    start_watcher()
    log("RESTART completed")

def count(pattern: str, folder: Path) -> int:
    try:
        return len(list(folder.glob(pattern)))
    except Exception:
        return -1

def write_status(last_command: str = ""):
    REMOTE.mkdir(parents=True, exist_ok=True)
    watcher = watcher_processes()
    downloader = downloader_processes()
    inbox = ROOT / "inbox"
    done = ROOT / "done"
    failed = ROOT / "failed"

    lines = [
        f"UPDATED={time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"LAST_COMMAND={last_command or '-'}",
        f"WATCHER={'RUNNING' if watcher else 'STOPPED'}",
        f"WATCHER_PID={watcher[0].pid if watcher else '-'}",
        f"DOWNLOADER={'RUNNING' if downloader else 'IDLE'}",
        f"DOWNLOADER_PID={downloader[0].pid if downloader else '-'}",
        f"INBOX_CSV={count('*.csv', inbox)}",
        f"READY={count('*.ready', inbox)}",
        f"DONE_CSV={count('*.csv', done)}",
        f"FAILED_CSV={count('*.csv', failed)}",
    ]
    tmp = REMOTE / "status.tmp"
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        tmp.replace(STATUS)
    except Exception:
        STATUS.write_text("\n".join(lines) + "\n", encoding="utf-8")
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass

def process_commands():
    REMOTE.mkdir(parents=True, exist_ok=True)
    # Deterministic priority: STOP > RESTART > START > STATUS.
    order = ["STOP.flag", "RESTART.flag", "START.flag", "STATUS.flag"]
    for name in order:
        path = REMOTE / name
        if not path.exists():
            continue
        command = COMMANDS[name]
        try:
            path.unlink()
        except Exception:
            continue

        log(f"Remote command received: {command}")
        if command == "START":
            start_watcher()
        elif command == "STOP":
            stop_all()
        elif command == "RESTART":
            restart_all()
        write_status(command)
        return True
    return False

def main():
    REMOTE.mkdir(parents=True, exist_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True)
    log("Remote controller started")
    write_status("BOOT")

    last_status = 0.0
    while True:
        try:
            changed = process_commands()
            now = time.time()
            if changed or now - last_status >= 30:
                write_status()
                last_status = now
        except Exception as e:
            log(f"LOOP ERROR: {e!r}")
        time.sleep(3)

if __name__ == "__main__":
    main()
