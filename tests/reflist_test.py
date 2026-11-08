"""«📄 Привести в порядок свой список» (v8.7): python reflist_test.py <portable/scripts>

The references of a messy list (numbers of three kinds, a wrapped line, a
heading, a DOI with brackets), the Crossref match checks, and the whole way
through the program with a Word file and fake Crossref, OpenAlex and ChatGPT:
found by DOI, found by search, not found; GOST in the order of the document,
APA alphabetically, the .docx, the DOI list for inbox and the AI formatting
of what was not found.
"""
import base64
import io
import json
import os
import sys
import tempfile
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

PORT = 8031
BASE = f"http://127.0.0.1:{PORT}"
tmp = Path(tempfile.mkdtemp(prefix="ad_reflist_"))
os.environ.update(ARTICLE_UI_NO_WINDOW="1", NO_PROXY="127.0.0.1,localhost",
                  ARTICLE_CROSSREF_URL=BASE + "/crossref", ARTICLE_OPENALEX_URL=BASE + "/works",
                  ARTICLE_EUROPEPMC_URL=BASE + "/epmc", ARTICLE_AI_OPENAI_URL=BASE + "/v1",
                  ARTICLE_KEYS_FILE=str(tmp / "keys.json"))
(tmp / "keys.json").write_text(json.dumps({"openai": "sk-test"}), encoding="utf-8")
sys.path.insert(0, sys.argv[1])
import app  # noqa: E402
import reflist as rl  # noqa: E402

app.SETTINGS_PATH = tmp / "settings.json"
app.INBOX = tmp / "inbox"
app.BIBLIO_DIR = tmp / "Список литературы"
fails = []


def check(name, got, want):
    if got != want:
        fails.append(f"{name}: {got!r} != {want!r}")


# ---- cutting a list into references
MESSY = """Список литературы
1. Alves F., Smith J. Ultrasound-assisted extraction of polyphenols from grape pomace // Food Chemistry. 2019.
Vol. 284. P. 11-17. https://doi.org/10.1016/j.foodchem.2019.01.001.
2) Petrova A., Ivanov I. Grape marc as a source of anthocyanins. Molecules, 2021, 26(5), 1234.
[3] Иванов И.И. Экстракция антоцианов из выжимок : монография. М. : Наука, 2018. 200 с.
4. Wakefield A.J. et al. Ileal-lymphoid-nodular hyperplasia. Lancet 1998; 351: 637-641. doi:10.1016/S0140-6736(97)11096-0.
"""
entries = rl.split_entries(MESSY)
check("four references", len(entries), 4)
check("wrapped line joined", entries[0].endswith("doi.org/10.1016/j.foodchem.2019.01.001."), True)
check("numbers cut", [e[:6] for e in entries], ["Alves ", "Petrov", "Иванов", "Wakefi"])
check("DOIs", [rl.find_doi(e) for e in entries],
      ["10.1016/j.foodchem.2019.01.001", "", "", "10.1016/s0140-6736(97)11096-0"])
check("one per paragraph", rl.split_entries("Smith J. On grapes and wine. 2020.\nLee K. On wine waste. 2019."),
      ["Smith J. On grapes and wine. 2020.", "Lee K. On wine waste. 2019."])
check("empty", rl.split_entries("References\n\n"), [])
good = {"title": "Grape marc as a source of anthocyanins", "year": "2021", "families": ["Petrova"]}
check("match", rl.matches(entries[1], good), True)
check("wrong year", rl.matches(entries[1], dict(good, year="2015")), False)
check("other title", rl.matches(entries[1], dict(good, title="Polyphenols of olive leaves: a review")), False)

# ---- fake services
GRAPE = {"DOI": "10.3390/molecules26051234", "title": ["Grape marc as a source of anthocyanins"],
         "author": [{"family": "Petrova", "given": "Anna"}, {"family": "Ivanov", "given": "Ivan"}],
         "issued": {"date-parts": [[2021]]}, "container-title": ["Molecules"], "volume": "26", "issue": "5", "page": "1234",
         "type": "journal-article"}
