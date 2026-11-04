# -*- coding: utf-8 -*-
# © 2026 Bucaneiro. Бесплатная программа; условия — в файле ЛИЦЕНЗИЯ.txt.
"""
Update check, automatic update and problem reports for the window (v7.2, v7.4).

check_update() reads version.json that the Windows build publishes next to
the zip on the "portable-latest" release page and says whether it is newer
than the running copy. download_update() fetches that zip and checks its
SHA-256; start_updater() hands it to a small PowerShell script that waits
for the program to exit, copies the new files over the program folder
(robocopy; inbox, pdf, logs and settings are not in the zip, so they stay)
and starts the program again. build_report() puts the user's description, the
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
                         "sha256": str(data.get("sha256") or "").lower(),
                         "zip": str(data.get("zip") or ""),
                         "newer": version_tuple(latest) > version_tuple(current)}
    except Exception as exc:  # no internet, GitHub down, release being rebuilt
        return result | {"error": f"{type(exc).__name__}: {exc}"[:200]}


# ---------------------------------------------------------------------------
# automatic update
# ---------------------------------------------------------------------------

class UpdateError(Exception):
    pass


def download_update(url: str, dest: Path, sha256: str = "", progress=None, timeout: float = 30) -> Path:
    """Download the zip to dest (progress(done, total) on the way) and check its SHA-256."""
    import hashlib
    digest = hashlib.sha256()
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response, part.open("wb") as f:
            total, done = int(response.headers.get("Content-Length") or 0), 0
            while True:
                block = response.read(1 << 20)
                if not block:
                    break
                f.write(block)
                digest.update(block)
                done += len(block)
                if progress:
                    progress(done, total)
    except OSError as exc:
        part.unlink(missing_ok=True)
        raise UpdateError(tr("Новая версия не скачалась: {error}", error=exc)) from None
    if sha256 and digest.hexdigest() != sha256.lower():
        part.unlink(missing_ok=True)
        raise UpdateError(tr("Скачанный архив повреждён (контрольная сумма не совпала). Попробуйте ещё раз."))
    os.replace(part, dest)
    return dest


# Plain ASCII on purpose: Windows PowerShell reads a script without a BOM in
# the ANSI code page; the Cyrillic paths come in as parameters.
UPDATER_PS1 = r"""param([int]$AppPid, [string]$Zip, [string]$Root)
$log = Join-Path $Root 'logs\update.log'
function Log($text) { Add-Content -LiteralPath $log -Value ("[{0}] {1}" -f (Get-Date -Format s), $text) -Encoding UTF8 }
try {
    Log "waiting for the program (pid $AppPid) to exit"
    Wait-Process -Id $AppPid -Timeout 90 -ErrorAction SilentlyContinue
    Get-Process python, pythonw -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -and $_.Path.StartsWith((Join-Path $Root 'python'), [StringComparison]::OrdinalIgnoreCase) } |
        Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 1
    $tmp = Join-Path ([IO.Path]::GetTempPath()) ('ArticleDownloader_update_' + [guid]::NewGuid().ToString('N'))
    Expand-Archive -LiteralPath $Zip -DestinationPath $tmp -Force
    $src = Join-Path $tmp 'ArticleDownloader'
    if (-not (Test-Path -LiteralPath (Join-Path $src 'scripts\app.py'))) { throw "no ArticleDownloader\scripts\app.py in the archive" }
    & robocopy $src $Root /E /IS /IT /R:3 /W:1 /NFL /NDL /NJH /NJS /NP | Out-Null
    $code = $LASTEXITCODE
    if ($code -ge 8) { throw "robocopy exit code $code" }
    Log "updated (robocopy $code)"
    Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $Zip -Force -ErrorAction SilentlyContinue
} catch {
    Log ("update failed: " + $_)
}
$pythonw = Join-Path $Root 'python\pythonw.exe'
$app = Join-Path $Root 'scripts\app.py'
Start-Process -FilePath $pythonw -ArgumentList @('-E', '-s', ('"' + $app + '"')) -WorkingDirectory $Root
Log 'program started again'
"""


def start_updater(zip_path: Path, root: Path, app_pid: int) -> Path:
    """Write the updater next to the zip and start it detached from the program."""
    import subprocess
    script = zip_path.with_name("ArticleDownloader_updater.ps1")
    script.write_text(UPDATER_PS1, encoding="ascii")
    args = ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
            "-File", str(script), "-AppPid", str(app_pid), "-Zip", str(zip_path), "-Root", str(root)]
    base = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    breakaway = getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0)
    # Out of the program's job if allowed, so nothing ends the updater with it.
    for flags in ((base | breakaway, base) if breakaway else (base,)):
        try:
            subprocess.Popen(args, cwd=str(zip_path.parent), creationflags=flags, close_fds=True,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return script
        except OSError:
            if flags == base:
                raise
    return script


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
