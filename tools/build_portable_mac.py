# -*- coding: utf-8 -*-
"""
Build the Mac folder of the program and its zip from ../portable (v8.6, beta):

    python tools/build_portable_mac.py [--out dist] [--python 3.13]

Runs on a Mac (the GitHub macOS runner); on Linux it builds the same folder
for a look, zipped with `zip` instead of `ditto`. Two complete CPythons from
python-build-standalone (no installer, any folder): python/arm64 for Apple
silicon (M1 and newer) and python/x86_64 for Intel Macs; "СКАЧАТЬ
СТАТЬИ.command" picks the one for the Mac it runs on. Each gets the macOS
wheels of playwright, psutil, pypdf and qrcode. Playwright only drives an
installed Chrome, Edge or Yandex Browser over CDP, so no browsers are
downloaded.

    dist/ArticleDownloader/            ready folder
    dist/ArticleDownloader_Mac.zip     zipped with ditto: executable bits and links kept
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import http.client
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parent / "portable"
PACKAGES = ["playwright", "psutil", "pypdf", "qrcode"]
PBS_API = "https://api.github.com/repos/astral-sh/python-build-standalone/releases/latest"
ARCHES = {"arm64": ("aarch64-apple-darwin", "macosx_11_0_arm64"),
          "x86_64": ("x86_64-apple-darwin", "macosx_11_0_x86_64")}
WINDOWS_ONLY = ("СКАЧАТЬ СТАТЬИ.bat",)
# Parts of the standard library the program never uses: smaller download.
DROP = ("test", "idlelib", "tkinter", "turtledemo", "ensurepip", "lib2to3", "pydoc_data")

LAUNCHER = r"""#!/bin/bash
# Загрузчик статей для Mac: двойной щелчок запускает программу.
# Article Downloader for Mac: double-click to start. (c) 2026 Bucaneiro.
cd "$(dirname "$0")" || exit 1
# The Mac marks every file of a downloaded archive as "from the internet";
# once this script was allowed to run, the program's own files need no more asking.
/usr/bin/xattr -dr com.apple.quarantine . 2>/dev/null
if [ "$(uname -m)" = "arm64" ] && [ -x python/arm64/bin/python3 ]; then
    PY="$PWD/python/arm64/bin/python3"
else
    PY="$PWD/python/x86_64/bin/python3"
fi
mkdir -p logs
echo ""
echo "  Загрузчик статей запускается — окно программы откроется через несколько секунд."
echo "  Это окно Терминала можно закрыть."
echo ""
nohup "$PY" -E -s scripts/app.py "$@" >> logs/start.log 2>&1 &
disown
sleep 3
"""

README_MAC = """ЗАГРУЗЧИК СТАТЕЙ ДЛЯ MAC (бета)

Как запустить
1. Дважды щёлкните «СКАЧАТЬ СТАТЬИ.command».
2. В первый раз Mac скажет, что не может проверить разработчика, и не откроет файл.
   Это нормально: программа бесплатная и не подписана в Apple (подпись стоит 99 $ в год).
   Откройте  → Системные настройки → Конфиденциальность и безопасность, прокрутите вниз
   и нажмите «Всё равно открыть» рядом с «СКАЧАТЬ СТАТЬИ.command», затем введите пароль Mac.
   На старых macOS (до 15) можно проще: щёлкните файл правой кнопкой → «Открыть» → «Открыть».
3. Откроется окно Терминала, а через несколько секунд — окно программы. Терминал можно закрыть.
   Дальше программа запускается двойным щелчком без вопросов.

Для скачивания статей нужен Google Chrome, Microsoft Edge или Яндекс.Браузер
(в папке «Программы»). Safari не подходит.

