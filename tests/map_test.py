"""«🕸 Карта статей» (v9.1): python map_test.py <portable/scripts>

map_graph(): the links inside the list only (a reference to a paper outside
the list and to itself ignored), how often each paper is cited by the others,
the groups of papers that cite each other (label propagation, the same every
time), the common words of their titles, the papers without links.
citation_links() with a fake OpenAlex (chunks of 50, unknown DOIs). The whole
way through the program: the map of a list is drawn, kept in the list's review
file, the window gets each paper's name, title and whether its PDF is here;
fewer than two papers is refused.
"""
import json
import os
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

PORT = 8045
tmp = Path(tempfile.mkdtemp(prefix="ad_map_"))
os.environ.update(ARTICLE_UI_NO_WINDOW="1", NO_PROXY="127.0.0.1,localhost",
                  ARTICLE_OPENALEX_URL=f"http://127.0.0.1:{PORT}/works", ARTICLE_KEYS_FILE=str(tmp / "keys.json"))
sys.path.insert(0, sys.argv[1])
import app  # noqa: E402
import pdf_table as pt  # noqa: E402
import review as rv  # noqa: E402

fails = []


def check(name, got, want):
    if got != want:
        fails.append(f"{name}: {got!r} != {want!r}")


# ---- the graph
links = {
    "a": {"id": "W1", "refs": [], "cited": 120},
    "b": {"id": "W2", "refs": ["W1", "W2"], "cited": 40},           # cites itself: ignored
    "c": {"id": "W3", "refs": ["W1", "W2", "W999"], "cited": 10},   # W999 is not in the list
    "d": {"id": "W4", "refs": [], "cited": 5},
    "e": {"id": "W5", "refs": ["W4"], "cited": 3},
    "f": {"id": "W6", "refs": ["W777"], "cited": 1},
}
titles = {"a": "Ultrasound-assisted extraction of polyphenols", "b": "Ultrasound extraction of grape phenolics",
          "c": "Ultrasonic extraction of polyphenols from pomace", "d": "Microwave drying of pomace",
          "e": "Microwave drying kinetics of grape skins", "f": "Chitosan films"}
g = rv.map_graph(links, titles)
check("edges", g["edges"], [["b", "a"], ["c", "a"], ["c", "b"], ["e", "d"]])
nodes = {n["doi"]: n for n in g["nodes"]}
check("cited inside the list", {d: nodes[d]["cited_in"] for d in "abcdef"}, {"a": 2, "b": 1, "c": 0, "d": 1, "e": 0, "f": 0})
check("groups", {d: nodes[d]["group"] for d in "abcdef"}, {"a": 0, "b": 0, "c": 0, "d": 1, "e": 1, "f": -1})
check("group words", [(x["size"], x["words"]) for x in g["groups"]],
      [(3, ["extraction", "polyphenols", "ultrasound"]), (2, ["drying", "microwave"])])
check("without links", g["alone"], 1)
check("the same every time", rv.map_graph(links, titles), g)

# ---- a fake OpenAlex
WORKS = {f"10.1000/p{i}": {"id": f"https://openalex.org/W{i}", "doi": f"https://doi.org/10.1000/p{i}",
                        "referenced_works": [f"https://openalex.org/W{j}" for j in range(max(0, i - 2), i)],
                        "cited_by_count": 100 - i} for i in range(60)}
ASKED = []


class Fake(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        q = {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}
        dois = [d.lower() for d in unquote(q.get("filter", "")).removeprefix("doi:").split("|") if d]
        ASKED.append(len(dois))
        body = json.dumps({"results": [WORKS[d] for d in dois if d in WORKS]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


srv = ThreadingHTTPServer(("127.0.0.1", PORT), Fake)
threading.Thread(target=srv.serve_forever, daemon=True).start()

seen = []
got = rv.citation_links([f"10.1000/p{i}" for i in range(55)] + ["10.1000/unknown"], None, lambda d, t: seen.append((d, t)))
check("chunks of 50", ASKED, [50, 6])
check("known DOIs", len(got), 55)
check("refs as ids", got["10.1000/p5"]["refs"], ["W3", "W4"])
check("progress", seen[-1], (56, 56))

# ---- the whole way through the program
app.SETTINGS_PATH = tmp / "settings.json"
app.INBOX = tmp / "inbox"
app.REPORT_PATH = tmp / "report.csv"
app.S.settings["pdf_dir"] = str(tmp / "pdf")
app.INBOX.mkdir()
(tmp / "pdf").mkdir()
DOIS = [f"10.1000/p{i}" for i in range(6)] + ["10.1000/unknown"]
(app.INBOX / "Обзор.txt").write_text("\n".join(DOIS), encoding="utf-8")
data = rv.load_review(app.INBOX, "Обзор.txt", DOIS)
for i, doi in enumerate(DOIS):
    data["records"][doi].update(title=f"Paper about grape pomace {i}", year=2020 + i % 3,
                                authors=[{"family": f"Author{i}", "given": "A."}], found=True,
                                decision="include" if i < 5 else "maybe")
rv.save_review(app.INBOX, data)
(tmp / "pdf" / f"X_{pt.sanitized('10.1000/p1')}.pdf").write_bytes(b"%PDF-1.4 fake")

(app.INBOX / "Одна.txt").write_text("10.1000/p0", encoding="utf-8")
check("too few", "error" in app.handle_api("/api/map_start", {"list": "Одна.txt", "scope": "all"}), True)
check("started", app.handle_api("/api/map_start", {"list": "Обзор.txt", "scope": "included"}), {})
for _ in range(200):
    state = app.handle_api("/api/map", {"list": "Обзор.txt"})
    if not state["job"].get("running"):
        break
    time.sleep(0.05)
m = state["map"]
check("no error", state["job"].get("error"), "")
check("the five «✓» papers", sorted(n["doi"] for n in m["nodes"]), [f"10.1000/p{i}" for i in range(5)])
check("links", m["edges"], [["10.1000/p1", "10.1000/p0"], ["10.1000/p2", "10.1000/p0"], ["10.1000/p2", "10.1000/p1"],
                            ["10.1000/p3", "10.1000/p1"], ["10.1000/p3", "10.1000/p2"], ["10.1000/p4", "10.1000/p2"],
                            ["10.1000/p4", "10.1000/p3"]])
p1 = next(n for n in m["nodes"] if n["doi"] == "10.1000/p1")
check("what the window gets", (p1["ref"], p1["title"], p1["pdf"], p1["cited_in"]),
      ("Author1, 2021", "Paper about grape pomace 1", True, 2))
check("kept in the review file", json.loads(rv.review_path(app.INBOX, "Обзор.txt").read_text(encoding="utf-8"))["map"]["edges"],
      m["edges"])
app.S.reviews.clear()                              # a restart: the map is still there
check("after a restart", app.handle_api("/api/map", {"list": "Обзор.txt"})["map"]["edges"], m["edges"])
check("unknown list", "error" in app.handle_api("/api/map", {"list": "Нет такого.txt"}), True)

srv.shutdown()
if fails:
    print("\n".join(fails))
    sys.exit(1)
print("map tests: OK")
