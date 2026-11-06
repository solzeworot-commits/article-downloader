# -*- coding: utf-8 -*-
# © 2026 Bucaneiro. Бесплатная программа; условия — в файле ЛИЦЕНЗИЯ.txt.
"""
ArticleDownloader portable window (v7.1).

Serves a small page on 127.0.0.1 and opens it as an app window. The start
screen has two buttons:

- "Получить список DOI": a keyword search in Scopus / Web of Science /
  OpenAlex (doi_search.py) or a site export, saved as a list in inbox\\;
- "Скачать статьи": pick a list from inbox\\ (Windows file dialog or the list
  in the window). Then every database is opened in the dedicated browser
  (article_downloader.py --warmup); the queue waits until the CAPTCHAs there
  are passed and runs by itself, one article at a time.

  python\\pythonw.exe scripts\\app.py [files with DOIs ...]

Files given on the command line (dropped on the icon) are queued and started
at once. A second launch only brings the window back and hands its files to
the running copy.
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
from urllib.parse import parse_qs, quote, unquote, urlparse
import urllib.request

import ai_helper as ai
import cite_styles as cs
import doi_search as ds
import i18n
import review as rv
import support as sp
from i18n import tr


SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
DOWNLOADER = SCRIPT_DIR / "article_downloader.py"
UI_FILE = SCRIPT_DIR / "ui" / "index.html"
ICON_FILE = SCRIPT_DIR / "icon.ico"
KEY_IMG_DIR = SCRIPT_DIR / "ui" / "img" / "keys"
# The author's avatar in "О программе": a crop of a CC BY 2.0 photo, credited there.
AVATAR_FILE = SCRIPT_DIR / "ui" / "img" / "avatar.jpg"
# The author's details for "Поблагодарить автора"; without the file the
# button is not shown.
DONATE_FILE = SCRIPT_DIR / "donate.json"
PYTHON_DIR = ROOT / "python"

LOGS = ROOT / "logs"
INBOX = ROOT / "inbox"
SETTINGS_PATH = ROOT / "settings.json"
QUEUE_PATH = LOGS / "queue.json"
TOKEN_PATH = LOGS / ".ui_token"
APP_LOG = LOGS / "app.log"
REPORT_PATH = ROOT / "Отчёт.csv"
BIBLIO_DIR = ROOT / "Список литературы"

UI_PORT = 17865
APP_VERSION = "7.9"
APP_AUTHOR = "Bucaneiro"
OFFICIAL_URL = "https://github.com/solzeworot-commits/article-downloader"
DOWNLOAD_URL = OFFICIAL_URL + "/releases/tag/portable-latest"
# The Windows build publishes version.json next to the zip; checked at start.
UPDATE_URL = (os.environ.get("ARTICLE_UPDATE_URL")
              or OFFICIAL_URL + "/releases/download/portable-latest/version.json")
ISSUES_URL = OFFICIAL_URL + "/issues/new"
UPDATE_ZIP_URL = OFFICIAL_URL + "/releases/download/portable-latest/ArticleDownloader_Portable.zip"
# ЛИЦЕНЗИЯ.txt is written by the build; a source checkout has LICENSE.
LICENSE_FILES = (ROOT / "ЛИЦЕНЗИЯ.txt", ROOT.parent / "LICENSE")
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# A warm-up older than this is repeated before a new start: the check pages
# of the databases expire.
WARM_FRESH_SEC = 3600
# Without a poll from the window for this long and with nothing to do, the
# program exits.
IDLE_EXIT_SEC = 900
JOB_LOG_LINES = 613

LIST_SUFFIXES = {".txt", ".csv", ".tsv", ".ris", ".bib", ".enw", ".nbib",
                 ".xlsx", ".docx"}
EXPORT_SUFFIXES = {".csv", ".txt", ".tsv", ".ris", ".bib", ".xls", ".xlsx"}

# Pages the key guide may open in the user's normal browser.
GUIDE_LINKS = {
    "elsevier": "https://dev.elsevier.com/",
    "elsevier_keys": "https://dev.elsevier.com/apikey/manage",
    "clarivate": "https://developer.clarivate.com/",
    "wos_starter": "https://developer.clarivate.com/apis/wos-starter",
    "openalex": "https://openalex.org/",
    "perplexity": "https://www.perplexity.ai/",
    "openai_keys": "https://platform.openai.com/api-keys",
    "openai_billing": "https://platform.openai.com/settings/organization/billing/overview",
    "anthropic_keys": "https://console.anthropic.com/settings/keys",
    "anthropic_billing": "https://console.anthropic.com/settings/billing",
    "gigachat": "https://developers.sber.ru/portal/products/gigachat-api",
    "gosuslugi_crt": "https://www.gosuslugi.ru/crt",
    "yandex_cloud": "https://console.yandex.cloud/",
    "chat_tg": "https://t.me/ArticleDownloaderChat",
    "chat_max": "https://max.ru/join/_ADhfiFX_Bf37RsP1VR8hDHUdFgj_im_R5DqDx4Iseo",
    "official": DOWNLOAD_URL,
    "photo_source": "https://commons.wikimedia.org/wiki/File:Jolly_Roger_at_Portsmouth_Historic_Dockyard.jpg",
    "photo_license": "https://creativecommons.org/licenses/by/2.0/",
}
SCOPUS_RESULTS_URL = "https://www.scopus.com/results/results.uri?sort=plf-f&src=s&sot=a&sdt=a&s="
WOS_ADVANCED_URL = "https://www.webofscience.com/wos/woscc/advanced-search"

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
    "lang": "",            # "" = by the Windows language
    "ai_provider": "",     # the AI helper's service (ai_helper.PROVIDERS)
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

        self.search = {"running": False, "progress": {}, "error": "",
                       "version": 0, "summary": None}
        self.search_records: list[dict] = []
        self.export_since = 0.0
        self.key_checks: dict[str, dict] = {}
        self.list_cache: dict[str, tuple] = {}
        self.last_list = ""
        self.reviews: dict[str, dict] = {}       # list file name -> review (rv)
        self.review_jobs: dict[str, dict] = {}   # list file name -> lookup progress
        self.ai_jobs: dict[str, dict] = {}       # list file name -> AI pre-screening progress
        self.style_jobs: dict[str, dict] = {}    # list file name -> journal style formatting progress
        self.style_failed: dict[tuple, set] = {} # (list, style) -> DOIs doi.org could not format

        self.browser = {}
        self.update: dict | None = None    # support.check_update()
        self.update_job = {"state": "", "done": 0, "total": 0, "error": ""}
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


def ps_quote(value) -> str:
    """A PowerShell single-quoted string."""
    return "'" + str(value).replace("'", "''") + "'"


def run_powershell(script: str, timeout: int) -> str:
    """Run a script (any Unicode in it) without a console window; its stdout."""
    script = "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8\n" + script
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-STA", "-ExecutionPolicy",
         "Bypass", "-EncodedCommand", encoded],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout, creationflags=NO_WINDOW,
    )
    return result.stdout.strip()


def ensure_desktop_shortcut():
    pythonw = PYTHON_DIR / "pythonw.exe"
    if os.name != "nt" or not pythonw.exists() or not S.settings.get("desktop_shortcut"):
        return

    q = ps_quote
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
    try:
        out = run_powershell(script, timeout=60)
        if out:
            app_log("Desktop shortcut: " + out)
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
                    S.notice = tr("Достигнут дневной лимит ({n} статей). Остальные скачаются "
                                  "завтра или после увеличения лимита в настройках.", n=S.day_count)
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
            status, message = "pending", tr("остановлено")
            item["attempts"] -= 1
            S.day_count = max(0, S.day_count - 1)
        elif timed_out:
            status, message = "failed", tr("не уложилось в {m} мин", m=limit // 60)
        elif not result["status"]:
            message = next((l for l in reversed(log) if l.strip()), tr("код {code}", code=code))

        if status == "captcha" and item["attempts"] < 2:
            status = "pending"
            message = tr("была капча — повторю в конце очереди")

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
                writer.writerow([tr(x) for x in ("Время", "Запрос", "Результат", "Файл", "Сообщение")])
            writer.writerow([item.get("finished", ""), item["query"],
                             tr(REPORT_STATUS.get(item["status"], item["status"])),
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


# v6.1: inbox\\ is the library of lists the user picks from; files there are
# no longer queued by themselves.

def _list_entries(path: Path) -> list[str]:
    stat = path.stat()
    cached = S.list_cache.get(str(path))
    if cached and cached[:2] == (stat.st_mtime, stat.st_size):
        return cached[2]
    try:
        text = file_to_text(path.name, path.read_bytes())
        entries, _ = parse_entries(text, bool(S.settings.get("title_for_plain_lines")))
    except Exception as exc:
        app_log(f"List not read {path}: {exc}")
        entries = []
    S.list_cache[str(path)] = (stat.st_mtime, stat.st_size, entries)
    return entries


def inbox_lists() -> list[dict]:
    INBOX.mkdir(parents=True, exist_ok=True)
    with S.lock:
        done = {i["query"].lower() for i in S.items if i["status"] == "success"}
        queued = {i["query"].lower() for i in S.items if i["status"] in ("pending", "running")}
    files = [p for p in INBOX.iterdir() if p.is_file() and p.suffix.lower() in LIST_SUFFIXES]
    out = []
    for path in sorted(files, key=lambda p: p.stat().st_mtime, reverse=True):
        entries = _list_entries(path)
        keys = [e.lower() for e in entries]
        out.append({
            "name": path.name,
            "modified": time.strftime("%d.%m.%Y %H:%M", time.localtime(path.stat().st_mtime)),
            "count": len(entries),
            "done": sum(k in done for k in keys),
            "queued": sum(k in queued for k in keys),
        })
    return out


def queue_list_file(path: Path) -> dict:
    """Queue every entry of a list file and start (databases → CAPTCHA → queue)."""
    text = file_to_text(path.name, path.read_bytes())
    entries, ignored = parse_entries(text, bool(S.settings.get("title_for_plain_lines")))
    added, duplicates = S.add_entries(entries)
    with S.lock:
        S.last_list = path.name
    app_log(f"List {path.name}: {added} queued, {duplicates} already there")
    if added or S.next_pending() is not None:
        start_queue()
    return {"name": path.name, "added": added, "duplicates": duplicates,
            "ignored": ignored, "total": len(entries)}


def choose_file_dialog() -> str:
    """The Windows "Open" dialog in inbox\\; the chosen path or ""."""
    if os.name != "nt":
        return ""
    INBOX.mkdir(parents=True, exist_ok=True)
    q = ps_quote
    script = f"""
