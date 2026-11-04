# -*- coding: utf-8 -*-
"""
ArticleDownloader FINAL watcher.

Queue format:
  inbox\\<name>.csv
  inbox\\<name>.ready

Blocked case (e.g. CAPTCHA):
  inbox\\<name>.csv remains in place
  inbox\\<name>.blocked is created

No filename suffixes such as _blocked_blocked are ever generated.

A Windows file lock prevents two watcher processes from working simultaneously.
"""

from __future__ import annotations

import argparse
import csv
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent

INBOX = ROOT / "inbox"
DONE = ROOT / "done"
FAILED = ROOT / "failed"
LOGS = ROOT / "logs"
LOCK_PATH = ROOT / "watcher.lock"
RESCUE_SAFETY_PAUSE = ROOT / "reports" / "rescue_safety_paused.flag"


for d in (INBOX, DONE, FAILED, LOGS):
    d.mkdir(parents=True, exist_ok=True)


def log(text: str):
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{stamp}] {text}"
    print(line, flush=True)

    try:
        with (LOGS / "watch_queue.log").open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def acquire_single_instance_lock():
    f = LOCK_PATH.open("a+b")

    if os.name == "nt":
        import msvcrt

        f.seek(0)
        if f.read(1) == b"":
            f.seek(0)
            f.write(b"1")
            f.flush()

        f.seek(0)

        try:
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            print("Watcher already running. Exiting.", flush=True)
            sys.exit(0)

    return f


def read_queries(csv_path: Path):
    """
    Flexible reader:
    - preferred header: query
    - also accepts doi/url/input/article
    - if there is no useful header, uses the first non-empty cell in each row
    """
    queries = []

    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))

    if not rows:
        return queries

    header = [x.strip().lower() for x in rows[0]]
    known = {"query", "doi", "url", "input", "article"}

    idx = None
    start = 0

    for i, name in enumerate(header):
        if name in known:
            idx = i
            start = 1
            break

    for row in rows[start:]:
        if not row:
            continue

        if idx is not None and idx < len(row):
            value = row[idx].strip()
        else:
            value = next((x.strip() for x in row if x.strip()), "")

        if value:
            queries.append(value)

    # A one-line CSV created without a header.
    if not queries and start == 0 and rows:
        value = next((x.strip() for x in rows[0] if x.strip()), "")
        if value:
            queries.append(value)

    return queries


def unique_target(folder: Path, name: str):
    target = folder / name
    if not target.exists():
        return target

    stem = Path(name).stem
    suffix = Path(name).suffix
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return folder / f"{stem}_{stamp}{suffix}"


def kill_job_tree(proc: subprocess.Popen):
    """
    Kill the downloader together with its Playwright node driver and PDF
    worker. Otherwise the orphaned driver keeps the output pipe open and the
    watcher blocks. The dedicated browser (browser.exe, which the downloader
    may have started) is deliberately left running.
    """
    try:
        import psutil

        for child in psutil.Process(proc.pid).children(recursive=True):
            try:
                name = child.name().lower().removesuffix(".exe")
                if name == "node" or name.startswith("python"):
                    child.kill()
            except psutil.Error:
                pass
    except Exception:
        pass

    try:
        proc.kill()
    except Exception:
        pass


def run_downloader(query: str, safe_mode: bool = False):
    cmd = [
        sys.executable,
        str(SCRIPT_DIR / "article_downloader.py"),
        query,
    ]

    log(f"RUN: {query}")

    # v5.21: the downloader receives the deadline and finishes its own waits
    # (CAPTCHA, Wiley viewer, eLIBRARY login) before it, so a timeout kill is
    # only a last resort. 240s was shorter than those waits.
    limit = int(os.environ.get("ARTICLE_JOB_TIMEOUT", "600"))
    if safe_mode:
        limit = min(limit, 120)
        log("RESCUE_SAFE_MODE: one attempt, bounded browser operations, timeout <=120s")

    env = dict(
        os.environ,
        ARTICLE_JOB_DEADLINE=f"{time.time() + limit:.0f}",
        # Without this the child prints in the ANSI code page and every
        # Russian/Chinese message arrives here as '����'.
        PYTHONIOENCODING="utf-8",
    )
    if safe_mode:
        env["ARTICLE_SAFE_MODE"] = "1"

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    try:
        stdout, stderr = proc.communicate(timeout=limit)
    except subprocess.TimeoutExpired:
        log(f"TIMEOUT ({limit}s): {query}")
        kill_job_tree(proc)
        try:
            out, err = proc.communicate(timeout=20)
        except subprocess.TimeoutExpired:
            out, err = "", "watcher: output pipe still open after kill\n"
        return 124, (out or "") + f"\nRESULT|FAILED|JOB_TIMEOUT_{limit}s\n", err or ""

    proc = subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)

    if proc.stdout:
        print(proc.stdout, end="", flush=True)
        try:
            with (LOGS / "watch_queue.log").open("a", encoding="utf-8") as f:
                f.write(proc.stdout)
        except Exception:
            pass

    if proc.stderr:
        print(proc.stderr, end="", flush=True)
        try:
            with (LOGS / "watch_queue.log").open("a", encoding="utf-8") as f:
                f.write(proc.stderr)
        except Exception:
            pass

    return proc.returncode, proc.stdout, proc.stderr


