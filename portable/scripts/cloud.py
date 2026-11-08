# -*- coding: utf-8 -*-
# © 2026 Bucaneiro. Бесплатная программа; условия — в файле ЛИЦЕНЗИЯ.txt.
"""
«☁ Облако» (v8.8): the home computer sends lists, the work computer (with the
university's access to the databases) downloads them. The bridge is a folder
of the user's own cloud (Yandex Disk, Google Drive, OneDrive, Cloud Mail.ru,
Dropbox, iCloud) or any folder both computers see:

    <cloud>/Загрузчик статей/
        В работу/         lists the home computer sends (one file per list)
        PDF/              the downloaded articles (both computers' PDF folder)
        состояние.json    what the work computer is doing, rewritten every minute

The program itself sends nothing over the internet: the cloud's own app
moves the files. Only plain file operations here (no shell commands).
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path

FOLDER = "Загрузчик статей"
TODO = "В работу"
PDF = "PDF"
STATUS = "состояние.json"
# The work computer rewrites its status at least this often; older than
# STALE_SEC on the home computer = "не отвечает" (the clouds sync in 0.5-2 min).
HEARTBEAT_SEC = 60
STALE_SEC = 6 * 60

IS_WIN = os.name == "nt"
IS_MAC = sys.platform == "darwin"


def _home() -> Path:
    return Path(os.environ.get("USERPROFILE") or Path.home()) if IS_WIN else Path.home()


def detect() -> list[dict]:
    """The cloud folders on this computer: [{"name", "path"}], the usual places only."""
    home = _home()
    found: list[tuple[str, Path]] = []

    def add(name: str, path):
        if path and Path(path).is_dir():
            found.append((name, Path(path)))

    if IS_MAC:
        storage = home / "Library" / "CloudStorage"
        if storage.is_dir():
            for d in sorted(storage.iterdir()):
                low = d.name.lower()
                if low.startswith("googledrive"):
                    for sub in ("My Drive", "Мой диск"):
                        add("Google Диск", d / sub)
                elif low.startswith("onedrive"):
                    add("OneDrive", d)
                elif low.startswith("dropbox"):
                    add("Dropbox", d)
                elif "yandex" in low:
                    add("Яндекс Диск", d)
        add("iCloud Drive", home / "Library" / "Mobile Documents" / "com~apple~CloudDocs")
        for sub in ("Yandex.Disk.localized", "Yandex.Disk"):
            add("Яндекс Диск", home / sub)
        add("Dropbox", home / "Dropbox")
    else:
        for sub in ("YandexDisk", "Яндекс.Диск", "Yandex.Disk"):
            add("Яндекс Диск", home / sub)
        for var in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
            add("OneDrive", os.environ.get(var))
        add("OneDrive", home / "OneDrive")
        add("Google Диск", home / "Google Drive")
        add("Google Диск", home / "Google Диск")
        if IS_WIN:
            for letter in "DEFGHIJKLMNOPQRSTUVWXYZ":
                for sub in ("My Drive", "Мой диск"):
                    add("Google Диск", Path(f"{letter}:\\") / sub)
        for sub in ("Облако Mail.Ru", "Cloud Mail.Ru"):
            add("Облако Mail.ru", home / sub)
        add("Dropbox", home / "Dropbox")
    out, seen = [], set()
    for name, path in found:
        key = os.path.normcase(str(path.resolve()))
        if key not in seen:
            seen.add(key)
            out.append({"name": name, "path": str(path)})
    return out


def shared_dir(chosen: str) -> Path:
    """The program's folder inside the chosen cloud folder (the chosen one when it already is it)."""
    path = Path(str(chosen or "").strip().strip('"'))
    return path if path.name == FOLDER else path / FOLDER


def prepare(shared: Path):
    for sub in (TODO, PDF):
        (shared / sub).mkdir(parents=True, exist_ok=True)


def list_files(shared: Path, suffixes) -> list[Path]:
    """The lists waiting in «В работу», the newest first (no hidden, temporary or Office lock files)."""
    todo = shared / TODO
    try:
        files = [p for p in todo.iterdir() if p.is_file() and p.suffix.lower() in suffixes
                 and not p.name.startswith((".", "~$")) and not p.name.endswith(".tmp")]
    except OSError:
        return []
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)


def signature(path: Path) -> list[int]:
    st = path.stat()
    return [st.st_size, int(st.st_mtime)]


