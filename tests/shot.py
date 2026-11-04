"""Screenshot of the program window: python shot.py <ArticleDownloader folder> out.png

CHROME_PATH may point to a Chromium/Chrome/Edge executable when Playwright has
no browser of its own installed.
"""
import os
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

root, out = Path(sys.argv[1]), sys.argv[2]
token = (root / "logs" / ".ui_token").read_text(encoding="utf-8").strip()
with sync_playwright() as p:
    kwargs = {"executable_path": os.environ["CHROME_PATH"]} if os.environ.get("CHROME_PATH") else {}
    browser = p.chromium.launch(args=["--no-sandbox"], **kwargs)
    page = browser.new_page(viewport={"width": 1180, "height": 900}, locale="ru-RU")
    page.goto(f"http://127.0.0.1:17865/?t={token}")
    page.wait_for_timeout(2500)
    page.screenshot(path=out, full_page=True)
    browser.close()
