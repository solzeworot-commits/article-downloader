"""
Download statistics (tools/download_stats.py), without the network:

    python download_stats_test.py

Counters of rebuilt "portable-latest" releases add up by version, a second
run over the same release adds nothing, and the data survive the round trip
through the release body.
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import download_stats as st  # noqa: E402


def check(cond, what):
    if not cond:
        raise SystemExit("download stats test FAILED: " + what)


def release(rid, name, zips, launches, published="2026-11-04T16:54:05Z"):
    return {"id": rid, "name": name, "published_at": published,
            "assets": [{"name": st.ZIP_NAME, "download_count": zips},
                       {"name": st.VERSION_NAME, "download_count": launches}]}


now = datetime(2026, 11, 5, 9, 30, tzinfo=timezone.utc)
data = st.load_data("")
st.record(data, release(1, "Загрузчик статей 7.4 — готовая программа для Windows", 10, 40))
st.record(data, release(1, "Загрузчик статей 7.4 — готовая программа для Windows", 12, 45))   # same release, later
st.record(data, release(2, "Загрузчик статей 7.4 — готовая программа для Windows", 3, 7))     # rebuilt 7.4
st.record(data, release(3, "Загрузчик статей 7.5 — готовая программа для Windows", 5, 9, "2026-11-10T10:00:00Z"))
st.record(data, release(4, "Загрузчик статей — готовая программа для Windows", 1, 0))         # before 7.2

body = st.render(data, now, current_id="3")
check("**21**" in body and "**61**" in body, "totals: 12+3+5+1 downloads, 45+7+9 launches")
check("| 7.4 | 04.11.2026 | 15 | 52 |" in body, "7.4 adds up its two releases")
check("| 7.5 (сейчас) | 10.11.2026 | 5 | 9 |" in body, "current version marked")
check(body.index("| 7.5") < body.index("| 7.4") < body.index("| до 7.2"), "newest version first")
check("05.11.2026 14:30" in body, "time in Astana")

again = st.load_data(body)
check(again == data, "data survive the release body")
st.record(again, release(1, "Загрузчик статей 7.4 — готовая программа для Windows", 12, 45))
check(st.render(again, now, current_id="3") == body, "recording the same release twice changes nothing")
check(st.version_of({"name": "Загрузчик статей 7.10 — …"}) == "7.10", "two-digit minor version")
print("download stats tests: OK")