def send(src: Path, shared: Path) -> Path:
    """The list into «В работу» (a new modification time, so the work computer takes it again)."""
    prepare(shared)
    target = shared / TODO / src.name
    tmp = shared / TODO / (src.name + ".tmp")
    shutil.copyfile(src, tmp)
    os.replace(tmp, target)
    return target


def remove(shared: Path, name: str) -> bool:
    target = shared / TODO / Path(name).name
    try:
        target.unlink()
        return True
    except FileNotFoundError:
        return False


def place(data: bytes, name: str, inbox: Path) -> Path:
    """Where a list from the cloud goes in inbox: its own name, unless a different list already has it."""
    inbox.mkdir(parents=True, exist_ok=True)
    target = inbox / name
    if target.exists() and target.read_bytes() != data:
        target = inbox / f"{Path(name).stem} (облако){Path(name).suffix}"
    if not target.exists() or target.read_bytes() != data:
        target.write_bytes(data)
    return target


def read_status(shared: Path) -> dict | None:
    try:
        data = json.loads((shared / STATUS).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) and data.get("app") == "ArticleDownloader" else None
    except (OSError, ValueError):
        return None


def write_status(shared: Path, data: dict):
    """Rewritten whole; the cloud apps sometimes hold the file, then straight over it."""
    target = shared / STATUS
    text = json.dumps(data, ensure_ascii=False, indent=1)
    tmp = shared / (".состояние.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, target)
    except OSError:
        target.write_text(text, encoding="utf-8")


def list_counts(keys: list[str], status_of: dict) -> dict:
    """How a list stands: total, done, queued (incl. running), no_access, captcha, failed (the rest)."""
    out = {"total": len(keys), "done": 0, "queued": 0, "no_access": 0, "captcha": 0, "failed": 0}
    for key in keys:
        st = status_of.get(key, "")
        if st == "success":
            out["done"] += 1
        elif st in ("pending", "running"):
            out["queued"] += 1
        elif st in ("no_access", "captcha"):
            out[st] += 1
        else:
            out["failed"] += 1
    return out


RANK = {"success": 0, "running": 1, "pending": 2, "captcha": 3, "no_access": 4, "unsupported": 5, "failed": 6}


def best_status(items: list[dict]) -> dict:
    """{query in lower case: its best status}: an article downloaded once counts as downloaded."""
    out: dict[str, str] = {}
    for item in items:
        key, st = str(item.get("query") or "").lower(), item.get("status") or ""
        if key not in out or RANK.get(st, 9) < RANK.get(out[key], 9):
            out[key] = st
    return out


def home_view(shared: Path, files: list[Path], status: dict | None, now: float | None = None) -> dict:
    """The home computer's picture: the state of the bar and of each list sent.

    state: never (no work computer yet), stale (not answering), captcha, run,
    limit (the daily limit), stopped («Стоп» pressed there), wait (sent, not
    taken yet), done, idle (answering, nothing sent).
    """
    now = time.time() if now is None else now
    lists, taken = [], (status or {}).get("lists") or {}
    for path in files:
        try:
            size = path.stat().st_size
        except OSError:
            continue
        rec = taken.get(path.name)
        if not rec or rec.get("size") not in (None, size):
            lists.append({"name": path.name, "state": "wait", "total": 0, "done": 0})
            continue
        st = "run" if rec.get("queued") else "done"
        lists.append({"name": path.name, "state": st, "taken": rec.get("taken", ""),
                      **{k: int(rec.get(k) or 0) for k in ("total", "done", "queued", "no_access", "captcha", "failed")}})
    view = {"lists": lists, "computer": (status or {}).get("computer", ""),
            "seen": (status or {}).get("updated_text", "")}
    if not status:
        view["state"] = "never"
        return view
    age = now - float(status.get("updated") or 0)
    if age > STALE_SEC:
        view["state"] = "stale"
        return view
    running = [x for x in lists if x["state"] == "run"]
    if running:
        view.update(done=sum(x["done"] for x in running), total=sum(x["total"] for x in running))
        view["state"] = ("captcha" if status.get("captcha") else
                         "limit" if status.get("limit") else
                         "run" if status.get("running") else "stopped")
    elif any(x["state"] == "wait" for x in lists):
        view["state"] = "wait"
    elif lists:
        view.update(state="done", done=sum(x["done"] for x in lists), total=sum(x["total"] for x in lists))
    else:
        view["state"] = "idle"
    return view
