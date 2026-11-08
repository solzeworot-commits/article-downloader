# -*- coding: utf-8 -*-
# © 2026 Bucaneiro. Бесплатная программа; условия — в файле ЛИЦЕНЗИЯ.txt.
"""
The program's icon by the clock on Windows (v8.8.1).

- The program window (the --app window of Edge, Chrome or Yandex titled
  «Загрузчик статей») leaves the taskbar for the tray when it is minimized;
  a click on the icon brings it back, or opens a new one when it was closed.
- Right click: «Открыть окно», «Выход». The tooltip says what the program does.
- Windows 11 puts new icons under «^»: the first time, ours is made visible
  (HKCU\\Control Panel\\NotifyIconSettings, IsPromoted); if the user hides it
  later, that choice stays.

Plain Win32 through ctypes, no extra packages. Elsewhere (the Mac) start()
does nothing and the window goes to the Dock as usual.
"""
from __future__ import annotations

import os
import sys
import threading
import time

IS_WIN = os.name == "nt"
TITLES = ("Загрузчик статей", "Article Downloader")   # the page title, Russian and English
WINDOW_CLASS = "Chrome_WidgetWin_1"                   # Chromium's top-level windows

_t: dict = {"thread": None, "hwnd": None, "hicon": None, "hidden": None, "hid_at": 0.0, "grace": 0.0,
            "icon_ok": False, "tip": "", "tip_at": 0.0, "cb": {}, "balloon": None, "promote_tries": 0}
_lock = threading.Lock()

WM_CLOSE, WM_DESTROY, WM_TIMER, WM_NULL = 0x0010, 0x0002, 0x0113, 0x0000
WM_LBUTTONUP, WM_RBUTTONUP = 0x0202, 0x0205
WM_TRAY, WM_ACTIVATE_APP, WM_BALLOON = 0x8001, 0x8002, 0x8003     # WM_APP + n
NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO = 0x1, 0x2, 0x4, 0x10
NIIF_USER = 0x4
SW_HIDE, SW_RESTORE = 0, 9
TICK_MS, TIMER_TICK, TIMER_PROMOTE = 400, 1, 2

