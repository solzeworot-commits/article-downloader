# -*- coding: utf-8 -*-
# © 2026 Bucaneiro. Бесплатная программа; условия — в файле ЛИЦЕНЗИЯ.txt.
"""
macOS only (v8.6.1): the browsers in /Applications, the Finder dialog and
alerts through osascript, the Mac's language, the updater and (v8.8) no sleep
for the work computer of «☁ Облако».

Only the Mac archive has this file: the Windows build leaves it out, so the
Windows program carries no Mac shell commands (Windows Defender took the
updater below, which clears the "downloaded from the internet" mark, for a
trojan in 8.6). osutil.py imports it on a Mac only.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# The executable inside each browser's .app bundle.
BROWSERS = {
    "yandex": ("Yandex.app", "Yandex"),
    "edge": ("Microsoft Edge.app", "Microsoft Edge"),
    "chrome": ("Google Chrome.app", "Google Chrome"),
}


def browser(kind: str) -> str | None:
    """The browser's executable in /Applications or ~/Applications, or None."""
    if kind not in BROWSERS:
        return None
    bundle, exe = BROWSERS[kind]
    for apps in (Path("/Applications"), Path.home() / "Applications"):
        candidate = apps / bundle / "Contents" / "MacOS" / exe
        if candidate.exists():
            return str(candidate)
    return None


def applescript_text(text: str) -> str:
    return '"' + str(text).replace("\\", "\\\\").replace('"', '\\"') + '"'


def alert(text: str, title: str):
    subprocess.run(["osascript", "-e", f"display alert {applescript_text(title)} message "
                    f"{applescript_text(text)} as critical"], timeout=600,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def choose_file(prompt: str, folder: Path) -> str:
    """The Finder "choose file" dialog; the POSIX path or "" when cancelled."""
    script = (f"POSIX path of (choose file with prompt {applescript_text(prompt)} "
              f"default location (POSIX file {applescript_text(str(folder))}))")
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=1800)
    return result.stdout.strip() if result.returncode == 0 else ""


_caffeinate: subprocess.Popen | None = None


def keep_awake(on: bool) -> bool:
    """v8.8: no sleep while the program runs (caffeinate ends with it by itself)."""
    global _caffeinate
    if on and _caffeinate is None:
        import os
        _caffeinate = subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    elif not on and _caffeinate is not None:
        _caffeinate.terminate()
        _caffeinate = None
    return True


def language() -> str:
    """"ru", "en", … from the Mac's preferred languages ("" when unknown)."""
    try:
        out = subprocess.run(["defaults", "read", "-g", "AppleLanguages"], capture_output=True,
                             text=True, timeout=5).stdout
    except Exception:
        return ""
    for line in out.splitlines():
        code = line.strip().strip('(),"').strip()
        if code:
            return code.split("-")[0].split("_")[0].lower()
    return ""


# The updater: ditto keeps the executable bits and links of the bundled Python;
# rsync copies over the program and leaves the user's folders (inbox, pdf, …) as they are.
UPDATER_SH = r"""#!/bin/bash
APP_PID="$1"; ZIP="$2"; ROOT="$3"; PY="$4"
LOG="$ROOT/logs/update.log"
log() { echo "[$(date +%Y-%m-%dT%H:%M:%S)] $1" >> "$LOG"; }
log "waiting for the program (pid $APP_PID) to exit"
for i in $(seq 1 90); do kill -0 "$APP_PID" 2>/dev/null || break; sleep 1; done
sleep 1
TMP="$(mktemp -d "${TMPDIR:-/tmp}/ArticleDownloader_update.XXXXXX")"
if /usr/bin/ditto -x -k "$ZIP" "$TMP" && [ -f "$TMP/ArticleDownloader/scripts/app.py" ]; then
    /usr/bin/xattr -dr com.apple.quarantine "$TMP" 2>/dev/null
    if /usr/bin/rsync -a "$TMP/ArticleDownloader/" "$ROOT/"; then log "updated (rsync)"; else log "update failed: rsync"; fi
else
    log "update failed: no ArticleDownloader/scripts/app.py in the archive"
fi
rm -rf "$TMP" "$ZIP"
cd "$ROOT" && nohup "$PY" -E -s "$ROOT/scripts/app.py" >/dev/null 2>&1 &
log "program started again"
"""


def start_updater(zip_path: Path, root: Path, app_pid: int, python: str = "") -> Path:
    script = zip_path.with_name("ArticleDownloader_updater.sh")
    script.write_text(UPDATER_SH, encoding="utf-8")
    subprocess.Popen(["/bin/bash", str(script), str(app_pid), str(zip_path), str(root), python or sys.executable],
                     cwd=str(zip_path.parent), start_new_session=True, close_fds=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return script
