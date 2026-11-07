# -*- coding: utf-8 -*-
# © 2026 Bucaneiro. Бесплатная программа; условия — в файле ЛИЦЕНЗИЯ.txt.
"""
What differs between Windows and macOS (v8.6, the Mac beta).

- user_data_dir(): where the keys and the downloader's browser profile live,
  outside the program folder — %LOCALAPPDATA%\\ArticleDownloader on Windows,
  ~/Library/Application Support/ArticleDownloader on a Mac (LOCALAPPDATA, when
  set, wins everywhere: the tests and the bench point it at a temporary folder).
- mac_browser(kind): Yandex Browser, Edge or Chrome in /Applications.
- open_path() / reveal(): a file or folder in Explorer / Finder.
- alert(): a message when the window cannot open.
- mac_language(): the first language of the Mac's preferred languages.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

IS_WIN = os.name == "nt"
IS_MAC = sys.platform == "darwin"

# HTTPS from the bundled Python on a Mac: its OpenSSL looks for the root
# certificates where Linux keeps them; macOS has its own bundle in /etc/ssl.
# Set before the first request (app.py imports this module at start; the
# downloader and the updater inherit the variable).
if IS_MAC and not os.environ.get("SSL_CERT_FILE") and os.path.exists("/etc/ssl/cert.pem"):
    os.environ["SSL_CERT_FILE"] = "/etc/ssl/cert.pem"

# The executable inside each browser's .app bundle.
MAC_BROWSERS = {
    "yandex": ("Yandex.app", "Yandex"),
    "edge": ("Microsoft Edge.app", "Microsoft Edge"),
    "chrome": ("Google Chrome.app", "Google Chrome"),
}


def user_data_dir() -> Path | None:
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local) / "ArticleDownloader"
    if IS_MAC:
        return Path.home() / "Library" / "Application Support" / "ArticleDownloader"
    return None


def mac_browser(kind: str) -> str | None:
    """The browser's executable in /Applications or ~/Applications, or None."""
    if kind not in MAC_BROWSERS:
        return None
    bundle, exe = MAC_BROWSERS[kind]
    for apps in (Path("/Applications"), Path.home() / "Applications"):
        candidate = apps / bundle / "Contents" / "MacOS" / exe
        if candidate.exists():
            return str(candidate)
    return None


def _quiet(args: list[str]):
    subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def open_path(path) -> None:
    """A file in its program, a folder in Explorer / Finder."""
    if IS_WIN:
        os.startfile(str(path))
    elif IS_MAC:
        _quiet(["open", str(path)])
    else:
        _quiet(["xdg-open", str(path)])


def reveal(path) -> None:
    """The folder with the file selected."""
    if IS_WIN:
        subprocess.Popen(["explorer", "/select,", str(path)])
    elif IS_MAC:
        _quiet(["open", "-R", str(path)])
    else:
        open_path(Path(path).parent)


def applescript_text(text: str) -> str:
    return '"' + str(text).replace("\\", "\\\\").replace('"', '\\"') + '"'


def alert(text: str, title: str) -> bool:
    """A message box; False when there is none on this system."""
    try:
        if IS_WIN:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, text, title, 0x10)
            return True
        if IS_MAC:
            subprocess.run(["osascript", "-e", f"display alert {applescript_text(title)} message "
                            f"{applescript_text(text)} as critical"], timeout=600,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
    except Exception:
        pass
    return False


def mac_choose_file(prompt: str, folder: Path) -> str:
    """The Finder "choose file" dialog; the POSIX path or "" when cancelled."""
    script = (f"POSIX path of (choose file with prompt {applescript_text(prompt)} "
              f"default location (POSIX file {applescript_text(str(folder))}))")
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=1800)
    return result.stdout.strip() if result.returncode == 0 else ""


def mac_language() -> str:
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
