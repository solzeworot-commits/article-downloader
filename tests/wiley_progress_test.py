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
writer.add_metadata({"/Subject": "test article " * 3000})
writer.write(payload)
pdf = payload.getvalue()


class Site(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        visits.append(self.path)
        attachment = False
        if "/doi/abs/" in self.path:
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
                path = ad.download_wiley(context, DOI, DOI, address + "/doi/" + DOI)
                assert path.read_bytes() == pdf
                assert not existing.is_closed() and len(context.pages) == 1
                assert "/file" in visits and not any("/pdfdirect/" in u for u in visits), visits

                direct = Path(tmp) / "direct.pdf"
                watch = ad.LoginServiceWatch(context)
                try:
                    assert ad.capture_pdf_with_fetch(context, address + "/direct", direct, 1,
                                                     "Wiley PDF", wiley_watch=watch)
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
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
print("Slow Wiley article, viewer, completed PDF and idle timeout: OK")
