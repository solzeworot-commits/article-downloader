"""«💬 Спросить по своим статьям» (v9.0): python ask_test.py <portable/scripts>

rank_passages(): the pages with at least two ideas of the question, the best
stretch of each, the best first; one idea is enough for a one-idea question.
With a fake ChatGPT: question_terms() (the groups of words) and
answer_from_papers() (statements only with valid sources; a statement without
a source is dropped; "not found"). The whole way through the program: a list
of three articles, two PDFs downloaded (named by their DOI), the excerpts sent
to the AI come only from the pages about the question, the answer points to
the paper and the page; no PDFs, no AI key, an empty question.
"""
import json
import os
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = 8043
tmp = Path(tempfile.mkdtemp(prefix="ad_ask_"))
os.environ.update(ARTICLE_UI_NO_WINDOW="1", NO_PROXY="127.0.0.1,localhost",
                  ARTICLE_AI_OPENAI_URL=f"http://127.0.0.1:{PORT}/v1", ARTICLE_KEYS_FILE=str(tmp / "keys.json"))
(tmp / "keys.json").write_text(json.dumps({"openai": "sk-test"}), encoding="utf-8")
sys.path.insert(0, sys.argv[1])
import ai_helper as ai  # noqa: E402
import app  # noqa: E402
import pdf_table as pt  # noqa: E402
import review as rv  # noqa: E402

fails = []


def make_pdf(path: Path, pages: list[list[str]]):
    """A PDF with one Helvetica text stream per page, one line per Tj (as in pdf_search_test)."""
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", None, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids = []
    for lines in pages:
        ops = ["BT", "/F1 11 Tf", "50 780 Td", "14 TL"]
        ops += ["(" + ln.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") + ") Tj T*" for ln in lines]
        stream = "\n".join(ops + ["ET"]).encode("latin-1")
        objs.append(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")
        content = len(objs)
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents {content} 0 R "
                    f"/Resources << /Font << /F1 3 0 R >> >> >>".encode())
        kids.append(len(objs))
    objs[1] = f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] /Count {len(kids)} >>".encode()
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for n, obj in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{n} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    path.write_bytes(bytes(out))


def check(name, got, want):
    if got != want:
        fails.append(f"{name}: {got!r} != {want!r}")


# ---- the excerpts
PAGES = [
    "Introduction. Grape pomace is a by-product of wine making and a source of phenolic compounds.",
    "Methods. Extraction was assisted by ultrasound at 20, 40 and 80 kHz. The highest yield of total phenolics "
    "was obtained at 40 kHz and 30 min, 52 mg GAE/g.",
    "Results. Microwave drying of the pomace changed its colour.",
]
GROUPS = [["ultrasound", "ultrasonic", "sonication"], ["khz", "frequency"], ["phenolic", "polyphenol"]]
best = pt.rank_passages(PAGES, GROUPS)
check("only the page with two ideas", [x["page"] for x in best], [2])
check("the stretch holds the answer", "40 kHz and 30 min" in best[0]["text"], True)
check("one-idea question", [x["page"] for x in pt.rank_passages(PAGES, [["microwave"]])], [3])
check("nothing", pt.rank_passages(PAGES, [["chitosan"], ["film"]]), [])
check("no groups", pt.rank_passages(PAGES, []), [])

# ---- a fake ChatGPT
SEEN = []
MODE = {"answer": "good"}


class Fake(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, data, code=200):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.send({"data": [{"id": "gpt-5-mini"}]})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        SEEN.append(body)
        prompt = body["messages"][-1]["content"]
        if prompt.startswith("Question:") and "E1 —" not in prompt:
            out = ({"groups": [["chitosan"], ["film", "coating"]]} if "хитозан" in prompt.lower() else
                   {"groups": [["Ultrasound", "sonication"], ["kHz", "frequency"], ["phenolic"]]})
        elif MODE["answer"] == "good":
            out = {"found": True, "summary": "Наибольший выход — при 40 кГц.",
                   "points": [{"text": "Максимум фенолов (52 мг GAE/г) получен при 40 кГц и 30 мин.", "sources": [1]},
                              {"text": "Утверждение без источника.", "sources": []},
                              {"text": "Ссылка на несуществующий фрагмент.", "sources": [9]},
                              {"text": "При 80 кГц выход ниже.", "sources": ["E2", 1]}]}
        else:
            out = {"found": False, "summary": "В этих статьях о частоте ничего нет.", "points": []}
        self.send({"choices": [{"index": 0, "message": {"role": "assistant",
                                                        "content": json.dumps(out, ensure_ascii=False)},
                                "finish_reason": "stop"}],
                   "usage": {"prompt_tokens": 500, "completion_tokens": 120}})


srv = ThreadingHTTPServer(("127.0.0.1", PORT), Fake)
threading.Thread(target=srv.serve_forever, daemon=True).start()
KEYS = {"openai": "sk-test"}

