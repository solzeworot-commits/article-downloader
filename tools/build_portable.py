# -*- coding: utf-8 -*-
"""
Build the portable Windows folder and its zip from ../portable:

    python tools/build_portable.py [--out dist] [--python 3.13.17]

Runs on Windows or Linux (needs internet and pip). Takes the NuGet "python"
package (a complete CPython for Windows x64 that needs no installer), adds the
win_amd64 wheels of playwright, psutil, pypdf and qrcode unpacked into
python/Lib/site-packages, copies ../portable next to it and zips the result:

    dist/ArticleDownloader/            ready folder
    dist/ArticleDownloader_Portable.zip

Playwright only drives an installed browser over CDP, so no Playwright
browsers are downloaded.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parent / "portable"
# qrcode draws the QR codes of the "Поблагодарить автора" window (SVG, no Pillow).
PACKAGES = ["playwright", "psutil", "pypdf", "qrcode"]


def download(url: str, dest: Path):
    if dest.exists():
        return
    print("download", url)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(url, timeout=300) as response, tmp.open("wb") as f:
        shutil.copyfileobj(response, f)
    tmp.replace(dest)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(HERE.parent / "dist"))
    parser.add_argument("--python", default="3.13.17", help="NuGet python version")
    args = parser.parse_args()

    out = Path(args.out).resolve()
    cache = out / "cache"
    wheels = cache / f"wheels-{args.python}"
    target = out / "ArticleDownloader"
    cache.mkdir(parents=True, exist_ok=True)

    nupkg = cache / f"python.{args.python}.nupkg"
    download(
        f"https://api.nuget.org/v3-flatcontainer/python/{args.python}/python.{args.python}.nupkg",
        nupkg,
    )

    major, minor = args.python.split(".")[:2]
    abi = f"cp{major}{minor}"
    subprocess.run(
        [sys.executable, "-m", "pip", "download", "--quiet",
         "--only-binary=:all:", "--platform", "win_amd64",
         "--python-version", f"{major}.{minor}", "--implementation", "cp",
         "--abi", abi, "--abi", "abi3", "--abi", "none",
         "-d", str(wheels), *PACKAGES],
        check=True,
    )

    if target.exists():
        shutil.rmtree(target)
    python_dir = target / "python"

    with zipfile.ZipFile(nupkg) as z:
        for info in z.infolist():
            if not info.filename.startswith("tools/") or info.is_dir():
                continue
            rel = info.filename[len("tools/"):]
            if rel.split("/")[0] in ("include", "libs"):
                continue  # headers and import libraries for building C extensions
            dest = python_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(z.read(info))

    site = python_dir / "Lib" / "site-packages"
    for wheel in sorted(wheels.glob("*.whl")):
        print("unpack", wheel.name)
        with zipfile.ZipFile(wheel) as z:
            z.extractall(site)
    for data_dir in site.glob("*.data"):
        shutil.rmtree(data_dir)  # e.g. greenlet's C headers

    shutil.copytree(SOURCE, target, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    # The licence goes next to the program as a Notepad-friendly file
    # (UTF-8 with BOM, CRLF), like "КАК ПОЛЬЗОВАТЬСЯ.txt".
    licence = (HERE.parent / "LICENSE").read_text(encoding="utf-8")
    (target / "ЛИЦЕНЗИЯ.txt").write_bytes(
        b"\xef\xbb\xbf" + licence.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8"))

    archive = out / "ArticleDownloader_Portable.zip"
    archive.unlink(missing_ok=True)
    # zipfile marks the Cyrillic names as UTF-8; Explorer and 7-Zip read them.
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        z.comment = ("Article Downloader (c) 2026 Bucaneiro. Free program, see ЛИЦЕНЗИЯ.txt. "
                     "Official page: https://github.com/solzeworot-commits/article-downloader"
                     ).encode("utf-8")
        for path in sorted(target.rglob("*")):
            if path.is_file():
                z.write(path, path.relative_to(out).as_posix())

    print(f"folder: {target}")
    print(f"zip:    {archive} ({archive.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
