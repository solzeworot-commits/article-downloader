"""«🔔 Новое по теме» (v8.5): python topics_test.py <portable/scripts>

A saved API search becomes a topic (the keywords, the sources, every DOI the
search found); after 30 days the home screen gets a reminder; «Проверить
новое» searches again from the year before the last check and saves only
the DOIs never seen before as a new list with its PRISMA data; «Позже» puts
the reminder off for a week, «Забыть» drops the topic. A fake OpenAlex.
"""
import json
import os
import sys
import tempfile
import threading
import time
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

PORT = 8029
os.environ.update(ARTICLE_UI_NO_WINDOW="1", ARTICLE_OPENALEX_URL=f"http://127.0.0.1:{PORT}/works",
                  NO_PROXY="127.0.0.1,localhost")
sys.path.insert(0, sys.argv[1])
tmp = Path(tempfile.mkdtemp(prefix="ad_topics_"))
os.environ["ARTICLE_KEYS_FILE"] = str(tmp / "keys.json")
import app  # noqa: E402

app.SETTINGS_PATH = tmp / "settings.json"
app.INBOX = tmp / "inbox"
app.TOPICS_PATH = app.INBOX / "_подробно" / "_темы.json"
fails = []


def check(name, got, want):
    if got != want:
        fails.append(f"{name}: {got!r} != {want!r}")


def work(doi, year=2025):
    return {"doi": "https://doi.org/" + doi, "display_name": "Title " + doi, "publication_year": year,
            "primary_location": {"source": {"display_name": "Food Chemistry"}}}


RESULTS = [work("10.1/a"), work("10.1/b")]
FILTERS = []


class Fake(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        q = {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}
        FILTERS.append(q.get("filter", ""))
        body = json.dumps({"meta": {"count": len(RESULTS), "next_cursor": None}, "results": RESULTS}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


srv = ThreadingHTTPServer(("127.0.0.1", PORT), Fake)
threading.Thread(target=srv.serve_forever, daemon=True).start()
S = app.S


def wait(topic_id):
    for _ in range(100):
        with S.lock:
            job = S.topic_jobs.get(topic_id)
            if job and not job["running"]:
                return dict(job)
        time.sleep(0.05)
    fails.append("the check did not finish")
    return {}


# ---- an API search, saved: the topic is remembered with everything it found
params = {"keywords": "grape pomace; grape marc\npolyphenols", "year_from": "2015", "year_to": "2024", "max_results": "50"}
app.start_search(params, ["openalex"])
for _ in range(100):
    if not S.search["running"]:
        break
    time.sleep(0.05)
check("search found", [r["doi"] for r in S.search_records], ["10.1/a", "10.1/b"])
res = app.handle_api("/api/search_save", {"dois": ["10.1/a"], "slug": "grape", "keywords": params["keywords"]})
check("saved and remembered", (res.get("count"), res.get("topic")), (1, True))
topics = json.loads(app.TOPICS_PATH.read_text(encoding="utf-8"))
check("one topic", len(topics), 1)
t = topics[0]
check("topic", (t["title"], t["sources"], t["seen"]), ("grape pomace; grape marc · polyphenols", ["openalex"], ["10.1/a", "10.1/b"]))
check("not due today", app.handle_api("/api/topics", {})["topics"][0]["due"], False)

# the same keywords in another order and case: the same topic
app.remember_topic({"keywords": "Polyphenols\ngrape pomace; grape marc"}, ["openalex"], [{"doi": "10.1/c"}], "x.txt")
with S.lock:
    check("same topic", (len(app.load_topics()), "10.1/c" in app.load_topics()[0]["seen"]), (1, True))
    app.load_topics()[0]["seen"].remove("10.1/c")

# ---- 40 days later: due; the new search starts from the year before the last check
old = (date.today() - timedelta(days=40)).isoformat()
with S.lock:
    app.load_topics()[0]["checked"] = old
due = app.topics_state()[0]
check("due", (due["due"], due["days"]), (True, 40))
tid = due["id"]
check("recheck params", app.recheck_params(params, "2026-03-01"), dict(params, year_from="2025", year_to=""))
check("recheck keeps a later year", app.recheck_params(dict(params, year_from="2026"), "2026-03-01")["year_from"], "2026")
RESULTS[:] = [work("10.1/a"), work("10.1/b"), work("10.1/new1", 2026), work("10.1/new2", 2026)]
check("check starts", app.handle_api("/api/topic_check", {"id": tid}).get("error"), None)
job = wait(tid)
check("new found", (job.get("new"), job.get("found")), (2, 4))
check("year filter", f"from_publication_date:{date.fromisoformat(old).year - 1}-01-01" in FILTERS[-1], True)
new_list = app.INBOX / job["list"]
check("new list", new_list.read_text(encoding="utf-8"), "10.1/new1\n10.1/new2\n")
meta = json.loads((app.INBOX / "_подробно" / (new_list.stem + ".meta.json")).read_text(encoding="utf-8"))
check("meta", (meta["per_source"], meta["topic"], meta["unique"]), ({"OpenAlex": 2}, tid, 2))
state = app.topics_state()[0]
check("checked today", (state["days"], state["due"], state["last_new"]), (0, False, 2))

# ---- nothing new the next time
job = (app.handle_api("/api/topic_check", {"id": tid}), wait(tid))[1]
check("nothing new", (job.get("new"), job.get("list")), (0, ""))

# ---- a source that fails
RESULTS[:] = []
srv.shutdown()
srv.server_close()
job = (app.handle_api("/api/topic_check", {"id": tid}), wait(tid))[1]
check("error shown", bool(job.get("error")), True)

# ---- later / forget
with S.lock:
    app.load_topics()[0]["checked"] = old
app.handle_api("/api/topic_later", {"id": tid})
check("later: not due for a week", app.topics_state()[0]["due"], False)
res = app.handle_api("/api/topic_forget", {"id": tid})
check("forgotten", (res["topics"], json.loads(app.TOPICS_PATH.read_text(encoding="utf-8"))), ([], []))
check("unknown topic", "error" in app.handle_api("/api/topic_check", {"id": "nope"}), True)

if fails:
    print("\n".join(fails))
    sys.exit(1)
print("topics tests: OK")