if IS_WIN:
    import ctypes
    from ctypes import wintypes as W

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    LRESULT = ctypes.c_ssize_t
    WNDPROC = ctypes.WINFUNCTYPE(LRESULT, W.HWND, W.UINT, W.WPARAM, W.LPARAM)
    WNDENUMPROC = ctypes.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)

    class WNDCLASSW(ctypes.Structure):
        _fields_ = [("style", W.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                    ("cbWndExtra", ctypes.c_int), ("hInstance", W.HINSTANCE), ("hIcon", W.HICON),
                    ("hCursor", W.HANDLE), ("hbrBackground", W.HBRUSH), ("lpszMenuName", W.LPCWSTR),
                    ("lpszClassName", W.LPCWSTR)]

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", W.DWORD), ("Data2", W.WORD), ("Data3", W.WORD), ("Data4", ctypes.c_ubyte * 8)]

    class NOTIFYICONDATAW(ctypes.Structure):
        _fields_ = [("cbSize", W.DWORD), ("hWnd", W.HWND), ("uID", W.UINT), ("uFlags", W.UINT),
                    ("uCallbackMessage", W.UINT), ("hIcon", W.HICON), ("szTip", W.WCHAR * 128),
                    ("dwState", W.DWORD), ("dwStateMask", W.DWORD), ("szInfo", W.WCHAR * 256),
                    ("uVersion", W.UINT), ("szInfoTitle", W.WCHAR * 64), ("dwInfoFlags", W.DWORD),
                    ("guidItem", GUID), ("hBalloonIcon", W.HICON)]

    def _sig(fn, args, res=W.BOOL):
        fn.argtypes, fn.restype = args, res

    _sig(user32.DefWindowProcW, [W.HWND, W.UINT, W.WPARAM, W.LPARAM], LRESULT)
    _sig(user32.RegisterClassW, [ctypes.POINTER(WNDCLASSW)], W.ATOM)
    _sig(user32.CreateWindowExW, [W.DWORD, W.LPCWSTR, W.LPCWSTR, W.DWORD, ctypes.c_int, ctypes.c_int,
                                  ctypes.c_int, ctypes.c_int, W.HWND, W.HMENU, W.HINSTANCE, W.LPVOID], W.HWND)
    _sig(user32.DestroyWindow, [W.HWND])
    _sig(user32.LoadImageW, [W.HINSTANCE, W.LPCWSTR, W.UINT, ctypes.c_int, ctypes.c_int, W.UINT], W.HANDLE)
    _sig(user32.DestroyIcon, [W.HICON])
    _sig(user32.GetSystemMetrics, [ctypes.c_int], ctypes.c_int)
    _sig(user32.GetMessageW, [ctypes.POINTER(W.MSG), W.HWND, W.UINT, W.UINT], ctypes.c_int)
    _sig(user32.TranslateMessage, [ctypes.POINTER(W.MSG)])
    _sig(user32.DispatchMessageW, [ctypes.POINTER(W.MSG)], LRESULT)
    _sig(user32.PostMessageW, [W.HWND, W.UINT, W.WPARAM, W.LPARAM])
    _sig(user32.PostQuitMessage, [ctypes.c_int], None)
    _sig(user32.SetTimer, [W.HWND, ctypes.c_size_t, W.UINT, W.LPVOID], ctypes.c_size_t)
    _sig(user32.KillTimer, [W.HWND, ctypes.c_size_t])
    _sig(user32.RegisterWindowMessageW, [W.LPCWSTR], W.UINT)
    _sig(user32.EnumWindows, [WNDENUMPROC, W.LPARAM])
    _sig(user32.GetWindowTextW, [W.HWND, W.LPWSTR, ctypes.c_int], ctypes.c_int)
    _sig(user32.GetClassNameW, [W.HWND, W.LPWSTR, ctypes.c_int], ctypes.c_int)
    _sig(user32.IsWindow, [W.HWND])
    _sig(user32.IsWindowVisible, [W.HWND])
    _sig(user32.IsIconic, [W.HWND])
    _sig(user32.ShowWindowAsync, [W.HWND, ctypes.c_int])
    _sig(user32.SetForegroundWindow, [W.HWND])
    _sig(user32.CreatePopupMenu, [], W.HMENU)
    _sig(user32.AppendMenuW, [W.HMENU, W.UINT, ctypes.c_size_t, W.LPCWSTR])
    _sig(user32.TrackPopupMenu, [W.HMENU, W.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int, W.HWND, W.LPVOID],
         ctypes.c_int)
    _sig(user32.DestroyMenu, [W.HMENU])
    _sig(user32.GetCursorPos, [ctypes.POINTER(W.POINT)])
    _sig(shell32.Shell_NotifyIconW, [W.DWORD, ctypes.POINTER(NOTIFYICONDATAW)])
    _sig(kernel32.GetModuleHandleW, [W.LPCWSTR], W.HMODULE)


def _safe(fn, *args):
    try:
        return fn(*args) if fn else None
    except Exception:
        return None


# ---- the program window -----------------------------------------------------

def _windows(visible: bool | None) -> list:
    """The program windows (the class and the exact title): visible ones, hidden ones or all (None)."""
    found = []
    buf = ctypes.create_unicode_buffer(256)

    def each(hwnd, _):
        shown = bool(user32.IsWindowVisible(hwnd))
        if visible is not None and shown != visible:
            return True
        user32.GetClassNameW(hwnd, buf, 256)
        if buf.value != WINDOW_CLASS:
            return True
        user32.GetWindowTextW(hwnd, buf, 256)
        if buf.value in TITLES:
            found.append(hwnd)
        return True

    user32.EnumWindows(WNDENUMPROC(each), 0)
    return found


def show_window(parked_only: bool = False) -> bool:
    """Bring the program window back (from the tray, minimized or behind others). False: none to show."""
    if not IS_WIN or not _t["hwnd"]:
        return False
    with _lock:
        w = _t["hidden"]
        if w and not user32.IsWindow(w):
            w = None
        if not w and not parked_only:
            w = next(iter(_windows(visible=True)), None)
        if not w:
            return False
        _t["hidden"] = None
        _t["grace"] = time.time() + 2.5        # do not take it back to the tray while it is being restored
    if not user32.IsWindowVisible(w) or user32.IsIconic(w):
        user32.ShowWindowAsync(w, SW_RESTORE)
    user32.SetForegroundWindow(w)
    return True


def parked() -> bool:
    """The program window is hidden in the tray right now."""
    return bool(_t["hidden"])


def state() -> dict:
    return {"running": bool(_t["hwnd"]), "icon_ok": _t["icon_ok"], "parked": parked()}


def _tick():
    now = time.time()
    with _lock:
        hidden = _t["hidden"]
        if hidden and now - _t["hid_at"] > 1.5 and (not user32.IsWindow(hidden) or user32.IsWindowVisible(hidden)):
            _t["hidden"] = hidden = None         # closed, or shown again by itself
    if not hidden and now > _t["grace"] and _safe(_t["cb"].get("should_hide")):
        w = next((h for h in _windows(visible=True) if user32.IsIconic(h)), None)
        if w:
            user32.ShowWindowAsync(w, SW_HIDE)
            with _lock:
                _t["hidden"], _t["hid_at"] = w, now
            _safe(_t["cb"].get("on_park"))
    if now - _t["tip_at"] > 2:
        _t["tip_at"] = now
        tip = _safe(_t["cb"].get("tip"))
        if tip and tip != _t["tip"]:
            _t["tip"] = tip
            shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(_nid(NIF_TIP)))


