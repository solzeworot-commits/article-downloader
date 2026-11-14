"""Supplementary materials (v8.9): python supplementary_test.py <portable/scripts>

A fake article page with the kinds of supplementary links publishers use
(ACS «_si_001», Springer ESM with a file name in Content-Disposition,
Elsevier «mmc2.docx», MDPI «…/s1» without an extension, a «Supplementary
Data» link found by its text) and traps: a video, a picture, a journal page,
a 60 MB file (HEAD only), a closed file, the article itself under another
name, a repeated link. Opened in a real browser (Edge / Chrome, or
ARTICLE_TEST_BROWSER) the way the downloader does it. Also: a page without
supplementary files, a verification page, the tick off.
"""
import io
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

tmp = Path(tempfile.mkdtemp(prefix="ad_supp_"))
os.environ.update(ARTICLE_LOG_DIR=str(tmp / "logs"), ARTICLE_PDF_DIR=str(tmp / "pdf"))
os.environ.pop("ARTICLE_JOB_DEADLINE", None)
sys.path.insert(0, sys.argv[1])
import article_downloader as ad  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402
from pypdf import PdfWriter  # noqa: E402

ad.SUPP_PAUSE_SEC = 0
PORT = 8042
BASE = f"http://127.0.0.1:{PORT}"


def pdf(title):
    w = PdfWriter()
    w.add_blank_page(width=200, height=200)
    w.add_metadata({"/Title": title})
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


ARTICLE = pdf("The article")
FILES = {   # path: (content type, body, extra headers)
    "/pdf/1.pdf": ("application/pdf", ARTICLE, {}),
    "/doi/suppl/10.9999/x1/suppl_file/x1_si_001.pdf": ("application/pdf", pdf("SI 1"), {}),
    "/esm/art1/MediaObjects/1_MOESM1_ESM.xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                                b"PK\x03\x04 xlsx " * 40,
                                                {"Content-Disposition": 'attachment; filename="Table S1.xlsx"'}),
    "/content/1-s2.0-S0308814620300000-mmc2.docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                                                    b"PK\x03\x04 docx " * 40, {}),
    "/article/10.3390/x1/s1": ("application/zip", b"PK\x03\x04 zip " * 40, {}),
    "/files/raw-data.csv": ("text/csv", b"sample;value\n" * 40, {}),
    "/journal/supplements": ("text/html; charset=utf-8", b"<html><body>Journal supplements</body></html>", {}),
    "/suppl/1/same_si_005.pdf": ("application/pdf", ARTICLE, {}),
}
HUGE = "/suppl/1/huge_si_004.zip"
LOCKED = "/suppl/1/locked_si_006.pdf"
PAGE = f"""<html><head><title>A fake article</title></head><body><h1>A fake article</h1>
<a href="/pdf/1.pdf">Download PDF</a>
<a href="/doi/suppl/10.9999/x1/suppl_file/x1_si_001.pdf">Supporting Information</a>
<a href="/esm/art1/MediaObjects/1_MOESM1_ESM.xlsx">ESM 1</a>
<a href="/content/1-s2.0-S0308814620300000-mmc2.docx">Download: Download Word document (25KB)</a>
<a href="/article/10.3390/x1/s1">Download Supplementary Material</a>
<a href="/files/raw-data.csv">Supplementary Data 1</a>
<a href="/suppl/1/movie_si_002.mp4">Video S1</a>
<a href="/suppl/fig_si_003.png">Figure S1</a>
<a href="/journal/supplements">Journal supplements</a>
<a href="{HUGE}">Supporting archive</a>
<a href="{LOCKED}">Supporting information 6</a>
<a href="/suppl/1/same_si_005.pdf">Supporting PDF</a>
<a href="/doi/suppl/10.9999/x1/suppl_file/x1_si_001.pdf#page=1">Supporting Information (again)</a>
<a href="/about">About the journal</a>
</body></html>""".encode()
EMPTY = b"<html><head><title>No extras</title></head><body><h1>An article</h1><a href='/pdf/1.pdf'>PDF</a></body></html>"
CHECK = (b"<!doctype html><html><head><title>Just a moment...</title></head><body><h1>Verify you are human</h1>"
         b"<p>Performance &amp; security by Cloudflare</p><a href='/doi/suppl/x_si_001.pdf'>Supporting Information</a>"
         b"</body></html>")
