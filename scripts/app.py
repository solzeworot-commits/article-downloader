# -*- coding: utf-8 -*-
"""
ArticleDownloader portable window (v6.0).

Serves a small page on 127.0.0.1 and opens it as an app window: paste DOIs
or drop a file, press the big button. On launch every database is opened in
the dedicated browser (article_downloader.py --warmup); the queue waits until
the CAPTCHAs there are passed and then runs by itself, one article at a time.

  python\\pythonw.exe scripts\\app.py [files with DOIs ...]

Files given on the command line (dropped on the icon) are queued and started.
Lists put into inbox\\ are picked up too. A second launch only brings the
window back and hands its files to the running copy.
"""

from __future__ import annotations

import base64
import csv
import html
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import traceback
import webbrowser
import zipfile
from collections import Counter, deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse
import urllib.request


SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
DOWNLOADER = SCRIPT_DIR / "article_downloader.py"
UI_FILE = SCRIPT_DIR / "ui" / "index.html"
ICON_FILE = SCRIPT_DIR / "icon.ico"
PYTHON_DIR = ROOT / "python"

LOGS = ROOT / "logs"
INBOX = ROOT / "inbox"
INBOX_DONE = INBOX / "_добавлено"
SETTINGS_PATH = ROOT / "settings.json"
QUEUE_PATH = LOGS / "queue.json"
TOKEN_PATH = LOGS / ".ui_token"
APP_LOG = LOGS / "app.log"
REPORT_PATH = ROOT / "Отчёт.csv"

UI_PORT = 17865
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# A warm-up older than this is repeated before a new start: the check pages
# of the databases expire.
WARM_FRESH_SEC = 3600
# Without a poll from the window for this long and with nothing to do, the
# program exits.
IDLE_EXIT_SEC = 900
JOB_LOG_LINES = 600

LIST_SUFFIXES = {".txt", ".csv", ".tsv", ".ris", ".bib", ".enw", ".nbib",
                 ".xlsx", ".docx"}

# key, title, start page, institution sign-in page, DOI prefixes
BASES = [
    ("elsevier", "ScienceDirect (Elsevier)", "https://www.sciencedirect.com/",
     "https://www.sciencedirect.com/user/institution/login?targetURL=%2F",
     ("10.1016",)),
    ("springer", "SpringerLink", "https://link.springer.com/",
     "https://wayf.springernature.com/?redirect_uri=https%3A%2F%2Flink.springer.com%2F",
     ("10.1007",)),
    ("nature", "Nature", "https://www.nature.com/",
     "https://wayf.springernature.com/?redirect_uri=https%3A%2F%2Fwww.nature.com%2F",
     ("10.1038",)),
    ("wiley", "Wiley", "https://onlinelibrary.wiley.com/",
     "https://onlinelibrary.wiley.com/action/ssostart?redirectUri=%2F",
     ("10.1002", "10.1111")),
    ("acs", "ACS Publications", "https://pubs.acs.org/",
     "https://pubs.acs.org/action/ssostart?redirectUri=%2F",
     ("10.1021",)),
    ("taylor", "Taylor & Francis", "https://www.tandfonline.com/",
     "https://www.tandfonline.com/action/ssostart?redirectUri=%2F",
     ("10.1080",)),
    ("oxford", "Oxford Academic", "https://academic.oup.com/",
     "https://academic.oup.com/", ("10.1093",)),
    ("mdpi", "MDPI", "https://www.mdpi.com/", None, ("10.3390",)),
    ("frontiers", "Frontiers", "https://www.frontiersin.org/", None, ("10.3389",)),
    ("cnki", "CNKI", "https://oversea.cnki.net/kns8s/advsearch?language=en",
     None, ()),
    ("elibrary", "eLIBRARY.RU", "https://www.elibrary.ru/",
     "https://www.elibrary.ru/", ()),
]
BASE_BY_KEY = {b[0]: b for b in BASES}

DEFAULT_SETTINGS = {
    "browser": "auto",
    "pause_sec": 10,
    "daily_limit": 300,
    "job_timeout_min": 10,
    "warmup_timeout_min": 20,
    "title_for_plain_lines": False,
    "pdf_dir": "",
    "desktop_shortcut": True,
    "bases": {b[0]: True for b in BASES},
}

DOI_RE = re.compile(r"10\.\d{4,9}/[^\s\"<>{}|\\^`]+", re.I)
URL_RE = re.compile(r"https?://[^\s\"<>]+", re.I)


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def app_log(text: str):
    try:
        LOGS.mkdir(parents=True, exist_ok=True)
        with APP_LOG.open("a", encoding="utf-8") as f:
            f.write(time.strftime("[%Y-%m-%d %H:%M:%S] ") + text + "\n")
    except Exception:
        pass


