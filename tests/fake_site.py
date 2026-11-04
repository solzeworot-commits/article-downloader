"""Fake publisher for testing: Cloudflare-like check page until a cookie is set."""
import io
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pypdf import PdfWriter

CHECK = b"""<!doctype html><html><head><title>Just a moment...</title></head><body>
<h1>Verify you are human</h1><p>Performance &amp; security by Cloudflare</p>
<button id="pass" onclick="document.cookie='human=1;path=/';location.reload()">I am human</button>
</body></html>"""


def pdf_bytes(n):
    w = PdfWriter()
    for _ in range(40):
        w.add_blank_page(width=300, height=300)
    w.add_metadata({"/Title": f"Fake article {n}"})
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="text/html; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        human = "human=1" in (self.headers.get("Cookie") or "")
        if not human:
            return self.send(403, CHECK)
        if self.path.startswith("/home"):
            return self.send(200, b"<html><head><title>Fake Publisher</title></head><body><h1>Fake Publisher</h1>"
                             b"<p>Access provided by Test University</p><p>Journals and books for testing.</p></body></html>")
        if self.path.startswith("/article/"):
            n = self.path.rsplit("/", 1)[-1]
            body = f"""<html><head><title>Fake article {n}</title>
<meta name="citation_title" content="Fake article {n}">
<meta name="citation_pdf_url" content="http://127.0.0.1:8000/pdf/{n}.pdf"></head>
<body><h1>Fake article {n}</h1><p>Abstract text of a fake article used for testing the downloader.</p>
<a href="/pdf/{n}.pdf">Download PDF</a></body></html>""".encode()
            return self.send(200, body)
        if self.path.startswith("/pdf/"):
            n = self.path.rsplit("/", 1)[-1].split(".")[0]
            return self.send(200, pdf_bytes(n), "application/pdf")
        self.send(404, b"not found")


ThreadingHTTPServer(("127.0.0.1", 8000), H).serve_forever()
