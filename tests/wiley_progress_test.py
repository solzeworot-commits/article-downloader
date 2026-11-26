"""Slow Wiley article, popup viewer and PDF transfer in a real Chromium browser."""
import io
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from playwright.sync_api import sync_playwright
from pypdf import PdfWriter

sys.path.insert(0, sys.argv[1])
DOI = "10.1029/test"
payload = io.BytesIO()
writer = PdfWriter()
writer.add_blank_page(width=300, height=300)
writer.add_metadata({"/Subject": "test article " * 30000})
writer.write(payload)
pdf = payload.getvalue()


class Site(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        visits.append(self.path)
        attachment = False
        if self.path == "/asynchronous":
            data = b"<body><script>fetch('/api/content').then(r=>r.text()).then(t=>document.body.id='ready')</script>"
        elif self.path == "/api/content":
            time.sleep(3)  # No first byte before the old idle cutoff.
            data = b" " * 12000
        elif "/doi/abs/" in self.path:
            data = (b"<html><body><h1>Test article</h1>" + b" " * 12000 +
                    b'<a target="_blank" href="/doi/epdf/10.1029/test">PDF</a></body></html>')
        elif "/doi/epdf/" in self.path:
            data = (b"<html><body>Opening viewer" + b" " * 12000 +
                    b"""<button id="new-download-btn" onclick="location.href='/file'">Download</button>""")
        elif self.path in ("/file", "/direct"):
            data, attachment = pdf, True
        elif self.path == "/empty":
            data = b"<html><body>Empty page</body></html>"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/pdf" if attachment else "text/html")
        self.send_header("Content-Length", str(len(data)))
        if attachment:
            self.send_header("Content-Disposition", 'attachment; filename="article.pdf"')
        self.end_headers()
        chunk = max(2048, len(data) // 6)
        for offset in range(0, len(data), chunk):
            if offset:
                time.sleep(0.65)
            self.wfile.write(data[offset:offset + chunk])
            self.wfile.flush()


visits = []
with tempfile.TemporaryDirectory() as tmp:
    os.environ.update(ARTICLE_LOG_DIR=tmp, ARTICLE_PDF_DIR=tmp, ARTICLE_SUPPLEMENTARY="0")
    import article_downloader as ad

    server = ThreadingHTTPServer(("127.0.0.1", 0), Site)
    address = "http://127.0.0.1:" + str(server.server_port)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as p:
            with socket.socket() as port:
                port.bind(("127.0.0.1", 0))
                cdp_port = port.getsockname()[1]
            exe = ad.find_browser_exe("chrome") or ad.find_browser_exe("edge")
            browser = p.chromium.launch(executable_path=exe, headless=True,
                                        args=[f"--remote-debugging-port={cdp_port}"])
            remote = p.chromium.connect_over_cdp(f"http://127.0.0.1:{cdp_port}")
            context = remote.contexts[0]
            existing = context.new_page()
            # Only the publisher hostname is substituted. DOM, bytes, popups, downloads
            # and page cleanup use the production path over a real browser connection.
            with (patch.object(ad.wiley_route, "article_doi", return_value=DOI),
                  patch.object(ad, "WILEY_IDLE_SEC", 2),
                  patch.object(ad, "BROWSER_PORT", cdp_port),
                  patch.dict(os.environ, ARTICLE_JOB_DEADLINE=str(time.time() + 80))):
                with patch.dict(os.environ, ARTICLE_JOB_TIMEOUT_SEC="8", ARTICLE_JOB_DEADLINE=str(time.time() + 8)):
                    path = ad.download_wiley(context, DOI, DOI, address + "/doi/" + DOI)
                    # A budget shorter than the whole job also covers native save_as.
                    assert float(os.environ["ARTICLE_JOB_DEADLINE"]) > time.time()
                assert path.read_bytes() == pdf
                assert not existing.is_closed() and len(context.pages) == 1
                assert "/file" in visits and not any("/pdfdirect/" in u for u in visits), visits

                direct = Path(tmp) / "direct.pdf"
                watch = ad.LoginServiceWatch(context)
                try:
                    with patch.dict(os.environ, ARTICLE_JOB_TIMEOUT_SEC="3",
                                    ARTICLE_JOB_DEADLINE=str(time.time() + 3)):
                        assert ad.capture_pdf_with_fetch(context, address + "/direct", direct, 1,
                                                         "Wiley PDF", wiley_watch=watch)
                        assert float(os.environ["ARTICLE_JOB_DEADLINE"]) > time.time()
                finally:
                    watch.close()
                assert direct.read_bytes() == pdf and visits.count("/direct") == 1
                assert not existing.is_closed() and len(context.pages) == 1

                existing.goto(address + "/empty")
                activity = ad.WileyActivity()
                activity.attach(existing)
                started = time.monotonic()
                try:
                    assert activity.wait(lambda: existing, lambda: None, lambda: None, "idle test") is None
                    assert 2 <= time.monotonic() - started < 10
                finally:
                    activity.close()
                activity = ad.WileyActivity()
                activity.attach(existing)
                try:
                    with patch.dict(os.environ, ARTICLE_JOB_TIMEOUT_SEC="8",
                                    ARTICLE_JOB_DEADLINE=str(time.time() + 8)):
                        existing.goto(address + "/asynchronous")
                        assert activity.wait(lambda: existing, lambda: existing.locator("body#ready").count(),
                                             lambda: None, "viewer API response")
                finally:
                    activity.close()

                # Leave the user's article open on a timeout, even on the next attach.
                with patch.object(ad.WileyActivity, "wait", side_effect=ad.DownloadError("test deadline")):
                    try:
                        ad.download_wiley(context, DOI, DOI, address + "/doi/" + DOI)
                    except ad.DownloadError:
                        pass
                    else:
                        raise AssertionError("Expired article unexpectedly succeeded")
                kept = [tab for tab in context.pages if "/doi/abs/" in tab.url]
                assert len(kept) == 1, [tab.url for tab in context.pages]
                with (patch.object(ad, "WILEY_BROWSER_PORT", cdp_port),
                      patch.object(ad, "CDP_MAX_IDLE_TABS", 1)):
                    ad.connect_cdp_browser(p, f"http://127.0.0.1:{cdp_port}", lambda: None, "Wiley Chrome")
                assert not kept[0].is_closed() and not existing.is_closed()
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
print("Slow Wiley article, viewer, completed PDF and idle timeout: OK")
