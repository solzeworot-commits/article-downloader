"""Print the running program's state: python state.py <ArticleDownloader folder>"""
import json
import sys
import urllib.request
from pathlib import Path

root = Path(sys.argv[1])
token = (root / "logs" / ".ui_token").read_text(encoding="utf-8").strip()
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
req = urllib.request.Request("http://127.0.0.1:17865/api/state?v=-1", headers={"X-Token": token})
s = json.loads(opener.open(req, timeout=10).read())
print("phase:", s["phase"], "| queue running:", s["running"], "| captcha now:", s["captcha_now"])
print("warm-up:", "running" if s["warm"]["running"] else "done",
      "| cleared:", s["warm"]["cleared"], "| error:", s["warm"]["error"] or "-")
print("bases:", {b["key"]: b["state"] for b in s["warm"]["bases"] if b["enabled"]})
for item in s.get("items", []):
    print(f'  {item["id"]:>3} {item["status"]:<11} {item["query"]}  {item["message"][:80]}')
