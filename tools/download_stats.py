# © 2026 Bucaneiro. Бесплатная программа; условия — в файле LICENSE.
"""
Download statistics that survive rebuilds:

    GH_TOKEN=… GITHUB_REPOSITORY=owner/repo python tools/download_stats.py

GitHub counts downloads per release file, and the "portable-latest" release
is deleted and made again at every build, so its counters start from zero
each time. This script remembers the counters of every "portable-latest"
release it has seen (by release id, so running it twice adds nothing) and
writes the totals as a table into the body of the "download-stats" release:
https://github.com/<repo>/releases/tag/download-stats. The data travel in a
hidden comment of that body; nothing is committed.

Run by the Windows build right before the old release is deleted and every
hour by .github/workflows/download-stats.yml.

- downloads: ArticleDownloader_Portable.zip (by hand or by the automatic update);
- launches: version.json, which every copy from 7.2 on fetches at start.
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

STATS_TAG = "download-stats"
LATEST_TAG = "portable-latest"
ZIP_NAME = "ArticleDownloader_Portable.zip"
VERSION_NAME = "version.json"
DATA_RE = re.compile(r"<!-- stats-json (.*?) -->", re.S)
ASTANA = timezone(timedelta(hours=5))


def api(method: str, path: str, data: dict | None = None):
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
    request = urllib.request.Request(
        "https://api.github.com" + path,
        data=json.dumps(data).encode() if data is not None else None,
        method=method,
        headers={"Accept": "application/vnd.github+json", "Authorization": f"Bearer {token}",
                 "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "article-downloader-stats"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read()
            return json.loads(body) if body else None
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def load_data(body: str) -> dict:
    match = DATA_RE.search(body or "")
    if match:
        try:
            return json.loads(match.group(1))
        except ValueError:
            pass
    return {"releases": {}}


def version_of(release: dict) -> str:
    match = re.search(r"\d+(?:\.\d+)+", release.get("name") or "")
    return match.group(0) if match else "до 7.2"


def record(data: dict, release: dict) -> dict:
    """Keep the current counters of this release (by id: a second run only refreshes them)."""
    counts = {a["name"]: a.get("download_count", 0) for a in release.get("assets", [])}
    entry = data["releases"].setdefault(str(release["id"]), {})
    entry.update(version=version_of(release), since=(release.get("published_at") or "")[:10],
                 downloads=max(entry.get("downloads", 0), counts.get(ZIP_NAME, 0)),
                 launches=max(entry.get("launches", 0), counts.get(VERSION_NAME, 0)))
    return data


def _version_key(version: str):
    return tuple(int(p) for p in re.findall(r"\d+", version)) or (0,)


def render(data: dict, now: datetime, current_id: str | None = None) -> str:
    by_version: dict[str, dict] = {}
    for release_id, entry in data["releases"].items():
        row = by_version.setdefault(entry["version"], {"downloads": 0, "launches": 0, "since": entry["since"],
                                                        "current": False})
        row["downloads"] += entry.get("downloads", 0)
        row["launches"] += entry.get("launches", 0)
        row["since"] = min(row["since"] or entry["since"], entry["since"] or row["since"])
        row["current"] |= release_id == current_id
    total_d = sum(r["downloads"] for r in by_version.values())
    total_l = sum(r["launches"] for r in by_version.values())
    lines = [
        "## 📊 Статистика «Загрузчика статей»",
        "",
        f"Обновляется сама каждый час. Последнее обновление: **{now.astimezone(ASTANA):%d.%m.%Y %H:%M}** по Астане.",
        "",
        "| | Всего |",
        "|---|---:|",
        f"| ⬇ Скачиваний программы | **{total_d}** |",
        f"| ▶ Запусков программы | **{total_l}** |",
        "",
        "### По версиям",
        "",
        "| Версия | Выложена | Скачиваний | Запусков, пока была новейшей |",
        "|---|---|---:|---:|",
    ]
    for version in sorted(by_version, key=_version_key, reverse=True):
        row = by_version[version]
        since = datetime.strptime(row["since"], "%Y-%m-%d").strftime("%d.%m.%Y") if row["since"] else "—"
        mark = " (сейчас)" if row["current"] else ""
        lines.append(f"| {version}{mark} | {since} | {row['downloads']} | {row['launches']} |")
    lines += [
        "",
        "**Скачивания** — сколько раз скачали архив программы, вручную или кнопкой «Обновить автоматически».",
        "**Запуски** — сколько раз программы версии 7.2 и новее при старте проверяли обновления; "
        "один человек обычно запускает программу много раз, поэтому это не число людей.",
        "Счёт идёт с 4 ноября 2026 года: более ранние скачивания GitHub обнулил при пересборках.",
        "",
        "<!-- stats-json " + json.dumps(data, ensure_ascii=False, sort_keys=True) + " -->",
    ]
    return "\n".join(lines) + "\n"


def main():
    repo = os.environ.get("GITHUB_REPOSITORY") or "solzeworot-commits/article-downloader"
    stats = api("GET", f"/repos/{repo}/releases/tags/{STATS_TAG}")
    data = load_data(stats["body"] if stats else "")
    latest = api("GET", f"/repos/{repo}/releases/tags/{LATEST_TAG}")
    if latest:
        record(data, latest)
    body = render(data, datetime.now(timezone.utc), str(latest["id"]) if latest else None)
    if stats:
        api("PATCH", f"/repos/{repo}/releases/{stats['id']}", {"body": body})
    else:
        api("POST", f"/repos/{repo}/releases", {
            "tag_name": STATS_TAG, "name": "📊 Статистика скачиваний", "body": body,
            "prerelease": True, "make_latest": "false"})
    totals = re.search(r"Скачиваний программы \| \*\*(\d+)\*\*.*?Запусков программы \| \*\*(\d+)\*\*", body, re.S)
    print(f"stats: downloads {totals.group(1)}, launches {totals.group(2)} — "
          f"https://github.com/{repo}/releases/tag/{STATS_TAG}")


if __name__ == "__main__":
    sys.exit(main())