Add-Type -AssemblyName System.Windows.Forms
$owner = New-Object System.Windows.Forms.Form -Property @{{TopMost = $true; ShowInTaskbar = $false}}
$d = New-Object System.Windows.Forms.OpenFileDialog
$d.Title = {q(tr('Выберите файл со списком DOI'))}
$d.InitialDirectory = {q(INBOX)}
$d.Filter = {q(tr('Списки DOI|*.txt;*.csv;*.tsv;*.xlsx;*.docx;*.ris;*.bib;*.enw;*.nbib|Все файлы|*.*'))}
if ($d.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) {{ Write-Output $d.FileName }}
$owner.Dispose()
"""
    try:
        out = run_powershell(script, timeout=1800)
    except Exception as exc:
        app_log(f"File dialog failed: {exc}")
        return ""
    return out.splitlines()[-1].strip() if out else ""


def downloads_dir() -> Path:
    override = os.environ.get("ARTICLE_DOWNLOADS_DIR")
    return Path(override) if override else Path.home() / "Downloads"


def recent_exports() -> list[dict]:
    """Export files that appeared in Downloads since the browser search began."""
    since = S.export_since
    if not since:
        return []
    found = []
    try:
        for path in downloads_dir().iterdir():
            if (path.is_file() and path.suffix.lower() in EXPORT_SUFFIXES
                    and path.stat().st_mtime >= since - 5):
                found.append(path)
    except OSError:
        return []
    found.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return [{"name": p.name, "size": p.stat().st_size,
             "modified": time.strftime("%H:%M:%S", time.localtime(p.stat().st_mtime))}
            for p in found[:5]]


def _set_search_result(records: list[dict], summary: dict):
    with S.lock:
        S.search_records = records
        S.search.update(running=False, error="", summary=summary,
                        version=S.search["version"] + 1)


def start_search(params: dict, sources: list[str]):
    with S.lock:
        if S.search["running"]:
            return
        S.search.update(running=True, error="", progress={s: {"done": 0, "of": 0} for s in sources})
    keys = ds.load_keys()

    def progress(source, done, of):
        with S.lock:
            S.search["progress"][source] = {"done": done, "of": of}

    def run():
        try:
            result = ds.run_search(params, sources, keys, progress)
            _set_search_result(result["records"], {
                "from": "api", "per_source": result["per_source"],
                "no_doi": result["no_doi"], "count": len(result["records"]),
                "queries": result["queries"], "slug": ds.query_slug(params),
            })
        except ds.SearchError as exc:
            with S.lock:
                S.search.update(running=False, error=exc.message)
        except Exception as exc:
            app_log(f"Search error: {exc}\n{traceback.format_exc()}")
            with S.lock:
                S.search.update(running=False, error=tr("Поиск не удался: {error}", error=exc))

    threading.Thread(target=run, daemon=True).start()


def import_export(name: str, data: bytes) -> dict:
    records, no_doi = ds.parse_export(name, data)
    slug = re.sub(r"[^\w\-]+", "_", Path(name).stem).strip("_") or tr("экспорт")
    summary = {"from": "export", "file": name, "count": len(records),
               "no_doi": no_doi, "slug": slug}
    _set_search_result(records, summary)
    return summary


def clear_search():
    """A new topic: forget the found records and the old export files."""
    with S.lock:
        S.search_records = []
        S.search.update(error="", summary=None, progress={}, version=S.search["version"] + 1)
        if S.export_since:
            S.export_since = time.time()


def reset_all():
    """«Очистить всё»: stop everything, then empty the queue and the search."""
    stop_queue()
    with S.lock:
        warm = S.warm_proc
    kill_job_tree(warm)
    deadline = time.time() + 15
    while time.time() < deadline:
        with S.lock:
            if not S.running and not S.warm["running"]:
                break
        time.sleep(0.2)
    with S.lock:
        S.items = [i for i in S.items if i["status"] == "running"]
        S.notice = ""
        if S.phase == "limit":
            S.phase = "idle"
        S.touch_items()
    clear_search()
    app_log("Everything cleared")


def archive_list(name: str) -> str:
    """Move a list (and its _подробно table) to inbox\\_архив\\; nothing is deleted."""
    source = INBOX / Path(name).name
    if not source.is_file():
        return ""
    archive = INBOX / "_архив"
    archive.mkdir(parents=True, exist_ok=True)
    target = archive / source.name
    if target.exists():
        target = archive / f"{source.stem}_{time.strftime('%Y%m%d-%H%M%S')}{source.suffix}"
    source.replace(target)
    for extra, suffix in ((INBOX / "_подробно" / (source.stem + ".csv"), "_подробно.csv"),
                          (rv.meta_path(INBOX, source.name), "_подробно.meta.json"),
                          (rv.review_path(INBOX, source.name), "_отбор.json")):
        if extra.is_file():
            extra.replace(archive / (target.stem + suffix))
    with S.lock:
        S.reviews.pop(source.name, None)
    app_log(f"List archived: {source.name} -> {target.name}")
    return target.name


def load_donate() -> dict:
    try:
        data = json.loads(DONATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    data["crypto"] = [c for c in data.get("crypto") or [] if c.get("address")]
    if not (data.get("sbp") or {}).get("url"):
        data.pop("sbp", None)
    return data if data.get("crypto") or data.get("sbp") else {}


def qr_svg(text: str) -> str:
    """QR code of the text itself, drawn here, so it always matches the text."""
    try:
        import qrcode
        import qrcode.image.svg
        image = qrcode.make(text, image_factory=qrcode.image.svg.SvgPathImage,
                            border=2, box_size=10)
        return image.to_string(encoding="unicode")
    except Exception as exc:
        app_log(f"QR not drawn: {exc}")
        return ""


def donate_info() -> dict:
    data = load_donate()
    # donate.json may carry English texts under "en" (message, plans, sbp title / note).
    english = data.pop("en", None) or {}
    if i18n.LANG == "en":
        for key, value in english.items():
            if isinstance(value, dict) and isinstance(data.get(key), dict):
                data[key] = data[key] | value
            else:
                data[key] = value
    if data.get("sbp"):
        data["sbp"]["qr"] = qr_svg(data["sbp"]["url"])
    for item in data.get("crypto", []):
        item["qr"] = qr_svg(item["address"])
    return data


# ---------------------------------------------------------------------------
# literature review: screening, reference list, PRISMA (review.py)
# ---------------------------------------------------------------------------

def list_dois(path: Path) -> list[str]:
    seen, out = set(), []
    for entry in _list_entries(path):
        doi = ds.normalize_doi(entry)
        if doi and doi not in seen:
            seen.add(doi)
            out.append(doi)
    return out


def get_review(list_name: str) -> dict:
    name = Path(str(list_name or "")).name
    path = INBOX / name
    with S.lock:
        data = S.reviews.get(name)
        if data is None:
            if not name or not path.is_file():
                raise FileNotFoundError(name)
            data = rv.load_review(INBOX, name, list_dois(path))
            S.reviews[name] = data
        return data


def start_lookup(list_name: str):
    """Fetch abstracts and bibliographic data for the DOIs that have none yet."""
    data = get_review(list_name)
    name = data["list"]
    with S.lock:
        job = S.review_jobs.get(name)
        if job and job["running"]:
            return
        todo = [d for d in data["order"] if data["records"][d].get("found") is None]
        job = {"running": bool(todo), "done": len(data["order"]) - len(todo),
               "total": len(data["order"]), "error": ""}
        S.review_jobs[name] = job
    if not todo:
        return

    def run():
        keys = ds.load_keys()
        try:
            for chunk in rv._chunks(todo, 50):
                meta, error = rv.enrich(chunk, keys)
                with S.lock:
                    for doi, found in meta.items():
                        # A service that did not answer is asked again next time.
                        if found["found"] or not error:
                            data["records"][doi].update(found)
                    job["done"] += len(chunk)
                    job["error"] = error
                    rv.save_review(INBOX, data)
        except Exception as exc:
            app_log(f"Lookup error: {exc}\n{traceback.format_exc()}")
            with S.lock:
                job["error"] = tr("Данные о статьях не загрузились: {error}", error=exc)
        finally:
            with S.lock:
                job["running"] = False

    threading.Thread(target=run, daemon=True).start()


def ai_service(keys: dict | None = None) -> tuple[str, dict]:
    """The AI helper's service and the keys: the chosen one, else the first with a key; "" without keys."""
    keys = keys if keys is not None else ds.load_keys()
    chosen = S.settings.get("ai_provider") or ""
    if chosen in ai.PROVIDERS and keys.get(chosen):
        return chosen, keys
    return next((p for p in ai.PROVIDERS if keys.get(p)), ""), keys


