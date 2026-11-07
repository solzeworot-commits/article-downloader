"""Search in the text of the PDFs and the draft outline of a review (v8.4):
python pdf_search_test.py <portable/scripts>

pdf_pages(): the text page by page, words hyphenated at a line end joined, the
reference list (and the pages after it) cut off, the pages cached on disk.
search_pages(): all the words close together, the page numbers, the snippet,
a phrase in quotes. ai.outline() with a fake ChatGPT: the sections, the paper
numbers mapped to the list (wrong ones dropped), the gaps; the .docx of the
outline opens as a Word document.
"""
import json
import os
import sys
import tempfile
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = 8027
os.environ.update(ARTICLE_AI_OPENAI_URL=f"http://127.0.0.1:{PORT}/v1", NO_PROXY="127.0.0.1,localhost")
sys.path.insert(0, sys.argv[1])
import ai_helper as ai  # noqa: E402
import cite_styles as cs  # noqa: E402
import pdf_table as pt  # noqa: E402

fails = []


def check(name, got, want):
    if got != want:
        fails.append(f"{name}: {got!r} != {want!r}")


def make_pdf(path: Path, pages: list[list[str]]):
    """A PDF with one Helvetica text stream per page, one line per Tj."""
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


tmp = Path(tempfile.mkdtemp(prefix="ad_find_"))
pdf = tmp / "a.pdf"
make_pdf(pdf, [
    ["Ultrasound-assisted extraction of polyphenols", "Introduction", "Grape pomace is a by-product of wine making."],
    ["Methods", "The extrac-", "tion was performed with ultrasound at 40 kHz", "for 30 min in 50% ethanol."],
    ["Results", "Deep eutectic solvents gave the best yield.", "Ultrasound alone was weaker."],
    ["References", "1. Smith. Ultrasound 40 kHz extraction of grapes. 2001."],
    ["Supplementary: ultrasound 40 kHz settings"],
])
cache = tmp / "cache"
pages = pt.pdf_pages(pdf, cache)
check("pages kept", len(pages), 4)                 # the reference list page cut to nothing, the page after it gone
check("references cut", any("Smith" in p or "Supplementary" in p for p in pages), False)
check("hyphenation joined", "extraction was performed" in pages[1], True)
check("cached", len(list(cache.glob("*.json"))), 1)
cached_file = next(cache.glob("*.json"))
cached_file.write_text(json.dumps(["from the cache"]), encoding="utf-8")
check("read from the cache", pt.pdf_pages(pdf, cache), ["from the cache"])
check("missing file", pt.pdf_pages(tmp / "none.pdf", cache), [])

check("terms", pt.query_terms('Ultrasound 40 kHz "deep eutectic" «полифенолы винограда», a'),
      ["ultrasound", "40", "khz", "deep eutectic", "полифенолы винограда"])
hits, count = pt.search_pages(pages, pt.query_terms("ultrasound 40 kHz"))
check("one place", (count, [h["page"] for h in hits]), (1, [2]))
check("snippet", "ultrasound at 40 kHz" in hits[0]["snippet"], True)
hits, count = pt.search_pages(pages, pt.query_terms('"deep eutectic" yield'))
check("phrase", (count, [h["page"] for h in hits]), (1, [3]))
check("not near", pt.search_pages(["ultrasound " + "x " * 400 + " khz"], ["ultrasound", "khz"])[1], 0)
check("part of a word", pt.search_pages(pages, ["extract"])[1] >= 2, True)
check("nothing", pt.search_pages(pages, ["microwave"]), ([], 0))
check("max hits", len(pt.search_pages(["a b. " * 600], ["a"], max_hits=3)[0]), 3)


# ---- the outline, with a fake ChatGPT
ANSWER = {"sections": [
    {"title": "Методы экстракции", "about": "Сравнение ультразвука и растворителей.", "papers": [1, 3, 3, 9]},
    {"title": "Выход полифенолов", "about": "Что влияет на выход.", "papers": [2, "x"]},
    {"title": "", "about": "пустой", "papers": [1]}],
    "gaps": "Нет данных о масштабировании."}
SEEN = []


class Fake(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, data):
        body = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.send({"data": [{"id": "gpt-5-mini"}]})

    def do_POST(self):
        SEEN.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
        self.send({"choices": [{"index": 0, "message": {"role": "assistant", "content": json.dumps(ANSWER, ensure_ascii=False)}}],
                   "usage": {"prompt_tokens": 1000, "completion_tokens": 200}})


srv = ThreadingHTTPServer(("127.0.0.1", PORT), Fake)
threading.Thread(target=srv.serve_forever, daemon=True).start()
papers = [{"title": "Ultrasound extraction", "year": "2020", "abstract": "Ultrasound at 40 kHz."},
          {"title": "Yield of polyphenols", "year": "2021", "abstract": "", "summary": "Объект: выжимки; Результат: 45 мг/г"},
          {"title": "Deep eutectic solvents", "year": "2022", "abstract": "DES."}]
plan, cost = ai.outline("openai", {"openai": "sk-test"}, "Экстракция полифенолов из виноградных выжимок", papers, "ru")
check("sections", [(s["title"], s["papers"]) for s in plan["sections"]],
      [("Методы экстракции", [0, 2]), ("Выход полифенолов", [1])])
check("gaps", plan["gaps"], "Нет данных о масштабировании.")
user = SEEN[-1]["messages"][-1]["content"]
check("topic sent", "Экстракция полифенолов" in user, True)
check("summary preferred to abstract", "45 мг/г" in user, True)
check("schema asked", SEEN[-1].get("response_format", {}).get("type"), "json_schema")
try:
    ai.outline("openai", {"openai": "sk-test"}, "", papers[:1], "ru")
    fails.append("one paper: no error")
except ai.AIError:
    pass

docx = tmp / "plan.docx"
cs.outline_docx(docx, [("title", "Черновик плана обзора: тема"), ("note", "Черновик ИИ."), ("h", "1. Методы"),
                       ("p", "О чём раздел."), ("li", "Silva et al., 2020 — Ultrasound & DES <test>")])
with zipfile.ZipFile(docx) as z:
    doc = z.read("word/document.xml").decode("utf-8")
check("docx text", all(x in doc for x in ("Черновик плана обзора: тема", "1. Методы", "• Silva et al., 2020 — Ultrasound &amp; DES &lt;test&gt;")), True)

srv.shutdown()
if fails:
    print("\n".join(fails))
    sys.exit(1)
print("pdf search and outline tests: OK")
