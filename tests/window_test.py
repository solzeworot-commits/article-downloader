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
print("window tests: OK")