GETS = []


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def answer(self, with_body):
        path = self.path.split("#")[0]
        if with_body:
            GETS.append(path)
        pages = {"/article/1": PAGE, "/article/2": EMPTY, "/check": CHECK}
        if path in pages:
            ctype, body, extra = "text/html; charset=utf-8", pages[path], {}
        elif path == HUGE:
            ctype, body, extra = "application/zip", b"", {}
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(60 * 1024 * 1024))
            self.end_headers()
            return
        elif path == LOCKED:
            self.send_response(403)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        elif path in FILES:
            ctype, body, extra = FILES[path]
        else:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in extra.items():
            self.send_header(k, v)
        self.end_headers()
        if with_body:
            self.wfile.write(body)

    def do_GET(self):
        self.answer(True)

    def do_HEAD(self):
        self.answer(False)


fails = []


def check(name, got, want):
    if got != want:
        fails.append(f"{name}: {got!r} != {want!r}")


def launch(p):
    exe = os.environ.get("ARTICLE_TEST_BROWSER")
    if exe:
        return p.chromium.launch(executable_path=exe, headless=True, args=["--no-sandbox"])
    for channel in ("msedge", "chrome"):
        try:
            return p.chromium.launch(channel=channel, headless=True)
        except Exception:
            pass
    for kind in ("edge", "chrome", "yandex"):
        exe = ad.find_browser_exe(kind)
        if exe:
            return p.chromium.launch(executable_path=exe, headless=True)
    return None


srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
pdf_dir = tmp / "pdf"
pdf_dir.mkdir(parents=True, exist_ok=True)
article = pdf_dir / "Fake_10.9999_x1.pdf"
article.write_bytes(ARTICLE)

with sync_playwright() as p:
    browser = launch(p)
    if browser is None:
        print("supplementary test: no Edge or Chrome here")
        sys.exit(1 if os.environ.get("CI") else 0)
    context = browser.new_context()

    page = context.new_page()
    page.goto(BASE + "/article/1")
    links = [u.replace(BASE, "") for u in ad.supplement_links(page)]
    page.close()
    check("links", links, ["/doi/suppl/10.9999/x1/suppl_file/x1_si_001.pdf", "/esm/art1/MediaObjects/1_MOESM1_ESM.xlsx",
                           "/content/1-s2.0-S0308814620300000-mmc2.docx", "/article/10.3390/x1/s1",
                           "/files/raw-data.csv", "/journal/supplements", HUGE, LOCKED, "/suppl/1/same_si_005.pdf"])

    count, folder = ad.download_supplements(context, BASE + "/article/1", article)
    check("saved", count, 5)
    check("folder", folder, pdf_dir / "Fake_10.9999_x1_supplementary")
    names = sorted(x.name for x in folder.iterdir()) if folder else []
    check("files", names, ["1-s2.0-S0308814620300000-mmc2.docx", "Table_S1.xlsx", "raw-data.csv", "s1.zip",
                           "x1_si_001.pdf"])
    if folder:
        check("bytes", (folder / "x1_si_001.pdf").read_bytes(), FILES["/doi/suppl/10.9999/x1/suppl_file/x1_si_001.pdf"][1])
    check("60 MB file not downloaded", HUGE in GETS, False)
    check("the video and the picture not asked for", any(x.endswith((".mp4", ".png")) for x in GETS), False)
    check("the article PDF next to it untouched", sorted(x.name for x in pdf_dir.iterdir()),
          ["Fake_10.9999_x1.pdf", "Fake_10.9999_x1_supplementary"])

    other = pdf_dir / "Other.pdf"
    other.write_bytes(pdf("Other"))
    check("no extras", ad.download_supplements(context, BASE + "/article/2", other), (0, None))
    check("no empty folder", (pdf_dir / "Other_supplementary").exists(), False)
    check("verification page left alone", ad.download_supplements(context, BASE + "/check", other), (0, None))
    os.environ["ARTICLE_SUPPLEMENTARY"] = "0"
    check("tick off", ad.download_supplements(context, BASE + "/article/1", other), (0, None))
    os.environ.pop("ARTICLE_SUPPLEMENTARY")
    browser.close()

srv.shutdown()
if fails:
    print("\n".join(fails))
    sys.exit(1)
print("supplementary tests: OK")