def set_ai_service(provider: str):
    with S.lock:
        S.settings["ai_provider"] = provider
        write_json_atomic(SETTINGS_PATH, S.settings)


def start_ai_screening(list_name: str, topic: str) -> dict:
    """Ask the AI about every undecided article with an abstract that has no advice yet."""
    data = get_review(list_name)
    name = data["list"]
    provider, keys = ai_service()
    if not provider:
        return {"error": tr("ИИ-помощник не подключён: вставьте ключ (кнопка «🤖 ИИ-помощник» на главной).")}
    topic = re.sub(r"\s+", " ", str(topic or "")).strip()
    if len(topic) < 3:
        return {"error": tr("Напишите, что вы ищете: тема и что должно быть в статье.")}
    with S.lock:
        job = S.ai_jobs.get(name)
        if job and job["running"]:
            return {}
        todo = [d for d in data["order"]
                if not data["records"][d].get("decision") and not data["records"][d].get("ai")
                and data["records"][d].get("found") is not None]
        job = {"running": bool(todo), "done": 0, "total": len(todo), "error": "", "cost": 0.0, "stop": False}
        S.ai_jobs[name] = job
        meta = rv.load_meta(INBOX, name)
        meta["ai_topic"] = topic[:1500]
        rv.save_meta(INBOX, name, meta)
    if not todo:
        return {"nothing": True}
    lang = i18n.LANG

    def run():
        app_log(f"AI screening: {len(todo)} articles of {name}")
        try:
            for chunk in rv._chunks(todo, ai.SCREEN_BATCH):
                with S.lock:
                    if job["stop"]:
                        break
                    batch = [{"doi": d, "title": data["records"][d].get("title", ""),
                              "abstract": data["records"][d].get("abstract", "")} for d in chunk]
                advice, cost = ai.screen(provider, keys, batch, topic, lang)
                with S.lock:
                    for doi, item in advice.items():
                        data["records"][doi]["ai"] = item
                    job["done"] += len(chunk)
                    job["cost"] += cost
                    rv.save_review(INBOX, data)
        except ai.AIError as exc:
            with S.lock:
                job["error"] = exc.message
        except Exception as exc:
            app_log(f"AI screening error: {exc}\n{traceback.format_exc()}")
            with S.lock:
                job["error"] = tr("ИИ-предотбор прервался: {error}", error=exc)
        finally:
            with S.lock:
                job["running"] = False
            app_log(f"AI screening finished: {job['done']} of {job['total']}, cost ${job['cost']:.4f}")

    threading.Thread(target=run, daemon=True).start()
    return {}