def _activate():
    if not show_window():
        threading.Thread(target=_safe, args=(_t["cb"].get("open_window"),), daemon=True).start()


# ---- the icon ---------------------------------------------------------------------

def _nid(flags: int):
    nid = NOTIFYICONDATAW()
    nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
    nid.hWnd, nid.uID, nid.uFlags = _t["hwnd"], 1, flags
    nid.uCallbackMessage, nid.hIcon = WM_TRAY, _t["hicon"]
    nid.szTip = (_t["tip"] or TITLES[0])[:127]
    return nid


def _add_icon():
    _t["icon_ok"] = bool(shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(_nid(NIF_MESSAGE | NIF_ICON | NIF_TIP))))


def _delete_icon():
    shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(_nid(0)))
    _t["icon_ok"] = False


def notify(title: str, text: str):
    """A notification next to the icon (a toast on Windows 10 and 11)."""
    if IS_WIN and _t["hwnd"]:
        _t["balloon"] = (str(title), str(text))
        user32.PostMessageW(_t["hwnd"], WM_BALLOON, 0, 0)


def _balloon():
    title, text = _t["balloon"] or ("", "")
    _t["balloon"] = None
    nid = _nid(NIF_INFO)
    nid.szInfoTitle, nid.szInfo = title[:63], text[:255]
    nid.dwInfoFlags, nid.hBalloonIcon = NIIF_USER, _t["hicon"]
    shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))


def _promote() -> bool:
    """Windows 11: show our icon on the taskbar, not under «^» (once: the user's later choice stays)."""
    try:
        import winreg
    except ImportError:
        return True
    exe = os.path.basename(sys.executable).lower()
    found = changed = False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\NotifyIconSettings") as root:
            for i in range(10000):
                try:
                    sub = winreg.EnumKey(root, i)
                except OSError:
                    break
                with winreg.OpenKey(root, sub, 0, winreg.KEY_READ | winreg.KEY_SET_VALUE) as key:
                    def value(name):
                        try:
                            return winreg.QueryValueEx(key, name)[0]
                        except OSError:
                            return None
                    if not (str(value("ExecutablePath") or "").lower().endswith(exe)
                            and str(value("InitialTooltip") or "").startswith(TITLES)):
                        continue
                    found = True
                    if value("IsPromoted") is None:
                        winreg.SetValueEx(key, "IsPromoted", 0, winreg.REG_DWORD, 1)
                        changed = True
    except OSError:
        return True                               # Windows 10: no such settings
    if changed:                                   # Explorer reads it when the icon is added
        _delete_icon()
        _add_icon()
    return found


def _menu(hwnd):
    labels = _safe(_t["cb"].get("labels")) or {}
    menu = user32.CreatePopupMenu()
    user32.AppendMenuW(menu, 0, 1, labels.get("open") or "Открыть окно")
    user32.AppendMenuW(menu, 0x800, 0, None)                       # a separator
    user32.AppendMenuW(menu, 0, 2, labels.get("quit") or "Выход")
    pt = W.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    user32.SetForegroundWindow(hwnd)                               # so the menu closes on a click elsewhere
    cmd = user32.TrackPopupMenu(menu, 0x2 | 0x100 | 0x80, pt.x, pt.y, 0, hwnd, None)
    user32.PostMessageW(hwnd, WM_NULL, 0, 0)
    user32.DestroyMenu(menu)
    if cmd == 1:
        _activate()
    elif cmd == 2:
        threading.Thread(target=_safe, args=(_t["cb"].get("quit"),), daemon=True).start()


