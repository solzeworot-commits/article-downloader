"""Wiley's login service cut off (v9.2.2): python wiley_login_test.py <portable/scripts>

From some networks wiley.scienceconnect.io cannot be reached (22.11.2026, from Russia:
cut by the name). LoginServiceWatch notes a failed top-level visit there and
check() ends the article at once as «нет доступа» (NoAccess, no retries). Only the
page's own navigation counts: a picture or a frame from that host failing to load,
a top-level visit to another host failing, or a slow visit there that the program
itself stops (net::ERR_ABORTED: the service answers, only slowly), does not. In a real browser (Edge /
Chrome, or ARTICLE_TEST_BROWSER); «localhost» stands in for the login service; the
browser does not connect to port 9 at all (net::ERR_UNSAFE_PORT): a failed visit.
"""
import os
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

tmp = Path(tempfile.mkdtemp(prefix="ad_wiley_"))
os.environ.update(ARTICLE_LOG_DIR=str(tmp / "logs"), ARTICLE_PDF_DIR=str(tmp / "pdf"))
sys.path.insert(0, sys.argv[1])
import article_downloader as ad  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

PORT = 8046
CUT = "http://localhost:9"          # a visit there always fails: like a login service cut off
PAGE = f"""<html><body><h1>An article page</h1>
<img src="{CUT}/widget.png"><iframe src="{CUT}/login-frame"></iframe>
<a id="pdf" href="{CUT}/login?redirect=pdf">PDF</a></body></html>""".encode()


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.startswith("/slow-login"):
            time.sleep(6)               # a login service that answers, only slowly
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(PAGE)))
            self.end_headers()
            self.wfile.write(PAGE)
        except OSError:
            pass                        # the browser has already gone elsewhere


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


def blocked(watch):
    try:
        watch.check()
        return False
    except ad.NoAccess as exc:
        return str(exc)


srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
check("the real login service is watched", ad.LOGIN_SERVICE_HOSTS, ("scienceconnect.io",))
ad.LOGIN_SERVICE_HOSTS = ("localhost",)

with sync_playwright() as p:
    browser = launch(p)
    if browser is None:
        print("wiley login test: no Edge or Chrome here")
        sys.exit(1 if os.environ.get("CI") else 0)
    context = browser.new_context()
    page = context.new_page()
    watch = ad.LoginServiceWatch(context)
    failed = []
    context.on("requestfailed", lambda r: failed.append((r.url, str(r.failure or ""))))

    # The article page loads; a picture and a frame from the login host fail: not a reason.
    page.goto(f"http://127.0.0.1:{PORT}/article")
    page.wait_for_timeout(1500)
    check("a picture or a frame from there does not count", blocked(watch), False)

    # A top-level visit to another host that fails: not the login service.
    try:
        page.goto("http://127.0.0.1:9/elsewhere", timeout=10000)
    except Exception:
        pass
    check("another host does not count", blocked(watch), False)

    # A slow visit there that the program itself stops by going elsewhere: the service answers.
    page = context.new_page()
    page.goto(f"http://127.0.0.1:{PORT}/article")
    page.evaluate(f"location.href = 'http://localhost:{PORT}/slow-login'")
    page.wait_for_timeout(800)
    page.goto(f"http://127.0.0.1:{PORT}/article")
    page.wait_for_timeout(800)
    check("the slow visit was stopped (net::ERR_ABORTED)",
          any("/slow-login" in u and "ERR_ABORTED" in f for u, f in failed), True)
    check("a slow visit stopped by the program does not count", blocked(watch), False)

    # The PDF link sends the page to the login service, and the connection fails: stop at once.
    page = context.new_page()           # a fresh tab: the last one is still on its error page
    page.goto(f"http://127.0.0.1:{PORT}/article")
    try:
        page.click("#pdf", timeout=10000)
        page.wait_for_timeout(1500)
    except Exception:
        pass
    message = blocked(watch)
    check("the login service cut off: no access", bool(message) and message.startswith("WILEY_LOGIN_BLOCKED"), True)
    check("the reason names the service", "wiley.scienceconnect.io" in (message or ""), True)

    # Not watched any more after close().
    other = ad.LoginServiceWatch(context)
    other.close()
    page = context.new_page()
    try:
        page.goto(f"{CUT}/again", timeout=10000)
    except Exception:
        pass
    check("closed: nothing noted", other.url, "")
    watch.close()
    browser.close()

# A blocked login is «нет доступа»: process_one hands NoAccess on without its retries.
check("no access, not a failure to retry", issubclass(ad.NoAccess, Exception) and not ad._transient_navigation_error(
    ad.NoAccess(ad.LOGIN_BLOCKED_MESSAGE)), True)

srv.shutdown()
if fails:
    print("\n".join(fails))
    sys.exit(1)
print("wiley login tests: OK")