def write_json_atomic(path: Path, data):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def load_settings() -> dict:
    settings = json.loads(json.dumps(DEFAULT_SETTINGS))
    try:
        saved = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        for key, value in saved.items():
            if key == "bases" and isinstance(value, dict):
                settings["bases"].update(
                    {k: bool(v) for k, v in value.items() if k in BASE_BY_KEY}
                )
            elif key in settings:
                settings[key] = value
    except FileNotFoundError:
        pass
    except Exception as exc:
        app_log(f"settings.json ignored: {exc}")
    return settings


def pdf_dir() -> Path:
    custom = str(S.settings.get("pdf_dir") or "").strip()
    return Path(custom) if custom else ROOT / "pdf"


def child_python() -> str:
    exe = Path(sys.executable)
    # pythonw.exe has no console; the console twin gives the children proper
    # stdout pipes, and CREATE_NO_WINDOW keeps them invisible.
    if exe.name.lower() == "pythonw.exe" and exe.with_name("python.exe").exists():
        return str(exe.with_name("python.exe"))
    return str(exe)


def child_env(deadline_sec: float | None = None) -> dict:
    env = dict(os.environ)
    for key in ("PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP"):
        env.pop(key, None)
    env.update(
        PYTHONNOUSERSITE="1",
        PYTHONIOENCODING="utf-8",
        PYTHONUTF8="1",
        ARTICLE_BROWSER=str(S.settings.get("browser") or "auto"),
        ARTICLE_PDF_DIR=str(pdf_dir()),
        ARTICLE_LOG_DIR=str(LOGS),
    )
    if deadline_sec:
        env["ARTICLE_JOB_DEADLINE"] = f"{time.time() + deadline_sec:.0f}"
    else:
        env.pop("ARTICLE_JOB_DEADLINE", None)
    return env


