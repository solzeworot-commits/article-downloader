"""
Clicks through the v6.1 screens like a user and saves screenshots:

    python ui_flow.py <bench folder>

The bench folder holds ArticleDownloader\\ (a copy of portable\\, running through
harness.py with the fake database), keys.json (ARTICLE_KEYS_FILE), downloads\\
(ARTICLE_DOWNLOADS_DIR) and gets shots\\. fake_api.py must answer on port 8001
(ARTICLE_SCOPUS_URL etc.) and fake_site.py on 8000. CHROME_PATH may point to a
Chromium for Playwright. HANDOFF.md describes the whole run.
"""
import json
import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

B = Path(sys.argv[1]); SH = B / "shots"
SH.mkdir(exist_ok=True)
token = (B / "ArticleDownloader/logs/.ui_token").read_text().strip()
URL = f"http://127.0.0.1:17865/?t={token}"

def shot(page, name, full=True):
    page.wait_for_timeout(1700)
    page.screenshot(path=str(SH / name), full_page=full)
    print("shot", name)

with sync_playwright() as p:
    kwargs = {"executable_path": os.environ["CHROME_PATH"]} if os.environ.get("CHROME_PATH") else {}
    br = p.chromium.launch(args=["--no-sandbox"], **kwargs)
    ctx = br.new_context(viewport={"width": 1180, "height": 900}, locale="ru-RU")
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(URL)
    shot(page, "1_home.png")

    # Search screen without keys
    page.click("#go-search")
    page.fill("#kw", "grape pomace\npolyphenols")
    page.fill("#f-from", "2015"); page.fill("#f-to", "2024")
    page.wait_for_function("document.getElementById('q-scopus').value.includes('TITLE-ABS-KEY')")
    print("scopus query:", page.input_value("#q-scopus"))
    print("wos query:", page.input_value("#q-wos"))
    shot(page, "2_search_no_keys.png")

    # Key guide: Scopus
    page.click("#keys-note button[data-go=keys]")
    shot(page, "3_keys_scopus.png")
    page.fill("#key-input", "wrong-key-1111")
    page.click("#key-check")
    page.wait_for_function("document.getElementById('key-res').textContent.includes('неверный')")
    print("wrong key:", page.text_content("#key-res"))
    page.fill("#key-input", "good-scopus-key-1234")
    page.click("#key-check")
    page.wait_for_function("document.getElementById('key-res').textContent.includes('сохранён')")
    print("good key:", page.text_content("#key-res"))
    page.wait_for_timeout(1700)
    page.locator(".step.final").screenshot(path=str(SH / "program_01_kluchi.png"))
    # WoS
    page.click("button[data-ktab=wos]")
    page.fill("#key-input", "pending-key-2222")
    page.click("#key-check")
    page.wait_for_function("document.getElementById('key-res').textContent.length > 20 && !document.getElementById('key-res').textContent.startsWith('Проверяю')")
    print("pending wos:", page.text_content("#key-res"))
    page.fill("#key-input", "good-wos-key-5678")
    page.click("#key-check")
    page.wait_for_function("document.getElementById('key-res').textContent.includes('сохранён')")
    shot(page, "4_keys_wos.png")
    print("keys file:", json.loads((B / "keys.json").read_text()).keys())

    # Search through the APIs
    page.click("#btn-home"); page.click("#go-search")
    page.check("#src-openalex")
    page.click("#btn-search")
    page.wait_for_selector("#results table", timeout=30000)
    print("summary:", page.text_content("#res-summary"), "|", page.text_content("#res-sources"))
    shot(page, "5_search_results.png")
    page.uncheck("input[data-sel='10.9999/scopus.10']")
    print("selection:", page.text_content("#sel-count"))
    page.fill("#save-name", "виноградные выжимки")
    page.click("#btn-save-list")
    page.wait_for_selector("#save-note .note.green")
    print("saved:", page.text_content("#save-note"))
    shot(page, "6_saved.png", full=False)

    # Straight to downloading that list
    page.click("#save-note button[data-list]")
    page.wait_for_selector("#lists table")
    page.wait_for_timeout(2500)
    print("choose note:", page.text_content("#choose-note"))
    print("lists:", page.text_content("#lists tbody"))
    shot(page, "7_download.png")

    # Browser mode with an export file in Downloads
    page.click("#btn-home"); page.click("#go-search")
    page.click("#mode-seg button[data-mode=browser]")
    page.click("#b-scopus")
    page.wait_for_timeout(1000)
    print("b-note:", page.text_content("#b-note"))
    export = (b"FN Clarivate Analytics Web of Science\nVR 1.0\nPT J\nTI Grape pomace phenolics review\nSO FOOD CHEMISTRY\nPY 2021\n"
              b"DI 10.1016/j.foodchem.2021.130000\nER\n\nPT J\nTI Second record\nSO MOLECULES\nPY 2020\nDI 10.3390/molecules25051234\nER\n\nEF\n")
    (B / "downloads" / "savedrecs.txt").write_bytes(export)
    page.wait_for_selector("#exports-found button[data-export]", timeout=10000)
    shot(page, "8_browser_mode.png")
    page.click("#exports-found button[data-export]")
    page.wait_for_function("document.getElementById('res-sources').textContent.includes('savedrecs')", timeout=10000)
    print("export summary:", page.text_content("#res-sources"), "|", page.text_content("#res-summary"))
    shot(page, "9_export_results.png")

    page.click("#btn-home")
    shot(page, "10_home_with_queue.png")
    assert not errors, errors
    br.close()
    print("ui flow: OK")