def accept_ai_advice(list_name: str) -> dict:
    """Turn the AI advice into decisions for the articles not decided yet."""
    data = get_review(list_name)
    n = 0
    with S.lock:
        for doi in data["order"]:
            rec = data["records"][doi]
            advice = rec.get("ai") or {}
            if rec.get("decision") or advice.get("verdict") not in rv.DECISIONS:
                continue
            rec["decision"] = advice["verdict"]
            rec["reason"] = (tr("ИИ: ") + advice.get("why", ""))[:120] if advice["verdict"] == "exclude" else ""
            n += 1
        if n:
            rv.save_review(INBOX, data)
    return {"accepted": n}


def review_prisma(name: str) -> dict:
    """PRISMA numbers; a list of selected articles counts from its parent list."""
    meta = rv.load_meta(INBOX, name)
    parent = meta.get("parent")
    if parent and (INBOX / parent).is_file():
        name, meta = parent, rv.load_meta(INBOX, parent)
    data = get_review(name)
    with S.lock:
        downloaded = {i["query"].lower() for i in S.items if i["status"] == "success"}
        return rv.prisma(data, meta, downloaded) | {"list": name}


def review_state(name: str) -> dict:
    data = get_review(name)
    meta = rv.load_meta(INBOX, data["list"])
    prisma = review_prisma(data["list"])
    with S.lock:
        return {
            "list": data["list"],
            "records": [data["records"][d] for d in data["order"]],
            "job": dict(S.review_jobs.get(data["list"]) or {}),
            "ai_job": dict(S.ai_jobs.get(data["list"]) or {}),
            "prisma": prisma,
            "keywords": meta.get("keywords") or [],
            "ai_topic": meta.get("ai_topic") or "",
        }


def save_selected(name: str, with_maybe: bool) -> dict:
    data = get_review(name)
    keep = ("include", "maybe") if with_maybe else ("include",)
    with S.lock:
        dois = [d for d in data["order"] if data["records"][d].get("decision") in keep]
        records = [{"doi": d, "title": data["records"][d].get("title", ""),
                    "year": data["records"][d].get("year", ""),
                    "journal": data["records"][d].get("journal", ""), "sources": []} for d in dois]
    if not dois:
        return {"error": tr("Пока ни одна статья не отмечена «✓ Подходит».")}
    stem = Path(name).stem
    saved = ds.save_list(INBOX, f"{stem}_отобранные", records, dated=False)
    # The new list keeps the found data and the decisions, and counts PRISMA
    # from the original list.
    child = rv.load_review(INBOX, saved.name, dois)
    with S.lock:
        for d in dois:
            child["records"][d] = dict(data["records"][d])
    rv.save_review(INBOX, child)
    rv.save_meta(INBOX, saved.name, {"parent": name, "created": time.strftime("%Y-%m-%d %H:%M")})
    app_log(f"Selected list {saved.name}: {len(dois)} DOI from {name}")
    return {"name": saved.name, "count": len(dois)}


BIBLIO_SCOPES = {"included": ("include",), "included_maybe": ("include", "maybe"), "all": None}


def biblio_records(name: str, scope: str) -> list[dict]:
    data = get_review(name)
    keep = BIBLIO_SCOPES.get(scope, ("include",))
    with S.lock:
        return [dict(data["records"][d]) for d in data["order"]
                if keep is None or data["records"][d].get("decision") in keep]


def style_info(style: str) -> dict:
    """{"id", "title", "numeric", "csl": the CSL style dict or None}; unknown styles are GOST."""
    if style.startswith("csl:"):
        found = cs.csl_find(style[4:])
        if found:
            return {"id": style, "title": found["title"], "numeric": found["format"] in ("numeric", "label"),
                    "csl": found}
        style = "gost"
    if style not in cs.STYLES:
        style = "gost"
    title, fmt, _fn, _label = cs.STYLES[style]
    return {"id": style, "title": title, "numeric": fmt == "numeric", "csl": None}


