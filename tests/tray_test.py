"""The icon by the clock (v8.8.1): python tray_test.py <portable/scripts>

Everywhere: the tooltip (what the program is doing), the «здесь, у часов»
notification only once, the setting, /api/hello without a window in the tray.
On Windows also for real: a stand-in for the program window (class
Chrome_WidgetWin_1, title «Загрузчик статей») leaves the taskbar when it is
minimized, comes back on a click and stays back; with the setting off it stays
on the taskbar; a second start of the program (/api/hello) brings the window
back from the tray; a closed window: the click opens a new one; stop() ends it.
Whether the icon itself appears depends on the session having a taskbar (the
CI one may not): printed, not checked.
"""
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

tmp = Path(tempfile.mkdtemp(prefix="ad_tray_"))
os.environ.update(ARTICLE_UI_NO_WINDOW="1", ARTICLE_KEYS_FILE=str(tmp / "keys.json"))
sys.path.insert(0, sys.argv[1])
import app  # noqa: E402
import tray  # noqa: E402

app.SETTINGS_PATH = tmp / "settings.json"
fails = []


def check(name, got, want):
    if got != want:
        fails.append(f"{name}: {got!r} != {want!r}")


def wait(cond, sec=6.0):
    end = time.time() + sec
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.1)
    return bool(cond())


# ---- the tooltip
app.S.items, app.S.phase, app.S.captcha_now = [], "idle", False
check("tip: idle", app.tray_tip(), f"Загрузчик статей {app.APP_VERSION}")
app.S.items = [{"id": 1, "query": "10.1/a", "status": "success"}, {"id": 2, "query": "10.1/b", "status": "pending"},
               {"id": 3, "query": "10.1/c", "status": "running"}]
app.S.phase = "downloading"
check("tip: downloading", app.tray_tip().splitlines()[1], "Скачиваю: готово 1, осталось 2")
app.S.captcha_now = True
check("tip: captcha", app.tray_tip().splitlines()[1].startswith("Капча"), True)
app.S.items, app.S.phase, app.S.captcha_now = [], "idle", False

# ---- «здесь, у часов» only the first time
app.S.settings["tray_hint_shown"] = False
sent = []
real_notify = tray.notify
tray.notify = lambda title, text: sent.append(title)
app.tray_parked()
app.tray_parked()
tray.notify = real_notify
check("hint once", len(sent), 1)
check("hint remembered", json.loads(app.SETTINGS_PATH.read_text(encoding="utf-8"))["tray_hint_shown"], True)

# ---- the setting
app.update_settings({"minimize_to_tray": False})
check("setting off", app.S.settings["minimize_to_tray"], False)
app.update_settings({"minimize_to_tray": True})
check("setting on", app.S.settings["minimize_to_tray"], True)

# ---- a second start with no window in the tray: the new copy opens a window itself
check("hello", app.handle_api("/api/hello", {}), {"added": 0, "shown": False})

if not tray.IS_WIN:
    check("no icon on this system", tray.start(None), False)
else:
    import ctypes
    from ctypes import wintypes as W

    u = tray.user32
    def_proc = tray.WNDPROC(lambda h, m, w, l: u.DefWindowProcW(h, m, w, l))
    win, ready = {}, threading.Event()

    def stand_in():
        hinst = tray.kernel32.GetModuleHandleW(None)
        wc = tray.WNDCLASSW()
        wc.lpfnWndProc, wc.hInstance, wc.lpszClassName = def_proc, hinst, tray.WINDOW_CLASS
        u.RegisterClassW(ctypes.byref(wc))
        win["hwnd"] = u.CreateWindowExW(0, tray.WINDOW_CLASS, tray.TITLES[0], 0x00CF0000 | 0x10000000,
                                        100, 100, 420, 300, None, None, hinst, None)
        ready.set()
        msg = W.MSG()
        while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            u.TranslateMessage(ctypes.byref(msg))
            u.DispatchMessageW(ctypes.byref(msg))

    threading.Thread(target=stand_in, daemon=True).start()
    ready.wait(5)
    h = win.get("hwnd")
    check("stand-in window", bool(h), True)
    opened, parks, hide = [], [], {"on": True}
    ok = tray.start(Path(sys.argv[1]) / "icon.ico", tip=lambda: "Загрузчик статей test",
                    should_hide=lambda: hide["on"], open_window=lambda: opened.append(1), quit=lambda: None,
                    on_park=lambda: parks.append(1), labels=lambda: {})
    check("tray started", ok, True)
    print("icon in the notification area:", tray.state()["icon_ok"])

    u.ShowWindowAsync(h, 6)                                    # SW_MINIMIZE
    check("minimized: off the taskbar", wait(lambda: not u.IsWindowVisible(h)), True)
    check("parked", tray.parked(), True)
    check("on_park", len(parks), 1)

    tray.activate()                                            # a click on the icon
    check("click: back", wait(lambda: u.IsWindowVisible(h) and not u.IsIconic(h)), True)
    time.sleep(1.5)
    check("stays back", (bool(u.IsWindowVisible(h)), tray.parked()), (True, False))

    hide["on"] = False                                         # «Сворачивать в трей» off
    u.ShowWindowAsync(h, 6)
    time.sleep(3.5)
    check("setting off: stays on the taskbar", bool(u.IsWindowVisible(h)), True)
    hide["on"] = True
    check("setting on again: to the tray", wait(lambda: not u.IsWindowVisible(h)), True)

    check("second start brings it back", app.handle_api("/api/hello", {})["shown"], True)
    check("back again", wait(lambda: u.IsWindowVisible(h) and not u.IsIconic(h)), True)

    u.PostMessageW(h, 0x0010, 0, 0)                           # the window closed
    check("closed", wait(lambda: not u.IsWindow(h)), True)
    tray.activate()
    check("click: a new window", wait(lambda: len(opened) == 1), True)

    tray.stop()
    check("stopped", tray.state()["running"], False)

if fails:
    print("\n".join(fails))
    sys.exit(1)
print("tray tests: OK")