META = {
    "10.1016/j.foodchem.2019.01.001": {"DOI": "10.1016/j.foodchem.2019.01.001",
                                       "title": ["Ultrasound-assisted extraction of polyphenols from grape pomace"],
                                       "author": [{"family": "Alves", "given": "Fernanda"}, {"family": "Smith", "given": "John"}],
                                       "issued": {"date-parts": [[2019]]}, "container-title": ["Food Chemistry"],
                                       "volume": "284", "page": "11-17"},
    "10.3390/molecules26051234": GRAPE,
    "10.1016/s0140-6736(97)11096-0": {"DOI": "10.1016/S0140-6736(97)11096-0", "title": ["Ileal-lymphoid-nodular hyperplasia"],
                                      "author": [{"family": "Wakefield", "given": "A. J."}], "issued": {"date-parts": [[1998]]},
                                      "container-title": ["The Lancet"], "volume": "351", "issue": "9103", "page": "637-641"},
}
AI_SEEN = []


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
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if u.path == "/crossref":
            if "query.bibliographic" in q:
                text = q["query.bibliographic"]
                items = [GRAPE] if "Grape marc" in text else [{"DOI": "10.1/unrelated", "title": ["Something else entirely here"],
                                                              "issued": {"date-parts": [[2005]]}}]
                return self.send({"message": {"items": items}})
            dois = [d.split(":", 1)[1].lower() for d in unquote(q.get("filter", "")).split(",") if d]
            return self.send({"message": {"items": [META[d] for d in dois if d in META]}})
        if u.path == "/v1/models":
            return self.send({"data": [{"id": "gpt-5-mini"}]})
        return self.send({"results": [], "resultList": {"result": []}})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        AI_SEEN.append(body)
        out = {"references": [{"n": 1, "text": "Иванов, И. И. Экстракция антоцианов из выжимок : монография / И. И. Иванов. – Москва : Наука, 2018. – 200 с."}]}
        self.send({"choices": [{"index": 0, "message": {"role": "assistant", "content": json.dumps(out, ensure_ascii=False)}}],
                   "usage": {"prompt_tokens": 300, "completion_tokens": 80}})


srv = ThreadingHTTPServer(("127.0.0.1", PORT), Fake)
threading.Thread(target=srv.serve_forever, daemon=True).start()


def docx(paragraphs):
    xml = ('<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
           + "".join(f"<w:p><w:r><w:t>{p.replace('&', '&amp;').replace('<', '&lt;')}</w:t></w:r></w:p>" for p in paragraphs)
           + "</w:body></w:document>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", xml)
    return base64.b64encode(buf.getvalue()).decode()


def wait():
    for _ in range(200):
        view = app.handle_api("/api/reflist", {})
        if not view["job"].get("running"):
            return view
        time.sleep(0.05)
    fails.append("the list did not finish")
    return view


res = app.handle_api("/api/reflist_start", {"file_name": "Мой список.docx", "file_b64": docx(MESSY.splitlines()),
                                             "style": "gost", "order": "doc"})
check("started", res, {})
view = wait()
check("statuses", [r["status"] for r in view["items"]], ["doi", "search", "none", "doi"])
check("counts", view["counts"], {"doi": 2, "search": 1, "none": 1})
check("numbered as in the document", [r["label"] for r in view["items"]], ["1. ", "2. ", "3. ", "4. "])
check("GOST text", view["items"][0]["text"].startswith("Alves, F. Ultrasound-assisted extraction"), True)
check("found by search has its DOI", view["items"][1]["doi"], "10.3390/molecules26051234")
check("not found stays as written", view["items"][2]["text"], entries[2])
check("job has no text", "text" in view["job"] or "entries" in view["job"], False)

res = app.handle_api("/api/reflist_start", {"restyle": True, "style": "apa", "order": "alpha"})
view = wait()
check("APA, alphabetical", [r["text"][:7] for r in view["items"]], ["Alves, ", "Petrova", "Wakefie", "Иванов "])
check("APA has no numbers", {r["label"] for r in view["items"]}, {""})

saved = app.handle_api("/api/reflist_save", {"fmt": "docx"})
check("docx name", saved.get("name"), "Мой список (приведён в порядок).docx")
with zipfile.ZipFile(app.BIBLIO_DIR / saved["name"]) as z:
    check("docx has the list", "Petrova, A., &amp; Ivanov, I. (2021)" in z.read("word/document.xml").decode(), True)
inbox = app.handle_api("/api/reflist_to_inbox", {})
check("DOI list", (inbox.get("count"), sorted((app.INBOX / inbox["name"]).read_text(encoding="utf-8").split())),
      (3, ["10.1016/j.foodchem.2019.01.001", "10.1016/s0140-6736(97)11096-0", "10.3390/molecules26051234"]))

app.handle_api("/api/reflist_start", {"restyle": True, "style": "gost", "order": "doc"})
wait()
check("AI starts", app.handle_api("/api/reflist_ai", {}), {})
view = wait()
check("AI formatted the one not found", (view["items"][2]["status"], view["items"][2]["text"].startswith("Иванов, И. И. Экстракция")), ("ai", True))
check("AI saw only that reference", ("Наука, 2018" in AI_SEEN[-1]["messages"][-1]["content"],
                                     "Grape marc" in AI_SEEN[-1]["messages"][-1]["content"]), (True, False))
check("nothing left for AI", app.handle_api("/api/reflist_ai", {}), {"nothing": True})
check("not a list", "error" in app.handle_api("/api/reflist_start", {"text": "привет", "style": "gost"}), True)
check(".doc refused", "error" in app.handle_api("/api/reflist_start", {"file_name": "a.doc", "file_b64": "AAAA"}), True)

srv.shutdown()
if fails:
    print("\n".join(fails))
    sys.exit(1)
print("reflist tests: OK")
