# -*- coding: utf-8 -*-
"""
ArticleDownloader tray controller for Windows.

Features:
- Runs watch_queue.py invisibly via pythonw.exe
- Lives in Windows notification area
- Accepts plain .txt DOI lists dropped into inbox
- Converts each DOI line into the existing .csv + .ready queue format
- Archives processed TXT lists under done\queue_lists
"""

from __future__ import annotations

import csv
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

import psutil
from PIL import Image, ImageDraw
import pystray
from pystray import MenuItem as Item


ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
WATCHER = SCRIPTS / "watch_queue.py"

INBOX = ROOT / "inbox"
PDF_DIR = ROOT / "pdf"
DONE = ROOT / "done"
FAILED = ROOT / "failed"
LOGS = ROOT / "logs"

QUEUE_LIST_ARCHIVE = DONE / "queue_lists"
QUEUE_LIST_FAILED = FAILED / "queue_lists"
TRAY_LOG = LOGS / "tray_watcher.log"

WATCHER_MATCH = "ArticleDownloader"
WATCHER_NAME = "watch_queue.py"
CREATE_NO_WINDOW = 0x08000000

DOI_RE = re.compile(r"10\.\d{4,9}/[^\s]+", re.I)
STOP_EVENT = threading.Event()


def log(msg: str):
    try:
        LOGS.mkdir(parents=True, exist_ok=True)
        with TRAY_LOG.open("a", encoding="utf-8") as f:
            f.write(time.strftime("[%Y-%m-%d %H:%M:%S] ") + msg + "\n")
    except Exception:
        pass


def pythonw_path() -> str:
    exe = Path(sys.executable)
    candidate = exe.with_name("pythonw.exe")
    if candidate.exists():
        return str(candidate)
    return "pythonw.exe"


def watcher_processes():
    result = []
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            cmd = " ".join(proc.info.get("cmdline") or [])
            if (
                WATCHER_NAME.lower() in cmd.lower()
                and WATCHER_MATCH.lower() in cmd.lower()
            ):
                result.append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return result


def watcher_running() -> bool:
    return bool(watcher_processes())


def start_watcher():
    if watcher_running():
        log("Watcher already running.")
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
    log(
        "Watcher start requested. "
        + ("RUNNING" if watcher_running() else "NOT DETECTED")
    )


def stop_watcher():
    for proc in watcher_processes():
        try:
            proc.terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    deadline = time.time() + 5
    while time.time() < deadline and watcher_running():
        time.sleep(0.2)

    for proc in watcher_processes():
        try:
            proc.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    log("Watcher stopped.")


def restart_watcher():
    stop_watcher()
    time.sleep(1)
    start_watcher()


def clean_doi(line: str) -> str:
    s = line.strip()
    if not s or s.startswith("#"):
        return ""

    # "title: ..." lines are title-search jobs for article_downloader.py.
    if s.lower().startswith("title:"):
        return s

    s = re.sub(
        r"^https?://(?:dx\.)?doi\.org/",
        "",
        s,
        flags=re.I,
    )

    m = DOI_RE.search(s)
    if not m:
        return ""

    return m.group(0).rstrip(".,;:)]}")