def start_style_job(name: str, info: dict, scope: str) -> dict:
    """Format the references of a list in a journal's style by doi.org, one by one, in the background."""
    data = get_review(name)
    csl = info["csl"]
    keep = BIBLIO_SCOPES.get(scope, ("include",))
    with S.lock:
        job = S.style_jobs.get(data["list"])
        if job and job["running"]:
            return job
        # DOIs doi.org could not format are not asked again in this session (no endless retries).
        failed = S.style_failed.setdefault((data["list"], csl["id"]), set())
        todo = [d for d in data["order"]
                if (keep is None or data["records"][d].get("decision") in keep)
                and not (data["records"][d].get("csl") or {}).get(csl["id"]) and d not in failed]
        if not todo:
            if not (job and job.get("style") == info["id"]):
                job = {"running": False, "done": 0, "total": 0, "failed": len(failed), "error": "", "style": info["id"]}
                S.style_jobs[data["list"]] = job
            return job
        job = {"running": True, "done": 0, "total": len(todo), "failed": 0, "error": "", "style": info["id"]}
        S.style_jobs[data["list"]] = job
    request_style = csl["parent"] or csl["id"]

    def run():
        failures = 0
        for doi in todo:
            try:
                text = cs.csl_format(doi, request_style, info["numeric"])
                with S.lock:
                    data["records"][doi].setdefault("csl", {})[csl["id"]] = text
                failures = 0
            except cs.StyleError as exc:
                failures += 1
                with S.lock:
                    job["failed"] += 1
                    job["error"] = str(exc)
                    failed.add(doi)
                if failures >= 5:          # doi.org is not answering at all: stop asking
                    break
            with S.lock:
                job["done"] += 1
            time.sleep(0.15)
        with S.lock:
            rv.save_review(INBOX, data)
            job["running"] = False
        app_log(f"Journal style {csl['id']}: {job['done']} of {job['total']}, failed {job['failed']}")

    threading.Thread(target=run, daemon=True).start()
    return job


def biblio_items(name: str, scope: str, alphabetical: bool, style: str) -> dict:
    records = biblio_records(name, scope)
    info = style_info(style)
    out = {"count": len(records), "missing": sum(1 for r in records if not r.get("found")),
           "style": info["id"], "title": info["title"], "numeric": info["numeric"]}
    if info["csl"]:
        job = start_style_job(name, info, scope)
        records = biblio_records(name, scope)          # with the texts formatted so far
        items = cs.csl_entries(records, info["csl"], alphabetical)
        with S.lock:
            out["job"] = dict(job)
        out["fallback"] = sum(1 for i in items if i.get("fallback"))
    else:
        items = cs.entries(records, info["id"], alphabetical)
    out["items"] = items
    return out


def save_biblio(name: str, scope: str, alphabetical: bool, fmt: str, style: str = "gost") -> Path:
    records = biblio_records(name, scope)
    BIBLIO_DIR.mkdir(parents=True, exist_ok=True)
    suffix = {"included": "", "included_maybe": "_с_вопросами", "all": "_все"}.get(scope, "")
    stem = Path(name).stem + suffix
    if fmt == "ris":
        path = BIBLIO_DIR / f"{stem}.ris"
        path.write_text(rv.to_ris(records), encoding="utf-8")
    elif fmt == "bib":
        path = BIBLIO_DIR / f"{stem}.bib"
        path.write_text(rv.to_bibtex(records), encoding="utf-8")
    else:
        data = biblio_items(name, scope, alphabetical, style)
        label = "ГОСТ" if data["style"] == "gost" else re.sub(r'[\\/:*?"<>|]+', " ", data["title"]).strip()[:60]
        if fmt == "docx":
            path = BIBLIO_DIR / f"{stem} ({label}).docx"
            cs.to_docx(path, tr("Список литературы") + f" — {data['title']}", data["items"])
        else:
            path = BIBLIO_DIR / f"{stem} ({label}).txt"
            text = "\r\n".join(i["label"] + i["text"] for i in data["items"]) + "\r\n"
            path.write_bytes(b"\xef\xbb\xbf" + text.encode("utf-8"))
    app_log(f"Reference list saved: {path.name}")
    return path


def search_meta(body: dict, saved: list[dict]) -> dict:
    """What the PRISMA scheme needs to know about where a saved list came from."""
    with S.lock:
        summary = dict(S.search.get("summary") or {})
        unique = len(S.search_records)
    meta = {"created": time.strftime("%Y-%m-%d %H:%M"),
            "keywords": ds._terms(str(body.get("keywords") or "")),
            "unique": unique, "saved": len(saved), "deselected": max(0, unique - len(saved))}
    if summary.get("from") == "api":
        ok = {k: v for k, v in (summary.get("per_source") or {}).items() if v.get("ok")}
        meta["per_source"] = {ds.SOURCE_TITLES.get(k, k): v["taken"] for k, v in ok.items()}
        meta["no_doi"] = int(summary.get("no_doi") or 0)
        meta["duplicates"] = max(0, sum(v["with_doi"] for v in ok.values()) - unique)
    elif summary.get("from") == "export":
        meta["per_source"] = {tr("Экспорт с сайта"): int(summary.get("count") or 0) + int(summary.get("no_doi") or 0)}
        meta["no_doi"] = int(summary.get("no_doi") or 0)
    return meta


def browser_search_url(source: str, query: str) -> str:
    if source == "scopus":
        return SCOPUS_RESULTS_URL + quote(query, safe="")
    return WOS_ADVANCED_URL


def idle_exit_loop():
    while True:
        time.sleep(30)
        with S.lock:
            busy = S.running or S.warm["running"] or S.search["running"]
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
# v7.2: new version check and problem reports (support.py)
# ---------------------------------------------------------------------------

def share_url(to: str, text: str) -> str:
    """Link that opens the chosen messenger (or the mail program) with the message."""
    link, message = quote(DOWNLOAD_URL, safe=""), quote(f"{text} {DOWNLOAD_URL}".strip(), safe="")
    return {
        "telegram": f"https://t.me/share/url?url={link}&text={quote(text, safe='')}",
        "whatsapp": f"https://wa.me/?text={message}",
        "vk": f"https://vk.com/share.php?url={link}&title={quote(tr('Загрузчик статей'), safe='')}",
        "email": f"mailto:?subject={quote(tr('Загрузчик статей — бесплатная программа'), safe='')}&body={message}",
    }.get(to, "")


def check_for_update() -> dict:
    result = sp.check_update(APP_VERSION, UPDATE_URL)
    with S.lock:
        S.update = result
    if result.get("newer"):
        app_log(f"New version available: {result['latest']} (running {APP_VERSION})")
    elif result.get("error"):
        app_log(f"Update check failed: {result['error']}")
    return result