groups, cost = ai.question_terms("openai", KEYS, "при какой частоте ультразвука выход наибольший?")
check("groups in lower case", groups, [["ultrasound", "sonication"], ["khz", "frequency"], ["phenolic"]])
EX = [{"label": "Silva et al., 2020 — Ultrasound", "page": 2, "text": PAGES[1]},
      {"label": "Lee, 2019 — Sonication", "page": 4, "text": "80 kHz gave a lower phenolic yield."}]
res, _ = ai.answer_from_papers("openai", KEYS, "при какой частоте?", EX)
check("statements with valid sources only", [(p["text"][:10], p["sources"]) for p in res["points"]],
      [("Максимум ф", [0]), ("При 80 кГц", [1, 0])])
check("found", res["found"], True)
MODE["answer"] = "none"
res, _ = ai.answer_from_papers("openai", KEYS, "при какой частоте?", EX)
check("not found", (res["found"], res["points"], res["summary"][:10]), (False, [], "В этих ста"))
MODE["answer"] = "good"

# ---- the whole way through the program
app.SETTINGS_PATH = tmp / "settings.json"
app.INBOX = tmp / "inbox"
app.PDF_TEXT_CACHE = tmp / "pdf_text"
app.REPORT_PATH = tmp / "report.csv"
app.S.settings["pdf_dir"] = str(tmp / "pdf")
app.S.settings["ai_provider"] = "openai"
app.INBOX.mkdir()
(tmp / "pdf").mkdir()
DOIS = ["10.1016/j.ultsonch.2020.105", "10.3390/foods9010001", "10.1007/s11947-019-0001"]
(app.INBOX / "Выжимки.txt").write_text("\n".join(DOIS), encoding="utf-8")
data = rv.load_review(app.INBOX, "Выжимки.txt", DOIS)
for doi, (fam, year, title) in zip(DOIS, [("Silva", 2020, "Ultrasound-assisted extraction of grape pomace"),
                                          ("Lee", 2019, "Microwave drying of pomace"),
                                          ("Ivanov", 2019, "Not downloaded")]):
    data["records"][doi].update(title=title, year=year, authors=[{"family": fam}], found=True, decision="include")
rv.save_review(app.INBOX, data)
make_pdf(tmp / "pdf" / f"Elsevier_{pt.sanitized(DOIS[0])}.pdf", [[PAGES[0]], [PAGES[1]], ["References", "1. Smith 2010."]])
make_pdf(tmp / "pdf" / f"MDPI_{pt.sanitized(DOIS[1])}.pdf", [[PAGES[2]], ["Microwave and colour only."]])

check("empty question", "error" in app.handle_api("/api/ask", {"list": "Выжимки.txt", "scope": "included", "q": "?"}), True)
check("started", app.handle_api("/api/ask", {"list": "Выжимки.txt", "scope": "included",
                                              "q": "При какой частоте ультразвука выход фенолов наибольший?"}), {})
for _ in range(200):
    job = app.handle_api("/api/ask_state", {"list": "Выжимки.txt"})["job"]
    if not job["running"]:
        break
    time.sleep(0.05)
check("no error", job["error"], "")
check("read both PDFs", (job["done"], job["total"], job["no_pdf"]), (2, 2, 1))
sent = SEEN[-1]["messages"][-1]["content"]
check("the excerpt about the question went to the AI", "40 kHz and 30 min" in sent, True)
check("the microwave paper did not", "Microwave" in sent, False)
check("the references were cut", "Smith 2010" in sent, False)
ans = job["answer"]
check("answer", (ans["found"], ans["papers"], len(ans["points"])), (True, 1, 2))
check("points to the paper and page", ans["points"][0]["cites"], [{"doi": DOIS[0], "page": 2, "ref": "Silva, 2020"}])

# nothing about the question in the PDFs: an honest answer without a second AI call
before = len(SEEN)
app.handle_api("/api/ask", {"list": "Выжимки.txt", "scope": "included", "q": "Как хитозановая плёнка влияет на кислород?"})
for _ in range(200):
    job = app.handle_api("/api/ask_state", {"list": "Выжимки.txt"})["job"]
    if not job["running"]:
        break
    time.sleep(0.05)
check("only the words were asked", len(SEEN) - before, 1)
check("honest «not found»", (job["answer"]["found"], job["answer"]["points"]), (False, []))

# no key
app.S.settings["ai_provider"] = ""
(tmp / "keys.json").write_text("{}", encoding="utf-8")
check("no AI", "error" in app.handle_api("/api/ask", {"list": "Выжимки.txt", "scope": "included", "q": "Какая частота?"}), True)

srv.shutdown()
if fails:
    print("\n".join(fails))
    sys.exit(1)
print("ask tests: OK")
