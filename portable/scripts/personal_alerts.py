"""Personal captcha alerts. Bot credentials live only at the notification service."""
import json
import os
from pathlib import Path
import re
import secrets
import tempfile
import threading
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

import osutil


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def relay_request(origin, path, token, payload=None):
    request = Request(origin + path, data=json.dumps(payload or {}).encode(), headers={
        "Authorization": "Bearer " + token, "Content-Type": "application/json",
    })
    with build_opener(NoRedirect()).open(request, timeout=8) as response:
        return json.loads(response.read(2048))


class PersonalAlerts:
    def __init__(self, path=None):
        root = osutil.user_data_dir() or Path.home() / ".config" / "ArticleDownloader"
        self.path = path or root / "personal-alerts.json"
        self.lock = threading.RLock()
        self.note = ""
        try:
            record = json.loads(self.path.read_text(encoding="utf-8"))
            self.binding = record if (isinstance(record, dict)
                and all(isinstance(record.get(k), str) for k in ("provider", "origin", "token"))
                and record["provider"] in ("telegram", "max")) else None
        except (OSError, ValueError):
            self.binding = None

    def status(self):
        with self.lock:
            return {"provider": self.binding["provider"] if self.binding else "", "note": self.note}

    def bind(self, link):
        if not isinstance(link, str):
            raise ValueError("invalid_link")
        parts = urlsplit(link.strip())
        if (parts.scheme != "https" or not re.fullmatch(r"article-downloader-notify\.[a-z0-9-]+\.workers\.dev", parts.netloc)
                or parts.path != "/pair" or parts.query or not re.fullmatch("[a-f0-9]{32}", parts.fragment)):
            raise ValueError("invalid_link")
        origin = "https://" + parts.netloc
        with self.lock:
            if self.binding:
                raise ValueError("already_bound")
            token = secrets.token_urlsafe(32)
            result = relay_request(origin, "/bind", token, {"code": parts.fragment})
            if result.get("provider") not in ("telegram", "max"):
                raise ValueError("unavailable")
            record = {"origin": origin, "token": token, "provider": result["provider"]}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = None
            try:
                with tempfile.NamedTemporaryFile(mode="w", dir=self.path.parent, encoding="utf-8", delete=False) as f:
                    tmp = Path(f.name)
                    json.dump(record, f)
                os.replace(tmp, self.path)
            finally:
                if tmp:
                    tmp.unlink(missing_ok=True)
            self.binding, self.note = record, ""

    def unlink(self):
        with self.lock:
            if self.binding:
                relay_request(self.binding["origin"], "/unlink", self.binding["token"])
                self.path.unlink(missing_ok=True)
                self.binding, self.note = None, ""

    def send(self, binding, test=False):
        try:
            relay_request(binding["origin"], "/test" if test else "/alert", binding["token"])
            note = "sent"
        except HTTPError as e:
            note = {401: "unlinked", 429: "recent"}.get(e.code, "unavailable")
        except (OSError, ValueError):
            note = "unavailable"
        with self.lock:
            if self.binding is binding:
                self.note = note
        return note

    def manual(self):
        # Called from the browser progress reader. Network I/O must not stall that reader.
        binding = self.binding
        if binding:
            threading.Thread(target=self.send, args=(binding,), daemon=True).start()
