# -*- coding: utf-8 -*-
"""
ArticleDownloader doctor: show what is actually going on and repair the
dedicated browsers without touching cookies/logins.

  python scripts\\doctor.py                      status report
  python scripts\\doctor.py reset-browser        close leftover tabs (9223, 9237)
  python scripts\\doctor.py reset-browser --hard kill the dedicated Yandex
                                                 instances and drop their
                                                 'reopen tabs' session files
  python scripts\\doctor.py missing LIST.txt     DOIs from LIST without a PDF
                                                 -> reports\\MISSING_<name>.txt
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
PDF_DIR = ROOT / "pdf"
INBOX = ROOT / "inbox"
REPORTS = ROOT / "reports"
SAFETY_FLAG = REPORTS / "rescue_safety_paused.flag"

LOCAL = Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
BROWSERS = {
    # port: (label, profile dir)
    9223: ("main downloader Yandex", LOCAL / "ArticleDownloader" / "YandexProfile"),
    9237: ("stage4 Yandex", LOCAL / "ArticleDownloader_Stage4_Yandex_Profile"),
}

FILE_ATTRIBUTE_OFFLINE = 0x1000
FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x400000
FILE_ATTRIBUTE_RECALL_ON_OPEN = 0x40000


def say(text: str = ""):
    try:
        print(text, flush=True)
    except UnicodeEncodeError:
        print(text.encode("ascii", "backslashreplace").decode("ascii"), flush=True)


# ---------------------------------------------------------------- browsers

def cdp_pages(port: int):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=3) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
        return [t for t in data if t.get("type") == "page"]
    except Exception:
        return None


def close_tabs(port: int) -> int:
    pages = cdp_pages(port)
    if not pages:
        return 0
    base = f"http://127.0.0.1:{port}"
    fresh = None
    for method in ("PUT", "GET"):
        try:
            req = urllib.request.Request(base + "/json/new?about:blank", method=method)
            with urllib.request.urlopen(req, timeout=5) as r:
                fresh = json.loads(r.read().decode("utf-8")).get("id")
            break
        except Exception:
            continue
    closed = 0
    for t in pages:
        if t.get("id") and t["id"] != fresh:
            try:
                with urllib.request.urlopen(base + "/json/close/" + t["id"], timeout=5):
                    closed += 1
            except Exception:
                pass
    return closed


def clear_session_restore(profile: Path) -> int:
    default = profile / "Default"
    removed = 0
    for name in ("Current Session", "Current Tabs", "Last Session", "Last Tabs"):
        try:
            (default / name).unlink()
            removed += 1
        except FileNotFoundError:
            pass
    sessions = default / "Sessions"
    if sessions.is_dir():
        for item in sessions.iterdir():
            try:
                item.unlink()
                removed += 1
            except Exception:
                pass
    return removed


def kill_dedicated_browsers() -> str:
    if os.name != "nt":
        return "not Windows: nothing killed"
    patterns = []
    for port, (_, profile) in BROWSERS.items():
        patterns.append(f"*--remote-debugging-port={port}*")
        patterns.append(f"*{profile}*")
    cond = " -or ".join(f"$_.CommandLine -like '{p}'" for p in patterns)
    # No double quotes inside the command: they get mangled on the way to powershell.
    ps = (
        "Get-CimInstance Win32_Process | "
        f"Where-Object {{ $_.Name -eq 'browser.exe' -and ({cond}) }} | ForEach-Object {{ "
        "try { Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop; "
        "Write-Output ('killed ' + $_.ProcessId) } catch {} }"
    )
    r = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
    )
    return r.stdout.strip() or "no dedicated browser process found"


# ---------------------------------------------------------------- processes

def running(script: str) -> list:
    try:
        import psutil
    except ImportError:
        return []
    out = []
    for p in psutil.process_iter(["pid", "cmdline"]):
        try:
            cmd = " ".join(p.info.get("cmdline") or [])
            if script in cmd and "doctor.py" not in cmd:
                out.append(p.info["pid"])
        except Exception:
            pass
    return out


# ---------------------------------------------------------------- PDFs

def is_cloud_only(path: Path) -> bool:
    attrs = getattr(path.stat(), "st_file_attributes", 0)
    return bool(attrs & (FILE_ATTRIBUTE_OFFLINE | FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS
                         | FILE_ATTRIBUTE_RECALL_ON_OPEN))


def pdf_ok(path: Path) -> bool:
    try:
        size = path.stat().st_size
        if size <= 1000:
            return False
        with path.open("rb") as f:
            if f.read(5) != b"%PDF-":
                return False
            f.seek(max(0, size - 65536))
            tail = f.read()
        return re.search(rb"startxref\s+\d+\s+%%EOF[ \t\r\n\x00]*\Z", tail) is not None
    except Exception:
        return False


def pdf_inventory():
    files = sorted(PDF_DIR.rglob("*.pdf")) if PDF_DIR.exists() else []
    good, bad, cloud = [], [], []
    for p in files:
        if is_cloud_only(p):
            cloud.append(p)          # do not download it from OneDrive just to check
        elif pdf_ok(p):
            good.append(p)
        else:
            bad.append(p)
    return files, good, bad, cloud


def doi_key(doi: str) -> str:
    # Same rule as article_downloader.sanitize_filename().
    value = re.sub(r'[<>:"/\\|?*]+', "_", doi)
    value = re.sub(r"\s+", "_", value).strip("._ ")
    return value.lower()


def read_dois(path: Path) -> list:
    raw = path.read_bytes()
    text = raw.decode("utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else raw.decode("utf-8-sig", "replace")
    seen, out = set(), []
    for m in re.finditer(r"10\.\d{4,9}/[^\s\"',;<>]+", text):
        doi = m.group(0).rstrip(".)]}").lower()
        if doi not in seen:
            seen.add(doi)
            out.append(doi)
    return out


# ---------------------------------------------------------------- commands

def cmd_status():
    say("=== BROWSERS (CDP) ===")
    for port, (label, profile) in BROWSERS.items():
        pages = cdp_pages(port)
        if pages is None:
            say(f"  {port} {label}: not running")
            continue
        flag = "  <-- too many, attach will be slow/hang" if len(pages) > 4 else ""
        say(f"  {port} {label}: {len(pages)} tab(s){flag}")
        for t in pages[:12]:
            say(f"      {t.get('url', '')[:110]}")

    say("\n=== PROCESSES ===")
    try:
        import psutil  # noqa: F401
        say(f"  watch_queue.py:        {running('watch_queue.py') or 'not running'}")
        say(f"  article_downloader.py: {running('article_downloader.py') or 'not running'}")
    except ImportError:
        say("  psutil not installed: process check skipped")

    say("\n=== QUEUE ===")
    csvs = {p.stem for p in INBOX.glob("*.csv")}
    ready = {p.stem for p in INBOX.glob("*.ready")}
    blocked = {p.stem for p in INBOX.glob("*.blocked")}
    orphans = sorted(csvs - ready - blocked)
    rescue = sorted(s for s in ready if s.upper().startswith("RESCUE_"))
    say(f"  inbox csv={len(csvs)} ready={len(ready)} blocked={len(blocked)}")
    if rescue:
        say(f"  RESCUE_ jobs waiting: {len(rescue)}")
    if orphans:
        say(f"  csv without .ready/.blocked (claimed by a killed watcher, never finished): {len(orphans)}")
        for s in orphans[:10]:
            say(f"      {s}.csv")
    if SAFETY_FLAG.exists():
        text = SAFETY_FLAG.read_text(encoding="utf-8-sig", errors="replace").strip()
        say(f"  SAFETY FLAG PRESENT -> every RESCUE_ job is skipped: {text}")

    say("\n=== PDF ===")
    files, good, bad, cloud = pdf_inventory()
    say(f"  {PDF_DIR}: {len(files)} pdf, valid={len(good)}, broken={len(bad)}, OneDrive cloud-only={len(cloud)}")
    for p in bad[:10]:
        say(f"      broken: {p.relative_to(PDF_DIR)}")


def cmd_reset(hard: bool, force: bool):
    busy = running("article_downloader.py")
    if busy and not force:
        say(f"article_downloader.py is running {busy}; stop the watcher first or add --force")
        return 2

    if not hard:
        for port, (label, _) in BROWSERS.items():
            pages = cdp_pages(port)
            if pages is None:
                say(f"{port} {label}: not running")
            else:
                say(f"{port} {label}: closed {close_tabs(port)} of {len(pages)} tab(s)")
        say("If a connect still hangs: doctor.py reset-browser --hard")
        return 0

    say(kill_dedicated_browsers())
    deadline = time.time() + 15
    while time.time() < deadline and any(cdp_pages(p) is not None for p in BROWSERS):
        time.sleep(0.5)
    time.sleep(1)
    for port, (label, profile) in BROWSERS.items():
        if cdp_pages(port) is not None:
            say(f"{port} {label}: STILL RUNNING - close it manually")
        elif profile.exists():
            say(f"{port} {label}: stopped; removed {clear_session_restore(profile)} session-restore file(s); cookies kept")
    say("The downloader restarts 9223 by itself on the next job.")
    return 0


def cmd_missing(list_path: Path):
    dois = read_dois(list_path)
    _, good, _, cloud = pdf_inventory()
    stems = [p.stem.lower() for p in good + cloud]
    missing = []
    for doi in dois:
        key = re.escape(doi_key(doi))
        pat = re.compile(r"(^|_)" + key + r"(_\d+)?$")
        if not any(pat.search(s) for s in stems):
            missing.append(doi)
    out = REPORTS / f"MISSING_{list_path.stem}.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(missing) + ("\n" if missing else ""), encoding="utf-8")
    say(f"{list_path.name}: {len(dois)} DOI, have PDF: {len(dois) - len(missing)}, missing: {len(missing)}")
    say(f"written: {out}")
    say("To re-queue: copy that .txt into inbox\\ (the tray turns each line into a job).")
    return 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd")
    r = sub.add_parser("reset-browser")
    r.add_argument("--hard", action="store_true")
    r.add_argument("--force", action="store_true")
    m = sub.add_parser("missing")
    m.add_argument("list", type=Path)
    args = ap.parse_args()

    if args.cmd == "reset-browser":
        return cmd_reset(args.hard, args.force)
    if args.cmd == "missing":
        return cmd_missing(args.list)
    cmd_status()
    return 0


if __name__ == "__main__":
    sys.exit(main())