def unique_destination(folder: Path, name: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / name

    if not dest.exists():
        return dest

    stem = Path(name).stem
    suffix = Path(name).suffix

    for i in range(1, 10000):
        candidate = folder / f"{stem}_{i}{suffix}"
        if not candidate.exists():
            return candidate

    raise RuntimeError("Could not create unique destination name")


def convert_txt_to_queue(txt_path: Path):
    """
    Convert one plain TXT list into the existing queue format:
    one DOI -> one CSV + one READY file.
    """
    try:
        # Wait briefly until OneDrive/editor has finished writing the file.
        size1 = txt_path.stat().st_size
        time.sleep(1)
        size2 = txt_path.stat().st_size

        if size1 != size2:
            log(f"TXT still changing, will retry later: {txt_path.name}")
            return

        text = txt_path.read_text(
            encoding="utf-8-sig",
            errors="replace",
        )

        dois = []
        seen = set()

        for line in text.splitlines():
            doi = clean_doi(line)
            if doi and doi.lower() not in seen:
                seen.add(doi.lower())
                dois.append(doi)

        if not dois:
            dest = unique_destination(
                QUEUE_LIST_FAILED,
                txt_path.name,
            )
            shutil.move(str(txt_path), str(dest))
            log(
                f"TXT has no DOI and was moved to failed: "
                f"{txt_path.name}"
            )
            return

        stamp = time.strftime("%Y%m%d-%H%M%S")
        base = re.sub(r"[^A-Za-z0-9_-]+", "_", txt_path.stem)[:40] or "list"

        for idx, doi in enumerate(dois, start=1):
            stem = f"{stamp}_{base}_{idx:03d}"

            csv_path = INBOX / f"{stem}.csv"
            ready_path = INBOX / f"{stem}.ready"

            # Duplicate-safe naming.
            n = 1
            while csv_path.exists() or ready_path.exists():
                stem2 = f"{stem}_{n}"
                csv_path = INBOX / f"{stem2}.csv"
                ready_path = INBOX / f"{stem2}.ready"
                n += 1

            with csv_path.open(
                "w",
                newline="",
                encoding="utf-8-sig",
            ) as f:
                w = csv.writer(f)
                w.writerow(["query"])
                w.writerow([doi])

            ready_path.write_text("", encoding="utf-8")

            log(f"Queued from TXT: {doi}")

        dest = unique_destination(
            QUEUE_LIST_ARCHIVE,
            txt_path.name,
        )
        shutil.move(str(txt_path), str(dest))

        log(
            f"TXT queue accepted: {txt_path.name}; "
            f"{len(dois)} DOI(s) queued."
        )

    except FileNotFoundError:
        pass
    except Exception:
        log(
            f"TXT QUEUE ERROR for {txt_path}:\n"
            + traceback.format_exc()
        )


def txt_inbox_loop():
    INBOX.mkdir(parents=True, exist_ok=True)
    QUEUE_LIST_ARCHIVE.mkdir(parents=True, exist_ok=True)
    QUEUE_LIST_FAILED.mkdir(parents=True, exist_ok=True)

    log("TXT inbox monitor started.")

    while not STOP_EVENT.is_set():
        try:
            for txt_path in sorted(INBOX.glob("*.txt")):
                if STOP_EVENT.is_set():
                    break
                convert_txt_to_queue(txt_path)
        except Exception:
            log("TXT LOOP ERROR:\n" + traceback.format_exc())

        STOP_EVENT.wait(5)


def open_folder(path: Path):
    path.mkdir(parents=True, exist_ok=True)
    os.startfile(str(path))


def status_text(_item=None):
    if watcher_running():
        return "Watcher: RUNNING"
    return "Watcher: STOPPED"


def icon_image(running: bool):
    img = Image.new(
        "RGBA",
        (64, 64),
        (0, 0, 0, 0),
    )
    d = ImageDraw.Draw(img)

    d.rounded_rectangle(
        (10, 7, 48, 56),
        radius=6,
        fill=(245, 245, 245, 255),
        outline=(65, 65, 65, 255),
        width=3,
    )

    d.line((18, 18, 40, 18), fill=(80, 80, 80, 255), width=3)
    d.line((18, 26, 36, 26), fill=(80, 80, 80, 255), width=3)
    d.line((30, 31, 30, 45), fill=(50, 100, 170, 255), width=5)

    d.polygon(
        [(22, 40), (38, 40), (30, 49)],
        fill=(50, 100, 170, 255),
    )

    dot = (40, 40, 56, 56)

    if running:
        d.ellipse(
            dot,
            fill=(34, 160, 70, 255),
            outline=(255, 255, 255, 255),
            width=2,
        )
    else:
        d.ellipse(
            dot,
            fill=(190, 60, 60, 255),
            outline=(255, 255, 255, 255),
            width=2,
        )

    return img


def refresh_icon(icon):
    running = watcher_running()

    icon.icon = icon_image(running)
    icon.title = (
        "ArticleDownloader — RUNNING"
        if running
        else "ArticleDownloader — STOPPED"
    )

    try:
        icon.update_menu()
    except Exception:
        pass


def action_start(icon, _item):
    start_watcher()
    refresh_icon(icon)


def action_stop(icon, _item):
    stop_watcher()
    refresh_icon(icon)


def action_restart(icon, _item):
    restart_watcher()
    refresh_icon(icon)


def action_exit(icon, _item):
    STOP_EVENT.set()
    stop_watcher()
    log("Tray exit requested.")
    icon.stop()


def setup(icon):
    try:
        log("Tray setup started.")

        start_watcher()

        t = threading.Thread(
            target=txt_inbox_loop,
            name="ArticleDownloaderTxtInbox",
            daemon=True,
        )
        t.start()

        refresh_icon(icon)
        icon.visible = True

        log("Tray icon is visible.")

    except Exception:
        log("SETUP ERROR:\n" + traceback.format_exc())
        raise


menu = pystray.Menu(
    Item(
        status_text,
        lambda icon, item: refresh_icon(icon),
        enabled=False,
    ),
    pystray.Menu.SEPARATOR,
    Item("Start watcher", action_start),
    Item("Stop watcher", action_stop),
    Item("Restart watcher", action_restart),
    pystray.Menu.SEPARATOR,
    Item("Open inbox", lambda icon, item: open_folder(INBOX)),
    Item("Open PDFs", lambda icon, item: open_folder(PDF_DIR)),
    Item("Open logs", lambda icon, item: open_folder(LOGS)),
    pystray.Menu.SEPARATOR,
    Item("Exit tray", action_exit),
)

icon = pystray.Icon(
    "ArticleDownloader",
    icon_image(False),
    "ArticleDownloader",
    menu,
)

try:
    log("Tray process started with " + sys.executable)
    icon.run(setup)
except Exception:
    log("FATAL ERROR:\n" + traceback.format_exc())
