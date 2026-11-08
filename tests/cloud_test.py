"""«☁ Облако» (v8.8): python cloud_test.py <portable/scripts>

One program plays both computers in turn over a temporary "cloud" folder:
the home computer sends a list, the work computer takes it (the second time
it sees it unchanged), queues it and writes its status; the home computer
reads the colours of the bar from it: never, wait, run, captcha, stopped,
done, stale, idle. Also: the cloud folders found, the PDF folder moved into
the cloud and back, a different list with the same name, a second work
computer, the next day after the daily limit. The queue itself is not
started (no browser): start_queue is replaced by a counter.
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

tmp = Path(tempfile.mkdtemp(prefix="ad_cloud_"))
home_dir = tmp / "home"
home_dir.mkdir(parents=True)
os.environ.update(HOME=str(home_dir), USERPROFILE=str(home_dir), ARTICLE_UI_NO_WINDOW="1",
                  ARTICLE_KEYS_FILE=str(tmp / "keys.json"), LOCALAPPDATA=str(tmp / "local"))
for var in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
    os.environ.pop(var, None)
sys.path.insert(0, sys.argv[1])
import app  # noqa: E402
import cloud  # noqa: E402

# Yandex Disk is detected at different places on each system: make the real one.
yandex = home_dir / ("Yandex.Disk.localized" if cloud.IS_MAC else "YandexDisk")
yandex.mkdir(parents=True)

app.SETTINGS_PATH = tmp / "settings.json"
app.INBOX = tmp / "inbox"
app.LOGS = tmp / "logs"
app.APP_LOG = tmp / "logs" / "app.log"
app.CLOUD_TAKEN_PATH = tmp / "logs" / "cloud_taken.json"
app.S.settings["pdf_dir"] = ""
app.S.settings["cloud"] = {}
started = []
app.start_queue = lambda: started.append(1)
fails = []


def check(name, got, want):
    if got != want:
        fails.append(f"{name}: {got!r} != {want!r}")


def api(path, body=None):
    return app.handle_api(path, body or {})


def as_home():
    check("home setup", api("/api/cloud_setup", {"dir": str(yandex), "role": "home", "pdf": True}).get("ok"), True)


def as_work():
    res = api("/api/cloud_setup", {"dir": str(yandex), "role": "work", "pdf": True})
    check("work setup", res.get("ok"), True)
    return res


def state():
    return app.cloud_state(fresh=True)


# ---- the clouds of this computer
check("found", [d["name"] for d in cloud.detect()], ["Яндекс Диск"])
check("found by the window", api("/api/cloud")["detected"][0]["path"], str(yandex))
check("not set", state(), {"role": ""})
check("no folder", "error" in api("/api/cloud_setup", {"dir": str(tmp / "nowhere"), "role": "home"}), True)
check("no role", "error" in api("/api/cloud_setup", {"dir": str(yandex), "role": ""}), True)

# ---- the home computer
as_home()
shared = yandex / cloud.FOLDER
check("folders made", ((shared / cloud.TODO).is_dir(), (shared / cloud.PDF).is_dir()), (True, True))
check("PDF into the cloud", app.pdf_dir(), shared / cloud.PDF)
check("no work computer yet", state()["state"], "never")
app.INBOX.mkdir(parents=True, exist_ok=True)
LIST = "Виноград.txt"
(app.INBOX / LIST).write_text("10.1000/a1\n10.1000/a2\n10.1000/a3\n", encoding="utf-8")
sent = api("/api/cloud_send", {"name": LIST})
check("sent", (sent.get("sent"), sent.get("answering")), (LIST, False))
check("in the cloud", (shared / cloud.TODO / LIST).is_file(), True)
check("not a list", "error" in api("/api/cloud_send", {"name": "нет.txt"}), True)

# ---- the work computer takes it
as_work()
check("the PDF folder stays in the cloud", app.pdf_dir(), shared / cloud.PDF)
app.cloud_tick()
check("seen once: not taken yet", (len(app.S.items), started), (0, []))
app.S.running = True                      # what start_queue would do
app.cloud_tick()
check("taken", [i["query"] for i in app.S.items], ["10.1000/a1", "10.1000/a2", "10.1000/a3"])
check("queue started", len(started), 1)
check("same list reused in inbox", sorted(p.name for p in app.INBOX.iterdir()), [LIST])
status = cloud.read_status(shared)
check("status written", (status["lists"][LIST]["total"], status["lists"][LIST]["queued"]), (3, 3))
check("work bar", (state()["state"], state()["total"]), ("run", 3))
app.cloud_tick()
check("not taken twice", len(app.S.items), 3)

# progress and a CAPTCHA
app.S.items[0]["status"] = "success"
app.S.captcha_now = True
app.cloud_tick()
as_home()
view = state()
check("home: captcha", (view["state"], view["done"], view["total"]), ("captcha", 1, 3))
check("home: list row", (view["lists"][0]["name"], view["lists"][0]["state"]), (LIST, "run"))
check("answering now", api("/api/cloud_send", {"name": LIST}).get("answering"), True)

# «Стоп» on the work computer
as_work()
app.cloud_tick()                          # the list sent again: seen once
app.S.captcha_now = False
app.S.running = False
app.cloud_tick()
check("sent again: no duplicates", len(app.S.items), 3)
as_home()
check("home: stopped", state()["state"], "stopped")

# all done
as_work()
app.S.items[1]["status"] = "success"
app.S.items[2]["status"] = "no_access"
app.cloud_tick()
as_home()
view = state()
check("home: done", (view["state"], view["done"], view["total"], view["lists"][0]["no_access"]), ("done", 2, 3, 1))

# not answering for a long time
status = cloud.read_status(shared)
check("stale", cloud.home_view(shared, cloud.list_files(shared, app.LIST_SUFFIXES), status,
                               now=status["updated"] + cloud.STALE_SEC + 5)["state"], "stale")

# a new list: waiting until the work computer takes it
(app.INBOX / "Оливки.txt").write_text("10.1000/b1\n", encoding="utf-8")
api("/api/cloud_send", {"name": "Оливки.txt"})
check("home: wait", state()["state"], "wait")

# removed from the cloud: the work computer forgets it
api("/api/cloud_remove", {"name": "Оливки.txt"})
api("/api/cloud_remove", {"name": LIST})
as_work()
app.cloud_tick()
check("forgotten", cloud.read_status(shared)["lists"], {})
as_home()
check("home: idle", state()["state"], "idle")

# the next day after the daily limit the work computer goes on by itself
as_work()
app.S.phase, app.S.running, app.S.day = "limit", False, "2000-01-01"
before = len(started)
app.cloud_tick()
check("next day", (app.S.phase, len(started) - before), ("idle", 1))

# a different list with the same name as one in inbox
placed = cloud.place(b"10.1000/c1\n", LIST, app.INBOX)
check("other list, other name", placed.name, "Виноград (облако).txt")

# a second work computer in the same folder
status = cloud.read_status(shared)
status.update(computer="ДРУГОЙ-ПК", updated=time.time())
(shared / cloud.STATUS).write_text(json.dumps(status, ensure_ascii=False), encoding="utf-8")
check("second work computer", "ДРУГОЙ-ПК" in as_work().get("warning", ""), True)

# off: the PDF folder comes back
api("/api/cloud_off")
check("off", (state(), app.S.settings["pdf_dir"], app.pdf_dir()), ({"role": ""}, "", app.ROOT / "pdf"))
check("off saved", json.loads(app.SETTINGS_PATH.read_text(encoding="utf-8"))["cloud"], {})
check("files stay in the cloud", (shared / cloud.STATUS).is_file(), True)

if fails:
    print("\n".join(fails))
    sys.exit(1)
print("cloud tests: OK")