def process_ready(ready_path: Path):
    stem = ready_path.stem
    csv_path = INBOX / f"{stem}.csv"

    if not csv_path.exists():
        log(f"Skip {ready_path.name}: matching CSV not found")
        return

    queries = read_queries(csv_path)

    if not queries:
        ready_path.unlink(missing_ok=True)
        target = unique_target(FAILED, csv_path.name)
        shutil.move(str(csv_path), str(target))
        (target.with_suffix(target.suffix + ".result.txt")).write_text(
            "EMPTY_QUEUE\n",
            encoding="utf-8",
        )
        log(f"FAILED empty queue: {csv_path.name}")
        return

    # Claim the job before processing so a second scan cannot run it.
    ready_path.unlink(missing_ok=True)

    results = []
    final_code = 0

    for query in queries:
        code, stdout, stderr = run_downloader(
            query, safe_mode=stem.upper().startswith("RESCUE_")
        )
        results.append(
            f"QUERY: {query}\nEXIT: {code}\n{stdout}\n{stderr}\n"
        )

        if code != 0:
            final_code = code
            break

        # Gentle rate limit between articles.
        time.sleep(10)

    result_text = "\n" + ("=" * 70) + "\n"
    result_text = result_text.join(results)

    if final_code == 0:
        target = unique_target(DONE, csv_path.name)
        shutil.move(str(csv_path), str(target))
        (target.with_suffix(target.suffix + ".result.txt")).write_text(
            result_text,
            encoding="utf-8",
        )
        log(f"DONE: {target.name}")
        return 0

    if final_code == 10:
        # CAPTCHA / manual action:
        # keep original CSV in inbox and create a sidecar marker only.
        blocked = INBOX / f"{stem}.blocked"
        blocked.write_text(
            result_text,
            encoding="utf-8",
        )
        log(
            f"BLOCKED: {csv_path.name}. "
            "Pass verification manually, then run retry_blocked.bat."
        )
        return 10

    target = unique_target(FAILED, csv_path.name)
    shutil.move(str(csv_path), str(target))
    (target.with_suffix(target.suffix + ".result.txt")).write_text(
        result_text,
        encoding="utf-8",
    )

    if final_code == 11:
        log(f"NO_ACCESS: {target.name}")
    elif final_code == 12:
        log(f"UNSUPPORTED: {target.name}")
    else:
        log(f"FAILED: {target.name}")
    return final_code


def scan_once():
    ready_files = sorted(
        INBOX.glob("*.ready"),
        key=lambda p: p.stat().st_mtime,
    )

    if ready_files:
        log(f"Ready jobs: {len(ready_files)}")

    consecutive_rescue_errors = 0
    for ready in ready_files:
        if RESCUE_SAFETY_PAUSE.exists() and ready.stem.upper().startswith("RESCUE_"):
            log(f"RESCUE_PAUSED: {ready.name}; safety flag exists")
            continue
        try:
            code = process_ready(ready)
            if ready.stem.upper().startswith("RESCUE_"):
                if code not in (0, 10, None):
                    consecutive_rescue_errors += 1
                    if consecutive_rescue_errors >= 2:
                        RESCUE_SAFETY_PAUSE.parent.mkdir(parents=True, exist_ok=True)
                        RESCUE_SAFETY_PAUSE.write_text(
                            "Rescue queue auto-paused after two consecutive failed RESCUE jobs. "
                            "Inspect and manually remove this flag to resume.\n",
                            encoding="utf-8",
                        )
                        log("SAFETY_PAUSE: two consecutive failed RESCUE jobs. "
                            "Further RESCUE jobs will not be processed.")
                        break
                else:
                    consecutive_rescue_errors = 0
        except Exception as exc:
            log(f"Watcher error for {ready.name}: {type(exc).__name__}: {exc}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--once",
        action="store_true",
        help="Process current ready queue once and exit",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=60,
        help="Polling interval in seconds",
    )
    args = parser.parse_args()

    lock_handle = acquire_single_instance_lock()

    log("Watcher started")

    if args.once:
        scan_once()
        return

    while True:
        scan_once()
        time.sleep(max(10, args.interval))


if __name__ == "__main__":
    main()
