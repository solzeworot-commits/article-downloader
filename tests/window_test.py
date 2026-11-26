"""The size of the program window (v8.1): python window_test.py <portable/scripts>"""
import os
import sys
import tempfile
from pathlib import Path

os.environ["ARTICLE_UI_NO_WINDOW"] = "1"
sys.path.insert(0, sys.argv[1])
import app  # noqa: E402

app.SETTINGS_PATH = Path(tempfile.mkdtemp(prefix="ad_window_")) / "settings.json"   # not the program's own file

S = app.S.settings
S["window"] = None
S["screen"] = {"w": 1366, "h": 728, "x": 0, "y": 0}          # a laptop: nearly the whole screen
assert app.window_geometry() == (1326, 712, 20, 8), app.window_geometry()
S["screen"] = {"w": 2560, "h": 1400, "x": 0, "y": 0}         # a big monitor: no wider than 1400, centred
assert app.window_geometry() == (1400, 1384, 580, 8), app.window_geometry()
S["window"] = {"w": 1000, "h": 700}                          # the size the user left it at
assert app.window_geometry() == (1000, 700, 780, 350), app.window_geometry()
S["window"] = {"w": 3000, "h": 2000}                         # never larger than the screen
assert app.window_geometry()[:2] == (2560, 1400)
S["window"], S["screen"] = None, {"w": 1920, "h": 1040, "x": -1920, "y": 0}   # a monitor on the left
assert app.window_geometry() == (1400, 1024, -1660, 8), app.window_geometry()
app.handle_api("/api/window_info", {"screen": {"w": 1600, "h": 860, "x": 0, "y": 0}})
assert S["screen"] == {"w": 1600, "h": 860, "x": 0, "y": 0}
app.handle_api("/api/window_info", {"window": {"w": 300, "h": 200}})   # too small: ignored
assert S["window"] is None
app.handle_api("/api/window_info", {"window": {"w": 1200, "h": 800}})
assert S["window"] == {"w": 1200, "h": 800} and app.window_geometry() == (1200, 800, 200, 30)
if os.name == "nt":
    import subprocess
    import threading
    import time
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from unittest.mock import patch

    import article_downloader as ad
    import tray

    class TestPage(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"<title>ArticleDownloader update test</title>")

        def log_message(self, *_):
            pass

    def wait_for(test, seconds=8):
        limit = time.monotonic() + seconds
        while time.monotonic() < limit:
            if test():
                return
            time.sleep(.1)
        assert test(), "window state did not change"

    # Old and new binaries can be started in either order. A new server
    # may only be created once the other process releases its listening port.
    for first, second in ((ThreadingHTTPServer, app.AppServer),
                          (app.AppServer, ThreadingHTTPServer), (app.AppServer, app.AppServer)):
        with first(("127.0.0.1", 0), TestPage) as server:
            addr = server.server_address
            try:
                duplicate = second(addr, TestPage)
            except OSError:
                pass
            else:
                duplicate.server_close()
                raise AssertionError("two app servers share the port")
        with second(addr, TestPage):
            pass

    exe = ad.find_browser_exe("edge") or ad.find_browser_exe("chrome")
    assert exe
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as profile, app.AppServer(("127.0.0.1", 0), TestPage) as server:
        port = server.server_port
        threading.Thread(target=server.serve_forever, daemon=True).start()
        with patch.object(tray, "TITLES", ("ArticleDownloader update test",)):
            processes = []

            def launch(path):
                process = subprocess.Popen([exe, "--user-data-dir=" + profile,
                                  "--no-first-run", "--no-default-browser-check", "--disable-background-mode",
                                  "--window-position=-20000,-20000",
                                  f"--app=http://127.0.0.1:{port}{path}"],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                processes.append(process)

            try:
                launch("/?t=old-token#download")
                wait_for(lambda: len(tray._windows(None)) == 1)
                old = tray._windows(None)[0]
                launch("/article")
                wait_for(lambda: len(tray._windows(None)) == 2)
                other = next(h for h in tray._windows(None) if h != old)
                tray.close_program_windows(port, app.run_powershell)
                wait_for(lambda: not tray.user32.IsWindow(old))
                assert tray.user32.IsWindow(other), "unrelated page with the same title was closed"
                launch("/?t=new-token")
                wait_for(lambda: len(tray._windows(None)) == 2)
                new = next(h for h in tray._windows(None) if h != other)
                tray.user32.ShowWindowAsync(new, tray.SW_HIDE)
                wait_for(lambda: not tray.user32.IsWindowVisible(new))
                tray.close_program_windows(port, app.run_powershell)
                wait_for(lambda: not tray.user32.IsWindow(new))
                assert tray.user32.IsWindow(other)
            finally:
                for h in tray._windows(None):
                    tray.user32.PostMessageW(h, tray.WM_CLOSE, 0, 0)
                server.shutdown()
                for process in processes:
                    process.wait(timeout=10)

print("window tests: OK")
