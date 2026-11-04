# -*- coding: utf-8 -*-
# © 2026 Bucaneiro. Бесплатная программа; условия — в файле ЛИЦЕНЗИЯ.txt.
"""
Update check and problem reports for the window (v7.2).

check_update() reads version.json that the Windows build publishes next to
the zip on the "portable-latest" release page and says whether it is newer
than the running copy. build_report() puts the user's description, the
system and the tail of the logs into one text with keys, tokens, e-mail
addresses and the Windows user name masked; issue_url() turns it into a
GitHub "new issue" link that stays under the length browsers accept.
"""
from __future__ import annotations

import json
import os
import platform
import re
import sys
import time
import urllib.request
from pathlib import Path
from urllib.parse import quote

from i18n import tr

USER_AGENT = "ArticleDownloader (+https://github.com/solzeworot-commits/article-downloader)"
# GitHub refuses longer "new issue" links; Cyrillic takes 6 characters per
# letter once encoded, so the log part is shortened to fit.
MAX_URL = 7500


def version_tuple(text: str) -> tuple[int, ...]:
    parts = re.findall(r"\d+", str(text or ""))
    return tuple(int(p) for p in parts[:4]) or (0,)


def check_update(current: str, url: str, timeout: float = 8) -> dict:
    """{"current", "checked", "latest", "newer", "published"} or {"error"}."""
    result = {"current": current, "checked": time.strftime("%H:%M")}
    try:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8-sig"))
        latest = str(data.get("version") or "").strip()
        if not latest:
            raise ValueError("no version in version.json")
        return result | {"latest": latest, "published": str(data.get("published") or ""),
                         "newer": version_tuple(latest) > version_tuple(current)}
    except Exception as exc:  # no internet, GitHub down, release being rebuilt
        return result | {"error": f"{type(exc).__name__}: {exc}"[:200]}


# ---------------------------------------------------------------------------
# problem report
# ---------------------------------------------------------------------------

_SECRET_RE = re.compile(
    r"(?i)\b(api[_-]?key|apikey|insttoken|x-els-apikey|x-els-insttoken|x-apikey|token|secret|password)"
    r"(\"?\s*[=:]\s*\"?)([^\s\"'&,;]{6,})")
_URL_TOKEN_RE = re.compile(r"([?&](?:t|token|api_key|apikey)=)[^&\s\"']+", re.I)
_HEX_RE = re.compile(r"\b[0-9a-fA-F]{24,}\b")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_USERS_RE = re.compile(r"(?i)([a-z]:[\\/]+(?:users|documents and settings)[\\/]+)[^\\/\s\"']+")


def sanitize(text: str) -> str:
    home = str(Path.home())
    if len(home) > 3:
        text = re.sub(re.escape(home), "%USERPROFILE%", text, flags=re.I)
        text = re.sub(re.escape(home.replace("\\", "/")), "%USERPROFILE%", text, flags=re.I)
    text = _USERS_RE.sub(r"\1<user>", text)
    text = _SECRET_RE.sub(lambda m: m.group(1) + m.group(2) + "•••", text)
    text = _URL_TOKEN_RE.sub(lambda m: m.group(1) + "•••", text)
    text = _HEX_RE.sub("•••", text)
    return _EMAIL_RE.sub("<e-mail>", text)


def log_tail(paths: list[Path], lines: int) -> str:
    out = []
    for path in paths:
        try:
            with path.open("r", encoding="utf-8", errors="replace") as f:
                tail = f.readlines()[-lines:]
        except OSError:
            continue
        if tail:
            out.append(tr("--- {name} (последние {n} строк) ---", name=path.name, n=len(tail)))
            out.extend(line.rstrip("\n") for line in tail)
    return "\n".join(out)


def system_info(version: str, browser: str = "") -> list[str]:
    return [
        tr("Версия программы: {version}", version=version),
        f"Windows: {platform.platform()}",
        f"Python: {sys.version.split()[0]}",
        tr("Браузер загрузчика: {browser}", browser=browser or tr("не найден")),
        tr("Время: {time}", time=time.strftime("%Y-%m-%d %H:%M")),
    ]


def build_report(version: str, section: str, description: str, browser: str = "",
                 logs: list[Path] | None = None, log_lines: int = 200) -> dict:
    """{"title", "head", "log"} — head is the description and system, log the tail."""
    description = (description or "").strip() or tr("(описание не заполнено)")
    head = "\n".join([
        tr("Где: {section}", section=section or tr("не указано")),
        "",
        tr("Что случилось:"),
        description,
        "",
        *system_info(version, browser),
    ])
    log = log_tail(logs or [], log_lines) if logs else ""
    title = tr("Проблема: {section} (версия {version})", section=section or tr("другое"), version=version)
    return {"title": sanitize(title), "head": sanitize(head), "log": sanitize(log)}


def report_text(report: dict) -> str:
    text = report["head"]
    if report["log"]:
        text += "\n\n" + tr("Журнал работы:") + "\n" + report["log"]
    return text + "\n"


def issue_url(base: str, report: dict) -> str:
    """A GitHub "new issue" link; the log is cut from its start until it fits."""
    def build(log_lines: list[str]) -> str:
        body = report["head"]
        if log_lines:
            body += "\n\n" + tr("Журнал работы (конец):") + "\n```\n" + "\n".join(log_lines) + "\n```"
        return f"{base}?title={quote(report['title'])}&body={quote(body)}"

    lines = report["log"].split("\n") if report["log"] else []
    lines = [line[:300] for line in lines]
    url = build(lines)
    while len(url) > MAX_URL and lines:
        lines = lines[max(1, len(lines) // 5):]
        url = build(lines)
    if len(url) > MAX_URL:  # a very long description
        short = dict(report, head=report["head"][:900] + "\n…")
        url = f"{base}?title={quote(short['title'])}&body={quote(short['head'])}"
    return url


def save_report(folder: Path, text: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / time.strftime("Отчёт_о_проблеме_%Y-%m-%d_%H%M%S.txt")
    path.write_bytes(b"\xef\xbb\xbf" + text.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8"))
    return path


def show_in_folder(path: Path):
    if os.name == "nt":
        import subprocess
        subprocess.Popen(["explorer", "/select,", str(path)])