def start_update() -> dict:
    """Download the new version, check it, hand it to the updater and close."""
    with S.lock:
        up, job = S.update or {}, S.update_job
        if job["state"] in ("downloading", "restarting"):
            return {"state": job["state"]}
        if not up.get("newer"):
            return {"error": tr("Новой версии нет — у вас последняя.")}
        job.update(state="downloading", done=0, total=0, error="")

    def progress(done, total):
        with S.lock:
            S.update_job.update(done=done, total=total)

    def run():
        try:
            import tempfile
            dest = Path(tempfile.gettempdir()) / "ArticleDownloader_update.zip"
            app_log(f"Update to {up.get('latest')}: downloading")
            sp.download_update(up.get("zip") or UPDATE_ZIP_URL, dest, up.get("sha256", ""), progress)
            if os.name != "nt" or os.environ.get("ARTICLE_UPDATE_DRY_RUN") == "1":
                app_log("Update downloaded and checked (no installation here)")
                with S.lock:
                    S.update_job.update(state="downloaded")
                return
            sp.start_updater(dest, ROOT, os.getpid())
            app_log("Updater started; closing for the update")
            with S.lock:
                S.update_job.update(state="restarting")
            time.sleep(1.5)       # the window gets the news before the program goes
            shutdown()
        except Exception as exc:
            app_log(f"Update failed: {exc}")
            with S.lock:
                S.update_job.update(state="error", error=str(exc))

    threading.Thread(target=run, daemon=True).start()
    return {"state": "downloading"}