Работает на Mac с процессором Apple (M1 и новее) и с Intel, macOS 11 и новее.
Это бета-версия: если что-то не так, нажмите в программе «🛟 Сообщить о проблеме».
Как пользоваться программой — в файле «КАК ПОЛЬЗОВАТЬСЯ.txt».
"""


def download(url: str, dest: Path, attempts: int = 5):
    """Download with a few tries: GitHub sometimes answers 504 for a minute (8.8.3 build)."""
    if dest.exists():
        return
    print("download", url)
    tmp = dest.with_suffix(dest.suffix + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": "ArticleDownloader-build"})
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(request, timeout=600) as response, tmp.open("wb") as f:
                shutil.copyfileobj(response, f)
            tmp.replace(dest)
            return
        except (OSError, http.client.HTTPException) as exc:      # URLError and HTTPError are OSErrors
            code = getattr(exc, "code", None)
            if (code is not None and code < 500 and code != 429) or attempt == attempts:
                raise
            wait = 15 * attempt
            print(f"  {exc}; again in {wait} s")
            time.sleep(wait)


def standalone_assets(minor: str) -> dict[str, str]:
    """{arch: url} of the newest python-build-standalone CPython <minor> for both Macs."""
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "ArticleDownloader-build"}
    if os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = "Bearer " + os.environ["GITHUB_TOKEN"]
    with urllib.request.urlopen(urllib.request.Request(PBS_API, headers=headers), timeout=60) as r:
        assets = {a["name"]: a["browser_download_url"] for a in json.load(r)["assets"]}
    out = {}
    for arch, (triple, _tag) in ARCHES.items():
        # e.g. cpython-3.13.9+20261014-aarch64-apple-darwin-install_only_stripped.tar.gz ("+" is %2B in the URL)
        pattern = re.compile(rf"^cpython-{re.escape(minor)}\.(\d+)\+\d+-{triple}-install_only_stripped\.tar\.gz$")
        found = sorted((int(m.group(1)), name) for name in assets if (m := pattern.match(name)))
        if not found:
            raise SystemExit(f"no python-build-standalone {minor} for {triple}")
        out[arch] = assets[found[-1][1]]
    return out


def install_python(arch: str, url: str, cache: Path, dest: Path, minor: str):
    tarball = cache / urllib.parse.unquote(url.rsplit("/", 1)[-1])
    download(url, tarball)
    tmp = cache / f"unpack-{arch}"
    shutil.rmtree(tmp, ignore_errors=True)
    with tarfile.open(tarball) as t:
        t.extractall(tmp, filter="tar") if hasattr(tarfile, "data_filter") else t.extractall(tmp)
    shutil.move(str(tmp / "python"), str(dest))
    lib = dest / "lib" / f"python{minor}"
    for name in DROP:
        shutil.rmtree(lib / name, ignore_errors=True)
    for cache_dir in lib.rglob("__pycache__"):
        shutil.rmtree(cache_dir, ignore_errors=True)
    # The wheels go in with pip below, built for this Mac's processor.
    site = lib / "site-packages"
    subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "--no-compile",
                    "--only-binary=:all:", "--platform", ARCHES[arch][1],
                    "--python-version", minor, "--implementation", "cp",
                    "--abi", "cp" + minor.replace(".", ""), "--abi", "abi3", "--abi", "none",
                    "--target", str(site), *PACKAGES], check=True)
    for extra in [*site.glob("*.data"), site / "bin", site / "include"]:
        shutil.rmtree(extra, ignore_errors=True)        # console scripts and C headers
    print(f"python/{arch}: {sum(f.stat().st_size for f in dest.rglob('*') if f.is_file()) / 1e6:.0f} MB")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(HERE.parent / "dist"))
    parser.add_argument("--python", default="3.13", help="CPython minor version")
    args = parser.parse_args()

    out = Path(args.out).resolve()
    cache = out / "cache-mac"
    target = out / "ArticleDownloader"
    cache.mkdir(parents=True, exist_ok=True)
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)

    for arch, url in standalone_assets(args.python).items():
        install_python(arch, url, cache, target / "python" / arch, args.python)

    shutil.copytree(SOURCE, target, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", *WINDOWS_ONLY))
    licence = (HERE.parent / "LICENSE").read_text(encoding="utf-8")
    (target / "ЛИЦЕНЗИЯ.txt").write_text(licence, encoding="utf-8")
    (target / "КАК ЗАПУСТИТЬ НА MAC.txt").write_text(README_MAC, encoding="utf-8")
    launcher = target / "СКАЧАТЬ СТАТЬИ.command"
    launcher.write_text(LAUNCHER, encoding="utf-8")
    launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    archive = out / "ArticleDownloader_Mac.zip"
    archive.unlink(missing_ok=True)
    if sys.platform == "darwin":
        # ditto is what Finder uses: the bits that make python and the launcher runnable survive.
        subprocess.run(["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(target), str(archive)],
                       check=True)
    else:
        subprocess.run(["zip", "-qry", str(archive), target.name], cwd=str(out), check=True)
    print(f"folder: {target}")
    print(f"zip:    {archive} ({archive.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