def spawn_downloader(args: list[str], deadline_sec: float | None = None):
    return subprocess.Popen(
        [child_python(), str(DOWNLOADER), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=child_env(deadline_sec),
        cwd=str(ROOT),
        creationflags=NO_WINDOW,
    )


def child_pids(pid: int) -> set[int]:
    try:
        import psutil
        return {c.pid for c in psutil.Process(pid).children(recursive=True)}
    except Exception:
        return set()


def kill_job_tree(proc: subprocess.Popen | None, known: set[int] = frozenset()):
    """
    Kill a downloader with its Playwright node driver and PDF worker, which
    otherwise keep the output pipe open. The dedicated browser is left running.
    """
    if proc is None:
        return
    try:
        import psutil
        pids = set(known) | child_pids(proc.pid)
        for pid in pids:
            try:
                child = psutil.Process(pid)
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


# ---------------------------------------------------------------------------
# reading DOI lists
# ---------------------------------------------------------------------------

def _clean_doi(doi: str) -> str:
    doi = doi.split("#")[0]
    while doi and doi[-1] in ".,;:'":
        doi = doi[:-1]
    # Keep the brackets that belong to the DOI (10.1016/S0021-9673(01)00123-4)
    # and drop the ones of the surrounding prose.
    for open_ch, close_ch in (("(", ")"), ("[", "]")):
        while doi.endswith(close_ch) and doi.count(close_ch) > doi.count(open_ch):
            doi = doi[:-1]
    while doi and doi[-1] in ".,;:'":
        doi = doi[:-1]
    return doi


def parse_entries(text: str, title_mode: bool) -> tuple[list[str], int]:
    """
    Pull queue entries out of free text: every DOI, article links without a
    DOI, "title:"/"cnki:" lines as they are and, with title_mode, any other
    line as a title search. Returns (entries, unrecognised line count).
    """
    entries, seen, ignored = [], set(), 0

    def add(value: str):
        key = value.lower()
        if key not in seen:
            seen.add(key)
            entries.append(value)

    for line in text.splitlines():
        s = line.strip().strip("﻿")
        if not s:
            continue
        low = s.lower()
        if low.startswith(("title:", "cnki:")):
            add(s)
            continue

        dois = [_clean_doi(d) for d in DOI_RE.findall(unquote(s))]
        dois = [d for d in dois if "/" in d and len(d) > 8]
        if dois:
            for doi in dois:
                add(doi)
            continue

        urls = [u.rstrip(".,;)]") for u in URL_RE.findall(s)]
        if urls:
            for url in urls:
                add(url)
            continue

        words = re.findall(r"[^\W\d_]{2,}", s)
        if title_mode and len(words) >= 3:
            add("title: " + s)
        else:
            ignored += 1

    return entries, ignored


def _xml_text(xml: str, breaks: tuple[str, ...]) -> str:
    for tag in breaks:
        xml = xml.replace(tag, "\n")
    xml = re.sub(r"<w:tab/>", " ", xml)
    return html.unescape(re.sub(r"<[^>]+>", "", xml))


def file_to_text(name: str, data: bytes) -> str:
    suffix = Path(name).suffix.lower()

    if suffix in (".xlsx", ".docx"):
        parts = []
        with zipfile.ZipFile(BytesIO(data)) as zf:
            for item in zf.namelist():
                if suffix == ".docx" and item == "word/document.xml":
                    parts.append(_xml_text(zf.read(item).decode("utf-8", "replace"),
                                           ("</w:p>",)))
                elif suffix == ".xlsx" and (
                        item == "xl/sharedStrings.xml"
                        or item.startswith("xl/worksheets/sheet")):
                    parts.append(_xml_text(zf.read(item).decode("utf-8", "replace"),
                                           ("</si>", "</c>", "</row>")))
        return "\n".join(parts)

    for encoding in ("utf-8-sig", "cp1251"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


# ---------------------------------------------------------------------------
# state
# ---------------------------------------------------------------------------

class State:
    def __init__(self):
        self.lock = threading.RLock()
        self.settings = load_settings()
        self.items: list[dict] = []
        self.next_id = 1
        self.items_version = 1
        self.day = time.strftime("%Y-%m-%d")
        self.day_count = 0
        self.job_logs: dict[int, deque] = {}

        self.running = False
        self.stop_requested = False
        self.phase = "idle"   # idle, warmup_wait, downloading, pause, limit
        self.current_id = None
        self.pause_until = 0.0
        self.captcha_now = False
        self.proc = None

        self.warm = self._warm_blank()
        self.warm_proc = None

        self.browser = {}
        self.first_run = False
        self.notice = ""
        self.last_poll = time.time()
        self.token = secrets.token_urlsafe(24)
        self.server = None

    @staticmethod
    def _warm_blank():
        return {"running": False, "done": False, "skipped": False,
                "cleared": False, "started_at": 0, "finished_at": 0,
                "error": "", "bases": {}}

    # -- persistence -------------------------------------------------------

    def load_queue(self):
        try:
            data = json.loads(QUEUE_PATH.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except Exception as exc:
            app_log(f"queue.json ignored: {exc}")
            return
        self.items = data.get("items", [])
        for item in self.items:
            if item.get("status") == "running":
                item["status"] = "pending"
        self.next_id = max([i["id"] for i in self.items] + [0]) + 1
        if data.get("day") == self.day:
            self.day_count = int(data.get("day_count", 0))

    def save_queue(self):
        with self.lock:
            data = {"items": self.items, "day": self.day,
                    "day_count": self.day_count}
            try:
                write_json_atomic(QUEUE_PATH, data)
            except Exception as exc:
                app_log(f"queue.json not saved: {exc}")

    def touch_items(self):
        self.items_version += 1
        self.save_queue()

    # -- queue -------------------------------------------------------------

    def add_entries(self, entries: list[str]) -> tuple[int, int]:
        added = duplicates = 0
        with self.lock:
            known = {i["query"].lower() for i in self.items
                     if i["status"] not in ("failed", "no_access", "unsupported")}
            for entry in entries:
                if entry.lower() in known:
                    duplicates += 1
                    continue
                known.add(entry.lower())
                self.items.append({
                    "id": self.next_id, "query": entry, "status": "pending",
                    "file": "", "message": "", "attempts": 0,
                    "added": time.strftime("%Y-%m-%d %H:%M"),
                })
                self.next_id += 1
                added += 1
            if added:
                self.touch_items()
        return added, duplicates

    def next_pending(self):
        with self.lock:
            pending = [(i["attempts"], n, i) for n, i in enumerate(self.items)
                       if i["status"] == "pending"]
            return min(pending, key=lambda t: t[:2])[2] if pending else None

    def item(self, item_id: int):
        with self.lock:
            return next((i for i in self.items if i["id"] == item_id), None)


S = State()


# ---------------------------------------------------------------------------
# browser info and desktop shortcut
# ---------------------------------------------------------------------------

def refresh_browser_info():
    os.environ["ARTICLE_BROWSER"] = str(S.settings.get("browser") or "auto")
    try:
        import article_downloader as ad
        kind, exe = ad.dedicated_browser()
        profile = ad.browser_profile_dir(kind)
        S.browser = {"kind": kind, "title": ad.BROWSER_TITLES.get(kind, kind),
                     "exe": exe, "error": "",
                     "yandex_missing": ad.find_browser_exe("yandex") is None}
        S.first_run = not profile.exists()
    except Exception as exc:
        S.browser = {"kind": "", "title": "", "exe": "", "error": str(exc),
                     "yandex_missing": True}


def ensure_desktop_shortcut():
    pythonw = PYTHON_DIR / "pythonw.exe"
    if os.name != "nt" or not pythonw.exists() or not S.settings.get("desktop_shortcut"):
        return

    def q(value) -> str:
        return "'" + str(value).replace("'", "''") + "'"

    # -E -s: PYTHONPATH/PYTHONHOME or a user site of another Python on this
    # computer must not leak into the bundled one.
    arguments = f'-E -s "{SCRIPT_DIR / "app.py"}"'
    script = f"""
$desktop = [Environment]::GetFolderPath('Desktop')
$link = Join-Path $desktop {q('Скачать статьи.lnk')}
$shell = New-Object -ComObject WScript.Shell
$s = $shell.CreateShortcut($link)
if ($s.TargetPath -ne {q(pythonw)} -or $s.Arguments -ne {q(arguments)}) {{
    $s.TargetPath = {q(pythonw)}
    $s.Arguments = {q(arguments)}
    $s.WorkingDirectory = {q(ROOT)}
    $s.IconLocation = {q(f"{ICON_FILE},0")}
    $s.Description = {q('Загрузчик статей')}
    $s.Save()
    Write-Output 'shortcut updated'
}}
"""
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy",
             "Bypass", "-EncodedCommand", encoded],
            capture_output=True, text=True, timeout=60, creationflags=NO_WINDOW,
        )
        if result.stdout.strip():
            app_log("Desktop shortcut: " + result.stdout.strip())
    except Exception as exc:
        app_log(f"Desktop shortcut not created: {exc}")


# ---------------------------------------------------------------------------
# warm-up: open the databases and wait for the CAPTCHAs
# ---------------------------------------------------------------------------

def _warm_url(key: str, home: str, prefixes: tuple) -> str:
    """An article of the queue triggers the real check; the home page if none."""
    with S.lock:
        for item in S.items:
            if item["status"] != "pending":
                continue
            query = item["query"]
            if prefixes and query.lower().startswith(tuple(p + "/" for p in prefixes)):
                return "https://doi.org/" + query
            if key == "elibrary" and "elibrary.ru" in query.lower():
                return query
    return home


def start_warmup():
    with S.lock:
        if S.warm["running"]:
            return
        if S.browser.get("error"):
            S.warm = S._warm_blank()
            S.warm.update(done=True, error=S.browser["error"])
            return
        enabled = [b for b in BASES if S.settings["bases"].get(b[0], True)]
        if not enabled:
            S.warm = S._warm_blank()
            S.warm.update(done=True, cleared=True, finished_at=time.time())
            return
        bases = [{"key": b[0], "url": _warm_url(b[0], b[2], b[4])} for b in enabled]
        S.warm = S._warm_blank()
        S.warm.update(running=True, started_at=time.time(),
                      bases={b["key"]: {"state": "loading", "detail": ""} for b in bases})
    threading.Thread(target=_warmup_thread, args=(bases,), daemon=True).start()


def _warmup_thread(bases: list[dict]):
    path = LOGS / "warmup_bases.json"
    proc = None
    try:
        path.write_text(json.dumps(bases, ensure_ascii=False), encoding="utf-8")
        limit = max(1, int(S.settings.get("warmup_timeout_min") or 20)) * 60
        proc = spawn_downloader(["--warmup", str(path)], deadline_sec=limit)
        with S.lock:
            S.warm_proc = proc
        for line in proc.stdout:
            line = line.rstrip()
            if line.startswith("WARMUP|"):
                _, key, state, detail = (line.split("|", 3) + ["", ""])[:4]
                with S.lock:
                    base = S.warm["bases"].setdefault(key, {})
                    base["state"] = state
                    if detail or state != "ok":
                        base["detail"] = detail
            elif line.startswith("RESULT|"):
                status, _, message = line[7:].partition("|")
                with S.lock:
                    if status == "SUCCESS":
                        S.warm["cleared"] = True
                    elif status == "FAILED":
                        S.warm["error"] = message
        proc.wait()
    except Exception as exc:
        app_log(f"Warm-up error: {exc}\n{traceback.format_exc()}")
        with S.lock:
            S.warm["error"] = str(exc)
    finally:
        with S.lock:
            S.warm.update(running=False, done=True, finished_at=time.time())
            S.warm_proc = None


def skip_warmup():
    with S.lock:
        proc = S.warm_proc
        S.warm["skipped"] = True
    kill_job_tree(proc)


# ---------------------------------------------------------------------------
# queue runner
# ---------------------------------------------------------------------------

def start_queue():
    with S.lock:
        if S.next_pending() is None:
            return
        warm = S.warm
        stale = (not warm["done"] or warm["skipped"] or warm["error"]
                 or time.time() - warm["finished_at"] > WARM_FRESH_SEC)
        if not warm["running"] and stale and not S.running:
            start_warmup()
        if S.running:
            return
        S.running = True
        S.stop_requested = False
        S.notice = ""
    threading.Thread(target=_runner, daemon=True).start()


def stop_queue():
    with S.lock:
        S.stop_requested = True
        proc = S.proc
    kill_job_tree(proc)


def _limit_reached() -> bool:
    with S.lock:
        today = time.strftime("%Y-%m-%d")
        if today != S.day:
            S.day, S.day_count = today, 0
        limit = int(S.settings.get("daily_limit") or 0)
        return limit > 0 and S.day_count >= limit


def _runner():
    try:
        while True:
            with S.lock:
                if S.stop_requested:
                    break
                waiting = S.warm["running"] and not S.warm["skipped"]
                S.phase = "warmup_wait" if waiting else S.phase
            if waiting:
                time.sleep(1)
                continue

            item = S.next_pending()
            if item is None:
                break
            if _limit_reached():
                with S.lock:
                    S.phase = "limit"
                    S.notice = (f"Достигнут дневной лимит ({S.day_count} статей). "
                                "Остальные скачаются завтра или после увеличения "
                                "лимита в настройках.")
                break

            _run_item(item)

            with S.lock:
                if S.stop_requested or S.next_pending() is None:
                    continue
                S.phase = "pause"
                S.pause_until = time.time() + max(0, int(S.settings.get("pause_sec") or 0))
            while time.time() < S.pause_until and not S.stop_requested:
                time.sleep(0.3)
    except Exception as exc:
        app_log(f"Runner error: {exc}\n{traceback.format_exc()}")
    finally:
        with S.lock:
            S.running = False
            if S.phase != "limit":
                S.phase = "idle"
            S.current_id = None
            S.captcha_now = False
            S.proc = None
            S.save_queue()


REPORT_STATUS = {
    "success": "скачано",
    "captcha": "капча",
    "no_access": "нет доступа",
    "unsupported": "не поддерживается",
    "failed": "ошибка",
}

STATUS_BY_RESULT = {
    "SUCCESS": "success",
    "CAPTCHA_REQUIRED": "captcha",
    "NO_ACCESS": "no_access",
    "UNSUPPORTED": "unsupported",
    "FAILED": "failed",
}


def _run_item(item: dict):
    limit = max(2, int(S.settings.get("job_timeout_min") or 10)) * 60
    log = deque(maxlen=JOB_LOG_LINES)
    with S.lock:
        item["status"] = "running"
        item["attempts"] = item.get("attempts", 0) + 1
        item["message"] = ""
        S.current_id = item["id"]
        S.phase = "downloading"
        S.captcha_now = False
        S.job_logs[item["id"]] = log
        S.day_count += 1
        S.touch_items()

    result = {"status": "", "message": ""}
    proc = spawn_downloader([item["query"]], deadline_sec=limit)
    with S.lock:
        S.proc = proc

    def read_output():
        for raw in proc.stdout:
            line = raw.rstrip("\n")
            log.append(line)
            low = line.lower()
            if "human verification detected" in low or "жду ручную проверку" in low:
                S.captcha_now = True
            elif "manual verification completed" in low or "проверка пройдена" in low:
                S.captcha_now = False
            if line.startswith("RESULT|"):
                status, _, message = line[7:].partition("|")
                result.update(status=status, message=message)

    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()

    known, timed_out = set(), False
    hard_deadline = time.time() + limit + 30
    while True:
        try:
            code = proc.wait(timeout=2)
            break
        except subprocess.TimeoutExpired:
            known |= child_pids(proc.pid)
            if S.stop_requested or time.time() > hard_deadline:
                timed_out = not S.stop_requested
                kill_job_tree(proc, known)
    reader.join(timeout=10)
    if reader.is_alive():
        kill_job_tree(proc, known)
        reader.join(timeout=5)

    with S.lock:
        S.proc = None
        S.captcha_now = False
        status = STATUS_BY_RESULT.get(result["status"], "failed")
        message = result["message"]

        if S.stop_requested and not result["status"]:
            status, message = "pending", "остановлено"
            item["attempts"] -= 1
            S.day_count = max(0, S.day_count - 1)
        elif timed_out:
            status, message = "failed", f"не уложилось в {limit // 60} мин"
        elif not result["status"]:
            message = next((l for l in reversed(log) if l.strip()), f"код {code}")

        if status == "captcha" and item["attempts"] < 2:
            status = "pending"
            message = "была капча — повторю в конце очереди"

        item["status"] = status
        item["message"] = message[:500]
        item["file"] = message if status == "success" else ""
        item["finished"] = time.strftime("%Y-%m-%d %H:%M")
        S.touch_items()

    if status == "pending" and result["status"] == "CAPTCHA_REQUIRED":
        # The site keeps asking: open the databases again so the check is in
        # front of the user, and hold the queue until it is passed (or the
        # warm-up time runs out).
        start_warmup()
    if status != "pending":
        _report(item)


def _report(item: dict):
    try:
        new = not REPORT_PATH.exists()
        with REPORT_PATH.open("a", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f, delimiter=";")
            if new:
                writer.writerow(["Время", "Запрос", "Результат", "Файл", "Сообщение"])
            writer.writerow([item.get("finished", ""), item["query"],
                             REPORT_STATUS.get(item["status"], item["status"]),
                             item.get("file", ""),
                             "" if item["status"] == "success" else item["message"]])
    except Exception as exc:
        app_log(f"Report not written: {exc}")


# ---------------------------------------------------------------------------
# inbox folder and files from the command line
# ---------------------------------------------------------------------------

def import_files(paths: list[str]) -> int:
    total = 0
    for raw in paths:
        path = Path(raw)
        try:
            text = file_to_text(path.name, path.read_bytes())
        except Exception as exc:
            app_log(f"File not read {path}: {exc}")
            continue
        entries, _ = parse_entries(text, bool(S.settings.get("title_for_plain_lines")))
        added, _ = S.add_entries(entries)
        app_log(f"Imported {added} entr(ies) from {path}")
        total += added
    return total


def inbox_loop():
    while True:
        try:
            INBOX.mkdir(parents=True, exist_ok=True)
            files = [p for p in INBOX.iterdir()
                     if p.is_file() and p.suffix.lower() in LIST_SUFFIXES
                     and time.time() - p.stat().st_mtime > 3]
            for path in files:
                added = import_files([str(path)])
                INBOX_DONE.mkdir(parents=True, exist_ok=True)
                target = INBOX_DONE / f"{time.strftime('%Y%m%d-%H%M%S')}_{path.name}"
                try:
                    path.replace(target)
                except OSError:
                    path.unlink(missing_ok=True)
                if added:
                    start_queue()
        except Exception as exc:
            app_log(f"Inbox error: {exc}")
        time.sleep(20)


def idle_exit_loop():
    while True:
        time.sleep(30)
        with S.lock:
            busy = S.running or S.warm["running"]
            idle = time.time() - S.last_poll > IDLE_EXIT_SEC
        if idle and not busy:
            app_log("No window for a long time and nothing to do: exiting")
            shutdown()
            return


def shutdown():
    stop_queue()
    with S.lock:
        warm = S.warm_proc
    kill_job_tree(warm)
    S.save_queue()
    if S.server:
        threading.Thread(target=S.server.shutdown, daemon=True).start()


# ---------------------------------------------------------------------------
# opening things on the desktop
# ---------------------------------------------------------------------------

def open_path(path: Path):
    if os.name == "nt":
        os.startfile(str(path))
    else:
        subprocess.Popen(["xdg-open", str(path)], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)


def open_ui_window(url: str):
    if os.environ.get("ARTICLE_UI_NO_WINDOW") == "1":
        return
    try:
        import article_downloader as ad
        for kind in ("edge", "chrome", "yandex"):
            exe = ad.find_browser_exe(kind)
            if exe:
                subprocess.Popen([exe, f"--app={url}", "--window-size=1120,760"],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return
    except Exception as exc:
        app_log(f"App window not opened: {exc}")
    webbrowser.open(url)


def open_in_dedicated_browser(urls: list[str]):
    def run():
        proc = spawn_downloader(["--open", *urls])
        for line in proc.stdout:
            if line.startswith("RESULT|FAILED|"):
                with S.lock:
                    S.notice = "Браузер не открылся: " + line.split("|", 2)[2].strip()
        proc.wait()
    threading.Thread(target=run, daemon=True).start()


def restart_browser():
    def run():
        proc = spawn_downloader(["--restart-browser"])
        proc.communicate()
    threading.Thread(target=run, daemon=True).start()


# ---------------------------------------------------------------------------
# HTTP API for the window
# ---------------------------------------------------------------------------

def public_state(known_version: int) -> dict:
    with S.lock:
        counts = Counter(i["status"] for i in S.items)
        bases = []
        for key, name, home, login, _ in BASES:
            info = S.warm["bases"].get(key, {})
            bases.append({
                "key": key, "name": name, "login": bool(login),
                "enabled": bool(S.settings["bases"].get(key, True)),
                "state": info.get("state", ""), "detail": info.get("detail", ""),
            })
        data = {
            "phase": S.phase,
            "running": S.running,
            "current": S.current_id,
            "pause_left": max(0, int(S.pause_until - time.time())) if S.phase == "pause" else 0,
            "captcha_now": S.captcha_now,
            "warm": {k: v for k, v in S.warm.items() if k != "bases"} | {"bases": bases},
            "browser": S.browser,
            "first_run": S.first_run,
            "day_count": S.day_count,
            "settings": S.settings,
            "counts": dict(counts),
            "total": len(S.items),
            "items_version": S.items_version,
            "notice": S.notice,
            "pdf_dir": str(pdf_dir()),
        }
        if known_version != S.items_version:
            data["items"] = [
                {k: i.get(k, "") for k in ("id", "query", "status", "file",
                                            "message", "attempts")}
                for i in S.items
            ]
    return data


def update_settings(new: dict):
    with S.lock:
        for key, value in new.items():
            if key == "bases" and isinstance(value, dict):
                S.settings["bases"].update(
                    {k: bool(v) for k, v in value.items() if k in BASE_BY_KEY})
            elif key in ("pause_sec", "daily_limit", "job_timeout_min",
                         "warmup_timeout_min"):
                try:
                    S.settings[key] = max(0, int(value))
                except (TypeError, ValueError):
                    pass
            elif key in ("title_for_plain_lines", "desktop_shortcut"):
                S.settings[key] = bool(value)
            elif key in ("browser", "pdf_dir"):
                S.settings[key] = str(value or "").strip()
        S.settings["browser"] = S.settings["browser"] or "auto"
        write_json_atomic(SETTINGS_PATH, S.settings)
        if S.phase == "limit" and not _limit_reached():
            S.phase, S.notice = "idle", ""
    refresh_browser_info()


def handle_api(path: str, body: dict) -> dict:
    if path == "/api/parse":
        title_mode = bool(body.get("title_mode"))
        text = body.get("text") or ""
        if body.get("file_b64"):
            text = file_to_text(body.get("file_name") or "list.txt",
                                base64.b64decode(body["file_b64"]))
        entries, ignored = parse_entries(text, title_mode)
        return {"entries": entries, "ignored": ignored}

    if path == "/api/start":
        entries, ignored = parse_entries(body.get("text") or "",
                                         bool(body.get("title_mode")))
        if "title_mode" in body:
            update_settings({"title_for_plain_lines": bool(body["title_mode"])})
        added, duplicates = S.add_entries(entries)
        start_queue()
        return {"added": added, "duplicates": duplicates, "ignored": ignored}

    if path == "/api/stop":
        stop_queue()
        return {}

    if path == "/api/retry":
        with S.lock:
            for item in S.items:
                if item["status"] in ("failed", "no_access", "captcha", "unsupported"):
                    item.update(status="pending", attempts=0, message="")
            S.touch_items()
        start_queue()
        return {}

    if path == "/api/clear":
        # "done": drop the downloaded ones; "all": everything not running.
        drop = ("success",) if body.get("what") == "done" else None
        with S.lock:
            S.items = [i for i in S.items if i["status"] == "running"
                       or (drop is not None and i["status"] not in drop)]
            S.touch_items()
        return {}

    if path == "/api/remove":
        with S.lock:
            S.items = [i for i in S.items
                       if i["id"] != body.get("id") or i["status"] == "running"]
            S.touch_items()
        return {}

    if path == "/api/warmup":
        with S.lock:
            proc = S.warm_proc
        kill_job_tree(proc)
        time.sleep(0.5)
        start_warmup()
        return {}

    if path == "/api/skip_warmup":
        skip_warmup()
        return {}

    if path == "/api/open":
        what = body.get("what")
        if what == "pdf_dir":
            pdf_dir().mkdir(parents=True, exist_ok=True)
            open_path(pdf_dir())
        elif what == "report" and REPORT_PATH.exists():
            open_path(REPORT_PATH)
        elif what == "file":
            item = S.item(body.get("id"))
            if item and item.get("file") and Path(item["file"]).exists():
                open_path(Path(item["file"]))
        return {}

    if path == "/api/login":
        base = BASE_BY_KEY.get(body.get("key"))
        if base:
            open_in_dedicated_browser([base[3] or base[2]])
        return {}

    if path == "/api/settings":
        update_settings(body)
        return {}

    if path == "/api/restart_browser":
        restart_browser()
        return {}

    if path == "/api/hello":
        added = import_files(body.get("files") or [])
        if added:
            start_queue()
        return {"added": added}

    if path == "/api/quit":
        shutdown()
        return {}

    raise KeyError(path)


class Handler(BaseHTTPRequestHandler):
    server_version = "ArticleDownloader"

    def log_message(self, fmt, *args):
        pass

    def _allowed(self) -> bool:
        host = (self.headers.get("Host") or "").lower()
        return host in (f"127.0.0.1:{UI_PORT}", f"localhost:{UI_PORT}")

    def _token_ok(self, query: dict) -> bool:
        token = self.headers.get("X-Token") or (query.get("t") or [""])[0]
        return secrets.compare_digest(token, S.token)

    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data, code: int = 200):
        self._send(code, json.dumps(data, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def do_GET(self):
        if not self._allowed():
            return self._send(403, b"forbidden", "text/plain")
        url = urlparse(self.path)
        query = parse_qs(url.query)

        if url.path == "/":
            page = UI_FILE.read_text(encoding="utf-8").replace("__TOKEN__", S.token)
            return self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")
        if url.path == "/favicon.ico" and ICON_FILE.exists():
            return self._send(200, ICON_FILE.read_bytes(), "image/x-icon")
        if url.path == "/api/ping":
            return self._json({"app": "ArticleDownloader"})
        if not self._token_ok(query):
            return self._send(403, b"forbidden", "text/plain")

        if url.path == "/api/state":
            S.last_poll = time.time()
            try:
                version = int((query.get("v") or ["0"])[0])
            except ValueError:
                version = 0
            return self._json(public_state(version))
        if url.path == "/api/log":
            try:
                item_id = int((query.get("id") or ["0"])[0])
            except ValueError:
                item_id = 0
            lines = list(S.job_logs.get(item_id, []))
            text = "\n".join(lines) or "Журнала нет: статья ещё не скачивалась после запуска программы."
            return self._send(200, text.encode("utf-8"), "text/plain; charset=utf-8")
        return self._send(404, b"not found", "text/plain")

    def do_POST(self):
        url = urlparse(self.path)
        if not self._allowed() or not self._token_ok(parse_qs(url.query)):
            return self._send(403, b"forbidden", "text/plain")
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}") if length else {}
            return self._json(handle_api(url.path, body))
        except KeyError:
            return self._send(404, b"not found", "text/plain")
        except Exception as exc:
            app_log(f"API {url.path} error: {exc}\n{traceback.format_exc()}")
            return self._json({"error": str(exc)}, 500)


# ---------------------------------------------------------------------------
# start
# ---------------------------------------------------------------------------

def message_box(text: str):
    app_log(text)
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, text, "Загрузчик статей", 0x10)
            return
        except Exception:
            pass
    print(text, file=sys.stderr)


def hand_over_to_running_copy(files: list[str]) -> bool:
    """A copy is already running: give it our files and show its window."""
    try:
        token = TOKEN_PATH.read_text(encoding="utf-8").strip()
        base = f"http://127.0.0.1:{UI_PORT}"
        with urllib.request.urlopen(base + "/api/ping", timeout=3) as r:
            if json.loads(r.read()).get("app") != "ArticleDownloader":
                return False
        req = urllib.request.Request(
            base + "/api/hello",
            data=json.dumps({"files": files}).encode("utf-8"),
            headers={"Content-Type": "application/json", "X-Token": token},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=30).read()
        open_ui_window(f"{base}/?t={token}")
        return True
    except Exception as exc:
        app_log(f"Hand-over failed: {exc}")
        return False


def main():
    LOGS.mkdir(parents=True, exist_ok=True)
    try:
        if APP_LOG.exists() and APP_LOG.stat().st_size > 5_000_000:
            APP_LOG.replace(APP_LOG.with_suffix(".old.log"))
    except OSError:
        pass

    files = [a for a in sys.argv[1:] if Path(a).is_file()]

    # Proxies of the user's normal browser must not catch the local page.
    os.environ["NO_PROXY"] = ",".join(
        x for x in (os.environ.get("NO_PROXY"), "127.0.0.1", "localhost") if x)

    try:
        server = ThreadingHTTPServer(("127.0.0.1", UI_PORT), Handler)
    except OSError:
        if hand_over_to_running_copy(files):
            return
        message_box(f"Порт {UI_PORT} занят другой программой. "
                    "Закройте её или перезагрузите компьютер.")
        return

    S.server = server
    TOKEN_PATH.write_text(S.token, encoding="utf-8")
    S.load_queue()
    refresh_browser_info()
    app_log(f"Started; browser: {S.browser.get('title') or S.browser.get('error')}")

    if files:
        import_files(files)

    url = f"http://127.0.0.1:{UI_PORT}/?t={S.token}"
    open_ui_window(url)

    threading.Thread(target=ensure_desktop_shortcut, daemon=True).start()
    threading.Thread(target=inbox_loop, daemon=True).start()
    threading.Thread(target=idle_exit_loop, daemon=True).start()

    # Launch = open the databases; unfinished articles go on by themselves
    # as soon as the CAPTCHAs are passed.
    start_warmup()
    if S.next_pending() is not None:
        start_queue()

    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        S.save_queue()
        app_log("Stopped")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        message_box(f"Программа не запустилась:\n{exc}\n\nПодробности: {APP_LOG}")
        app_log(traceback.format_exc())