def problem_report(body: dict) -> dict:
    section = str(body.get("section") or "")[:60]
    logs = [APP_LOG, LOGS / "article_downloader.log"] if body.get("attach_log", True) else []
    report = sp.build_report(APP_VERSION, section, str(body.get("text") or "")[:4000],
                             browser=S.browser.get("title") or "", logs=logs)
    action = body.get("action")
    if action == "github":
        url = sp.issue_url(ISSUES_URL, report)
        app_log("Problem report: GitHub issue page opened")
        if os.environ.get("ARTICLE_UI_NO_WINDOW") != "1":
            webbrowser.open(url)
        return {"url": url}
    text = sp.report_text(report)
    if action == "save":
        path = sp.save_report(LOGS, text)
        app_log(f"Problem report saved: {path.name}")
        if os.environ.get("ARTICLE_UI_NO_WINDOW") != "1":
            if os.name == "nt":
                sp.show_in_folder(path)
            else:
                open_path(LOGS)
        return {"file": str(path)}
    return {"report": text}


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
                    S.notice = tr("Браузер не открылся: {error}", error=line.split("|", 2)[2].strip())
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
            "last_list": S.last_list,
            "search": {k: v for k, v in S.search.items()},
            "key_checks": dict(S.key_checks),
            "exports": recent_exports(),
            "native_dialog": os.name == "nt",
            "donate": bool(load_donate()),
            "version": APP_VERSION,
            "update": S.update,
            "update_job": dict(S.update_job),
        }
        keys = ds.load_keys()
        data["keys"] = ds.keys_status(keys)
        provider, _ = ai_service(keys)
        data["ai"] = {"provider": provider, "title": ai.PROVIDERS.get(provider, "")}
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
        elif what == "biblio_dir":
            BIBLIO_DIR.mkdir(parents=True, exist_ok=True)
            open_path(BIBLIO_DIR)
        elif what == "license":
            licence = next((p for p in LICENSE_FILES if p.exists()), None)
            if licence:
                open_path(licence)
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

    # -- lists in inbox\\ ----------------------------------------------------
    if path == "/api/choose_file":
        chosen = choose_file_dialog()
        if not chosen:
            return {"chosen": False}
        return {"chosen": True, **queue_list_file(Path(chosen))}

    if path == "/api/queue_file":
        name = Path(str(body.get("name") or "")).name
        target = INBOX / name
        if not name or not target.is_file():
            return {"error": tr("Файл не найден в папке inbox.")}
        return queue_list_file(target)

    # -- literature review ----------------------------------------------------
    if path == "/api/review_open":
        try:
            start_lookup(body.get("list"))
        except FileNotFoundError:
            return {"error": tr("Список не найден в папке inbox.")}
        return {}

    if path == "/api/review_decide":
        try:
            data = get_review(body.get("list"))
        except FileNotFoundError:
            return {"error": tr("Список не найден в папке inbox.")}
        decision = body.get("decision") or ""
        if decision not in rv.DECISIONS + ("",):
            return {"error": tr("Неизвестное решение.")}
        with S.lock:
            rec = data["records"].get(ds.normalize_doi(body.get("doi")))
            if rec is None:
                return {"error": tr("Статьи нет в списке.")}
            rec["decision"] = decision
            rec["reason"] = str(body.get("reason") or "")[:120] if decision == "exclude" else ""
            rv.save_review(INBOX, data)
        return {}

    if path == "/api/ai_screen":
        try:
            return start_ai_screening(body.get("list"), body.get("topic"))
        except FileNotFoundError:
            return {"error": tr("Список не найден в папке inbox.")}

    if path == "/api/ai_screen_stop":
        with S.lock:
            job = S.ai_jobs.get(Path(str(body.get("list") or "")).name)
            if job:
                job["stop"] = True
        return {}

    if path == "/api/ai_translate":
        try:
            data = get_review(body.get("list"))
        except FileNotFoundError:
            return {"error": tr("Список не найден в папке inbox.")}
        doi = ds.normalize_doi(body.get("doi"))
        with S.lock:
            rec = data["records"].get(doi)
            if rec is None:
                return {"error": tr("Статьи нет в списке.")}
            cached = rec.get("tr") or {}
            title, abstract = rec.get("title", ""), rec.get("abstract", "")
        if cached.get("lang") == i18n.LANG and cached.get("abstract"):
            return cached
        provider, keys = ai_service()
        try:
            result = ai.translate(provider, keys, title, abstract, i18n.LANG)
        except ai.AIError as exc:
            return {"error": exc.message, "kind": exc.kind}
        with S.lock:
            rec["tr"] = {"lang": i18n.LANG, "title": result["title"], "abstract": result["abstract"]}
            rv.save_review(INBOX, data)
        return rec["tr"] | {"cost": result["cost"]}

    if path == "/api/ai_accept":
        try:
            return accept_ai_advice(body.get("list"))
        except FileNotFoundError:
            return {"error": tr("Список не найден в папке inbox.")}

    if path == "/api/ai_search":
        try:
            provider, keys = ai_service()
            return ai.suggest_search(provider, keys, body.get("topic") or "", i18n.LANG)
        except ai.AIError as exc:
            return {"error": exc.message, "kind": exc.kind}

    if path == "/api/ai_check":
        provider = body.get("provider") or "perplexity"
        if provider not in ai.PROVIDERS:
            return {"error": tr("Неизвестный ИИ-помощник.")}
        typed = {f: str(body[f]).strip() for f in ai.KEY_FIELDS[provider] if str(body.get(f) or "").strip()}
        keys = ds.load_keys() | typed
        result = ai.check_key(provider, keys) | {"at": time.strftime("%H:%M")}
        if result["ok"]:
            if typed:
                ds.save_keys(typed)
            set_ai_service(provider)
        app_log(f"AI key check ({provider}): {result['kind']}")
        with S.lock:
            S.key_checks[provider] = result
        return result

    if path == "/api/ai_use":
        provider = body.get("provider")
        if provider not in ai.PROVIDERS or not ds.load_keys().get(provider):
            return {"error": tr("Сначала вставьте и проверьте ключ этого сервиса.")}
        set_ai_service(provider)
        return {"provider": provider}

    if path == "/api/review_save_selected":
        try:
            return save_selected(body.get("list"), bool(body.get("with_maybe")))
        except FileNotFoundError:
            return {"error": tr("Список не найден в папке inbox.")}

    if path == "/api/biblio":
        try:
            records = biblio_records(body.get("list"), body.get("scope") or "included")
        except FileNotFoundError:
            return {"error": tr("Список не найден в папке inbox.")}
        return biblio_items(body.get("list"), body.get("scope") or "included", body.get("order") != "list",
                            str(body.get("style") or "gost"))

    if path == "/api/csl_search":
        return {"styles": cs.csl_search(str(body.get("q") or ""))}

    if path == "/api/biblio_save":
        try:
            saved = save_biblio(body.get("list"), body.get("scope") or "included",
                                body.get("order") != "list", body.get("format") or "txt",
                                str(body.get("style") or "gost"))
        except FileNotFoundError:
            return {"error": tr("Список не найден в папке inbox.")}
        return {"name": saved.name, "folder": str(BIBLIO_DIR)}

    if path == "/api/open_doi":
        doi = ds.normalize_doi(body.get("doi"))
        if doi:
            url = "https://doi.org/" + quote(doi, safe="/:;()[]<>")
            if os.environ.get("ARTICLE_UI_NO_WINDOW") != "1":
                webbrowser.open(url)
            return {"url": url}
        return {"error": tr("Это не DOI.")}

    if path == "/api/archive_list":
        moved = archive_list(str(body.get("name") or ""))
        return {"archived": moved} if moved else {"error": tr("Файл не найден в папке inbox.")}

    if path == "/api/open_inbox":
        INBOX.mkdir(parents=True, exist_ok=True)
        open_path(INBOX)
        return {}

    # -- DOI search ------------------------------------------------------------
    if path == "/api/build_queries":
        try:
            queries = ds.build_queries(body)
        except ds.SearchError as exc:
            return {"error": exc.message}
        return {"scopus": queries["scopus"], "wos": queries["wos"]}

    if path == "/api/search":
        sources = [x for x in body.get("sources") or [] if x in ds.SOURCE_TITLES]
        if not sources:
            return {"error": tr("Отметьте хотя бы один источник.")}
        try:
            ds.build_queries(body)
        except ds.SearchError as exc:
            return {"error": exc.message}
        start_search(body, sources)
        return {}

    if path == "/api/search_import":
        if body.get("file_b64"):
            name = body.get("file_name") or "export.txt"
            data = base64.b64decode(body["file_b64"])
        else:
            name = Path(str(body.get("name") or "")).name
            source = downloads_dir() / name
            if not name or not source.is_file() or source.suffix.lower() not in EXPORT_SUFFIXES:
                return {"error": tr("Файл экспорта не найден.")}
            data = source.read_bytes()
        try:
            return import_export(name, data)
        except ds.SearchError as exc:
            return {"error": exc.message}

    if path == "/api/suggest_keywords":
        try:
            return ds.suggest_keywords(body.get("keywords") or "")
        except ds.SearchError as exc:
            return {"error": exc.message}

    if path == "/api/open_donate_link":
        url = (load_donate().get("sbp") or {}).get("url", "")
        if url.startswith("https://"):
            app_log("Donate link opened")
            if os.environ.get("ARTICLE_UI_NO_WINDOW") != "1":
                webbrowser.open(url)
        return {"url": url}

    if path == "/api/reset_all":
        reset_all()
        return {}

    if path == "/api/search_clear":
        clear_search()
        return {}

    if path == "/api/search_save":
        wanted = [d.lower() for d in body.get("dois") or []]
        with S.lock:
            by_doi = {r["doi"]: r for r in S.search_records}
        records = [by_doi[d] for d in wanted if d in by_doi]
        if not records:
            return {"error": tr("Не выбрано ни одной статьи.")}
        saved = ds.save_list(INBOX, str(body.get("slug") or tr("список")), records)
        rv.save_meta(INBOX, saved.name, search_meta(body, records))
        app_log(f"Saved list {saved.name}: {len(records)} DOI")
        return {"name": saved.name, "count": len(records)}

    if path == "/api/browser_search":
        source = body.get("source")
        if source not in ("scopus", "wos"):
            return {"error": tr("Неизвестный источник.")}
        url = browser_search_url(source, str(body.get("query") or ""))
        with S.lock:
            S.export_since = time.time()
        open_in_dedicated_browser([url])
        return {"url": url}

    if path == "/api/export_watch":
        with S.lock:
            S.export_since = time.time() if body.get("on") else 0.0
        return {}

    # -- API keys --------------------------------------------------------------
    if path == "/api/keys_save":
        ds.save_keys({k: body[k] for k in ds.KEY_FIELDS if k in body})
        with S.lock:
            for source in ("scopus", "wos", "openalex", *ai.PROVIDERS):
                if source in body or (source == "scopus" and "scopus_insttoken" in body):
                    S.key_checks.pop(source, None)
        return {"keys": ds.keys_status(ds.load_keys())}

    if path == "/api/keys_check":
        source = body.get("source")
        if source not in ds.SOURCE_TITLES:
            return {"error": tr("Неизвестный источник.")}
        keys = ds.load_keys()
        for field in ds.KEY_FIELDS:
            if str(body.get(field) or "").strip():
                keys[field] = str(body[field]).strip()
        result = ds.check_key(source, keys) | {"at": time.strftime("%H:%M")}
        with S.lock:
            S.key_checks[source] = result
        return result

    if path == "/api/report":
        return problem_report(body)

    if path == "/api/set_lang":
        lang = i18n.set_lang(str(body.get("lang") or ""))
        with S.lock:
            S.settings["lang"] = lang
            write_json_atomic(SETTINGS_PATH, S.settings)
        app_log(f"Interface language: {lang}")
        return {"lang": lang}

    if path == "/api/share":
        url = share_url(str(body.get("to") or ""), str(body.get("text") or "")[:600])
        if url and os.environ.get("ARTICLE_UI_NO_WINDOW") != "1":
            webbrowser.open(url)
        return {"url": url}

    if path == "/api/check_update":
        return check_for_update()

    if path == "/api/update_now":
        return start_update()

    if path == "/api/open_link":
        url = GUIDE_LINKS.get(body.get("key"))
        if url:
            app_log(f"Guide link: {url}")
            if os.environ.get("ARTICLE_UI_NO_WINDOW") != "1":
                webbrowser.open(url)
        return {"url": url or ""}

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
            page = (i18n.translate_page(UI_FILE.read_text(encoding="utf-8")).replace("__TOKEN__", S.token)
                    .replace("__APP_VERSION__", APP_VERSION).replace("__APP_AUTHOR__", APP_AUTHOR))
            return self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")
        if url.path == "/favicon.ico" and ICON_FILE.exists():
            return self._send(200, ICON_FILE.read_bytes(), "image/x-icon")
        if url.path == "/api/ping":
            return self._json({"app": "ArticleDownloader", "version": APP_VERSION, "root": str(ROOT)})
        if url.path == "/img/avatar.jpg" and AVATAR_FILE.exists():
            return self._send(200, AVATAR_FILE.read_bytes(), "image/jpeg")
        if url.path.startswith("/img/keys/"):
            name = url.path.rsplit("/", 1)[-1]
            image = KEY_IMG_DIR / name
            if re.fullmatch(r"[\w\-]+\.(png|jpg)", name) and image.is_file():
                ctype = "image/png" if name.endswith(".png") else "image/jpeg"
                return self._send(200, image.read_bytes(), ctype)
            return self._send(404, b"not found", "text/plain")
        if not self._token_ok(query):
            return self._send(403, b"forbidden", "text/plain")

        if url.path == "/api/state":
            S.last_poll = time.time()
            try:
                version = int((query.get("v") or ["0"])[0])
            except ValueError:
                version = 0
            return self._json(public_state(version))
        if url.path == "/api/donate":
            return self._json(donate_info())
        if url.path == "/api/share_info":
            return self._json({"url": DOWNLOAD_URL, "qr": qr_svg(DOWNLOAD_URL)})
        if url.path == "/api/review":
            try:
                return self._json(review_state((query.get("list") or [""])[0]))
            except FileNotFoundError:
                return self._json({"error": tr("Список не найден в папке inbox.")})
        if url.path == "/api/inbox":
            return self._json({"files": inbox_lists(), "inbox": str(INBOX)})
        if url.path == "/api/search_results":
            with S.lock:
                return self._json({"version": S.search["version"],
                                   "records": S.search_records})
        if url.path == "/api/log":
            try:
                item_id = int((query.get("id") or ["0"])[0])
            except ValueError:
                item_id = 0
            lines = list(S.job_logs.get(item_id, []))
            text = "\n".join(lines) or tr("Журнала нет: статья ещё не скачивалась после запуска программы.")
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
            ctypes.windll.user32.MessageBoxW(None, text, tr("Загрузчик статей"), 0x10)
            return
        except Exception:
            pass
    print(text, file=sys.stderr)


