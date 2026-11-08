# -*- coding: utf-8 -*-
# © 2026 Bucaneiro. Бесплатная программа; условия — в файле ЛИЦЕНЗИЯ.txt.
"""
What differs between Windows and macOS (v8.6, the Mac beta).

- user_data_dir(): where the keys and the downloader's browser profile live,
  outside the program folder — %LOCALAPPDATA%\\ArticleDownloader on Windows,
  ~/Library/Application Support/ArticleDownloader on a Mac (LOCALAPPDATA, when
  set, wins everywhere: the tests and the bench point it at a temporary folder).
- open_path() / reveal(): a file or folder in Explorer / Finder.
- alert(): a message when the window cannot open.
- mac_browser(), mac_choose_file(), mac_language(): in mac_support.py, which
  only the Mac archive has (v8.6.1: no Mac shell commands in the Windows one).
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

def user_data_dir() -> Path | None:
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local) / "ArticleDownloader"
    if IS_MAC:
        return Path.home() / "Library" / "Application Support" / "ArticleDownloader"
    return None


def mac_browser(kind: str) -> str | None:
    import mac_support
    return mac_support.browser(kind)


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


def alert(text: str, title: str) -> bool:
    """A message box; False when there is none on this system."""
    try:
        if IS_WIN:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, text, title, 0x10)
            return True
        if IS_MAC:
            import mac_support
            mac_support.alert(text, title)
            return True
    except Exception:
        pass
    return False


def mac_choose_file(prompt: str, folder: Path) -> str:
    import mac_support
    return mac_support.choose_file(prompt, folder)


def mac_language() -> str:
    try:
        import mac_support
    except ImportError:
        return ""
    return mac_support.language()
