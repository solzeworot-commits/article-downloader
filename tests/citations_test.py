"""Retracted articles and «🔁 Найти ещё по ссылкам» (v8.3): python citations_test.py <portable/scripts>

A fake OpenAlex knows four selected articles (S1-S4, S4 retracted) and the
works around them. Checks: the retraction flag in the lookup and in the
separate check, the order of the candidates (linked to more selected
articles first, then by citations), the ones left out (already in the list,
no DOI, not an article), the count of links both ways, the stop, the
articles added to a review (kept in the review, not in the list file) and
PRISMA counting them as found through citations.
"""
import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

PORT = 8025
os.environ.update(ARTICLE_OPENALEX_URL=f"http://127.0.0.1:{PORT}/works", NO_PROXY="127.0.0.1,localhost")
sys.path.insert(0, sys.argv[1])
import review as rv  # noqa: E402

A = "https://openalex.org/"


def work(wid, doi, title, cited_by=10, year=2020, kind="article", retracted=False, refs=()):
    return {"id": A + wid, "doi": f"https://doi.org/{doi}" if doi else None, "display_name": title,
            "publication_year": year, "cited_by_count": cited_by, "type": kind, "is_retracted": retracted,
            "primary_location": {"source": {"display_name": "Food Chemistry"}},
            "referenced_works": [A + r for r in refs]}


WORKS = {
    # the selected articles
    "W1": work("W1", "10.1/s1", "Selected one", refs=["W10", "W11", "W12", "W13"]),
    "W2": work("W2", "10.1/s2", "Selected two", refs=["W10", "W11", "W14"]),
    "W3": work("W3", "10.1/s3", "Selected three", refs=["W10", "W2"]),
    "W4": work("W4", "10.1/s4", "Selected four, retracted", retracted=True, refs=["W15"]),
    # what they cite
    "W10": work("W10", "10.1/ref-a", "Cited by three", cited_by=50),
    "W11": work("W11", "10.1/ref-b", "Cited by two", cited_by=900),
    "W12": work("W12", "10.1/in-list", "Already in the list", cited_by=5),
    "W13": work("W13", None, "No DOI", cited_by=5),
    "W14": work("W14", "10.1/erratum", "An erratum", kind="erratum"),
    "W15": work("W15", "10.1/ref-c", "Cited once, retracted", cited_by=3, retracted=True),
    # what cites them
    "W20": work("W20", "10.1/cit-a", "Cites two of them", cited_by=7, year=2024, refs=["W1", "W2"]),
    "W21": work("W21", "10.1/cit-b", "Cites one", cited_by=400, year=2023, refs=["W1"]),
}
CITING = {"W1": ["W20", "W21"], "W2": ["W20"], "W3": [], "W4": []}
SEEN = []


class Fake(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        q = {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}
        SEEN.append(q)
        flt = unquote(q.get("filter", ""))
        if flt.startswith("doi:"):
            dois = flt[4:].split("|")
            res = [w for w in WORKS.values() if w["doi"] and w["doi"].split("doi.org/")[1] in dois]
        elif flt.startswith("cites:"):
            res = [WORKS[c] for c in CITING.get(flt[6:], [])]
        elif flt.startswith("openalex:"):
            res = [WORKS[i] for i in flt[9:].split("|") if i in WORKS]
        else:
            res = []
        fields = q.get("select", "").split(",")
        res = [{k: v for k, v in w.items() if k in fields} for w in res]
        body = json.dumps({"results": res}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


srv = ThreadingHTTPServer(("127.0.0.1", PORT), Fake)
threading.Thread(target=srv.serve_forever, daemon=True).start()
fails = []


def check(name, got, want):
    if got != want:
        fails.append(f"{name}: {got!r} != {want!r}")


# ---- retractions
flags = rv.fetch_retracted(["10.1/s1", "10.1/s4", "10.1/unknown"], {"openalex": "k"})
check("retracted flags", flags, {"10.1/s1": False, "10.1/s4": True, "10.1/unknown": False})
check("api key passed", SEEN[-1].get("api_key"), "k")
oa = rv.fetch_openalex(["10.1/s4"], {})
check("lookup has the flag", oa["10.1/s4"]["retracted"], True)
check("merge keeps it", rv.merge_meta(oa["10.1/s4"], None)["retracted"], True)
check("no OpenAlex answer, no flag", "retracted" in rv.merge_meta(None, {"title": "x"}), False)

# ---- the citation search
steps = []
res = rv.snowball(["10.1/s1", "10.1/s2", "10.1/s3", "10.1/s4", "10.1/nowhere"],
                  {"10.1/s1", "10.1/s2", "10.1/s3", "10.1/s4", "10.1/in-list"}, {},
                  progress=lambda d, t: steps.append((d, t)))
got = [(c["doi"], c["links"], c["cited_in"], c["cites"]) for c in res["candidates"]]
check("candidates and links", got, [
    ("10.1/ref-a", 3, 3, 0),      # cited by three selected articles
    ("10.1/ref-b", 2, 2, 0),      # cited by two, 900 citations
    ("10.1/cit-a", 2, 0, 2),      # cites two of them, 7 citations
    ("10.1/cit-b", 1, 0, 1),      # cites one, 400 citations
    ("10.1/ref-c", 1, 1, 0),      # cited once, 3 citations
])
check("seeds", (res["seeds"], res["not_found"], res["checked"]), (4, 1, 4))
check("progress", steps[-1], (4, 4))
check("retracted candidate flagged", [c["retracted"] for c in res["candidates"] if c["doi"] == "10.1/ref-c"], [True])
check("selected W2 cited by W3 is not a candidate", any(c["doi"] == "10.1/s2" for c in res["candidates"]), False)
check("candidate fields", sorted(res["candidates"][0]),
      sorted(["id", "doi", "title", "year", "journal", "cited_by", "type", "retracted", "links", "cited_in", "cites"]))
res2 = rv.snowball(["10.1/s1"], set(), {}, limit=2)
check("limit", (len(res2["candidates"]), res2["more"] > 0), (2, True))
res3 = rv.snowball(["10.1/s1", "10.1/s2"], set(), {}, stop=lambda: True)
check("stop", res3["checked"], 0)

# ---- added articles live in the review, PRISMA counts them apart
tmp = Path(tempfile.mkdtemp())
(tmp / "list.txt").write_text("10.1/s1\n10.1/s2\n", encoding="utf-8")
data = rv.load_review(tmp, "list.txt", ["10.1/s1", "10.1/s2"])
data["records"]["10.1/ref-a"] = {"doi": "10.1/ref-a", "decision": "include", "reason": "", "found": True, "via": "refs"}
data["added"] = ["10.1/ref-a"]
data["records"]["10.1/s1"]["decision"] = "include"
rv.save_review(tmp, data)
again = rv.load_review(tmp, "list.txt", ["10.1/s1", "10.1/s2"])
check("order keeps added", again["order"], ["10.1/s1", "10.1/s2", "10.1/ref-a"])
check("list file untouched", (tmp / "list.txt").read_text(encoding="utf-8"), "10.1/s1\n10.1/s2\n")
p = rv.prisma(again, {"per_source": {"scopus": 2}}, set())
check("prisma", (p["identified"], p["via_refs"], p["screened"], p["included"]), (2, 1, 3, 2))
p2 = rv.prisma(again, {}, set())
check("prisma without sources", (p2["identified"], p2["via_refs"]), (2, 1))

srv.shutdown()
if fails:
    print("\n".join(fails))
    sys.exit(1)
print("citations tests: OK")