def running_copy() -> dict | None:
    """What answers on our port: {"app", "version", "root"} (older copies: only "app")."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{UI_PORT}/api/ping", timeout=3) as r:
            info = json.loads(r.read())
        return info if info.get("app") == "ArticleDownloader" else None
    except Exception:
        return None


def ask_to_quit(root: Path) -> bool:
    """Close the copy that runs from root (its token lies in root\\logs)."""
    try:
        token = (root / "logs" / ".ui_token").read_text(encoding="utf-8").strip()
        req = urllib.request.Request(
            f"http://127.0.0.1:{UI_PORT}/api/quit", data=b"{}", method="POST",
            headers={"Content-Type": "application/json", "X-Token": token})
        urllib.request.urlopen(req, timeout=10).read()
        return True
    except Exception as exc:
        app_log(f"Could not close the running copy: {exc}")
        return False


def same_folder(a, b) -> bool:
    return os.path.normcase(os.path.abspath(str(a))) == os.path.normcase(os.path.abspath(str(b)))


def take_over_from_running_copy(files: list[str]):
    """
    The port is busy. The same version from this folder: hand it our files and
    show its window. An older version or a copy from another folder (left
    running when a new version was unpacked): close it and start this one.
    Returns our server, or None when this process should end.
    """
    other = running_copy()
    if other is None:
        message_box(tr("Порт {port} занят другой программой. Закройте её или перезагрузите компьютер.",
                       port=UI_PORT))
        return None
    root = Path(other["root"]) if other.get("root") else ROOT
    older = sp.version_tuple(other.get("version") or "0") < sp.version_tuple(APP_VERSION)
    if same_folder(root, ROOT) and not older:
        if not hand_over_to_running_copy(files):
            message_box(tr("Программа уже запущена, но не отвечает. Перезагрузите компьютер."))
        return None
    app_log(f"Closing the running copy {other.get('version') or '(old)'} from {root}")
    if ask_to_quit(root):
        for _ in range(60):
            time.sleep(0.5)
            try:
                return ThreadingHTTPServer(("127.0.0.1", UI_PORT), Handler)
            except OSError:
                continue
    message_box(tr("Уже работает другая копия программы (старая версия или из другой папки). "
                   "Нажмите в её окне «Выход» и запустите программу снова."))
    return None


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
    i18n.set_lang(S.settings.get("lang") or i18n.detect_lang())
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
        server = take_over_from_running_copy(files)
        if server is None:
            return

    S.server = server
    TOKEN_PATH.write_text(S.token, encoding="utf-8")
    S.load_queue()
    refresh_browser_info()
    app_log(f"Started; browser: {S.browser.get('title') or S.browser.get('error')}")

    if files:
        import_files(files)
        S.last_list = Path(files[0]).name

    url = f"http://127.0.0.1:{UI_PORT}/?t={S.token}"
    open_ui_window(url)

    threading.Thread(target=ensure_desktop_shortcut, daemon=True).start()
    threading.Thread(target=idle_exit_loop, daemon=True).start()
    threading.Thread(target=check_for_update, daemon=True).start()

    # v6.1: the start screen comes first; the databases open once a list is
    # chosen (or "Продолжить" pressed). A file dropped on the icon starts at
    # once, as before.
    if files:
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
        message_box(tr("Программа не запустилась:\n{error}\n\nПодробности: {log}", error=exc, log=APP_LOG))
        app_log(traceback.format_exc())