def _wndproc(hwnd, msg, wparam, lparam):
    try:
        if msg == WM_TRAY:
            if lparam == WM_LBUTTONUP:
                _activate()
            elif lparam == WM_RBUTTONUP:
                _menu(hwnd)
            return 0
        if msg == WM_TIMER:
            if wparam == TIMER_TICK:
                _tick()
            elif wparam == TIMER_PROMOTE:
                _t["promote_tries"] += 1
                if _promote() or _t["promote_tries"] >= 4:   # Explorer writes its settings a little later
                    user32.KillTimer(hwnd, TIMER_PROMOTE)
            return 0
        if msg == WM_ACTIVATE_APP:
            _activate()
            return 0
        if msg == WM_BALLOON:
            _balloon()
            return 0
        if msg == _t.get("taskbar_created") and msg:          # Explorer restarted: the icon again
            _add_icon()
            return 0
        if msg == WM_CLOSE:
            _delete_icon()
            user32.DestroyWindow(hwnd)
            return 0
        if msg == WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
    except Exception:
        pass
    return user32.DefWindowProcW(hwnd, msg, wparam, lparam)


def _run(icon_path, ready: threading.Event):
    try:
        hinst = kernel32.GetModuleHandleW(None)
        if "wndproc" not in _t:                         # once per process; kept: Windows calls it
            _t["wndproc"] = WNDPROC(_wndproc)
            wc = WNDCLASSW()
            wc.lpfnWndProc, wc.hInstance, wc.lpszClassName = _t["wndproc"], hinst, "ArticleDownloaderTray"
            user32.RegisterClassW(ctypes.byref(wc))
        hwnd = user32.CreateWindowExW(0, "ArticleDownloaderTray", "ArticleDownloader tray", 0,
                                      0, 0, 0, 0, None, None, hinst, None)
        if not hwnd:
            return
        size = user32.GetSystemMetrics(49) or 16                  # SM_CXSMICON
        _t["hicon"] = user32.LoadImageW(None, str(icon_path), 1, size, size, 0x10) if icon_path else None
        _t["hwnd"] = hwnd
        _t["taskbar_created"] = user32.RegisterWindowMessageW("TaskbarCreated")
        _t["tip"] = _safe(_t["cb"].get("tip")) or TITLES[0]
        _add_icon()
        # Windows left hidden in the tray by an earlier run (an update restarted the program): close them.
        for w in _windows(visible=False):
            if user32.IsIconic(w):
                user32.PostMessageW(w, WM_CLOSE, 0, 0)
        user32.SetTimer(hwnd, TIMER_TICK, TICK_MS, None)
        user32.SetTimer(hwnd, TIMER_PROMOTE, 3000, None)
    finally:
        ready.set()
    msg = W.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))
    if _t["hicon"]:
        user32.DestroyIcon(_t["hicon"])
    _t.update(hwnd=None, hicon=None, icon_ok=False)


# ---- start and stop ---------------------------------------------------------------

def start(icon_path, tip=None, should_hide=None, open_window=None, quit=None, on_park=None, labels=None) -> bool:
    """Show the icon (Windows only). The callbacks: tip() -> str, should_hide() -> bool,
    open_window(), quit(), on_park() (the window just went to the tray), labels() -> {"open", "quit"}."""
    if not IS_WIN or _t["thread"]:
        return False
    _t["cb"] = {"tip": tip, "should_hide": should_hide, "open_window": open_window,
                "quit": quit, "on_park": on_park, "labels": labels}
    ready = threading.Event()
    thread = threading.Thread(target=_run, args=(icon_path, ready), daemon=True, name="tray")
    _t["thread"] = thread
    thread.start()
    ready.wait(5)
    return bool(_t["hwnd"])


def activate():
    """As a click on the icon (from any thread)."""
    if IS_WIN and _t["hwnd"]:
        user32.PostMessageW(_t["hwnd"], WM_ACTIVATE_APP, 0, 0)


def stop():
    """Remove the icon. A window still hidden in the tray is closed: the program is ending."""
    if not IS_WIN:
        return
    hidden, hwnd, thread = _t["hidden"], _t["hwnd"], _t["thread"]
    if hidden and user32.IsWindow(hidden):
        user32.PostMessageW(hidden, WM_CLOSE, 0, 0)
    _t["hidden"] = None
    if hwnd:
        user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
    if thread and thread is not threading.current_thread():
        thread.join(3)
    _t["thread"] = None
