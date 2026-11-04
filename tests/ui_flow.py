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

    # v7.0: screening by abstracts, straight from the saved list
    page.click("#save-note button[data-review]")
    page.wait_for_selector("#rv-body .rv-abstract")
    page.wait_for_function("!document.querySelector('#rv-body .rv-abstract').textContent.includes('Загружаю')", timeout=30000)
    assert "<mark>" in page.inner_html("#rv-body .rv-abstract"), "search words are not highlighted"
    for key in ("1", "1", "3"):
        page.keyboard.press(key)
        page.wait_for_timeout(300)
    page.click("[data-reason='не по теме']")
    page.wait_for_timeout(300)
    page.keyboard.press("2")
    page.wait_for_timeout(600)
    counts = page.text_content("#rv-counts")
    assert ("отобрано 5" in counts and "подходит: 2" in counts and "под вопросом: 1" in counts
            and "не подходит: 2" in counts), counts
    print("screening:", counts)
    shot(page, "14_review.png", full=False)
    page.click("#rv-save")
    page.wait_for_selector("#rv-saved .note.green")
    print("selected list:", page.text_content("#rv-saved")[:80])

    # v7.0: reference list and PRISMA for the selected articles
    page.click("#rv-saved button[data-biblio]")
    page.wait_for_selector("#bb-entries li")
    page.wait_for_timeout(800)
    assert page.locator("#bb-entries li").count() == 2, page.text_content("#bb-entries")
    print("GOST:", page.locator("#bb-entries li").first.text_content()[:120])
    prisma = page.text_content("#prisma")
    assert "Найдено записей: 130" in prisma and "повторы — 8" in prisma and "Включено в обзор: 2" in prisma, prisma
    for button in ("#bb-save-txt", "#bb-save-ris", "#bb-save-bib"):
        page.click(button)
        page.wait_for_timeout(500)
    files = sorted(x.name for x in (B / "ArticleDownloader/Список литературы").iterdir())
    assert len(files) == 3, files
    print("reference files:", files)
    shot(page, "15_biblio.png")
    with page.expect_download() as download:
        page.click("#pr-png")
    download.value.save_as(str(SH / "16_prisma.png"))
    print("PRISMA picture:", download.value.suggested_filename, (SH / "16_prisma.png").stat().st_size, "bytes")
    page.click("#btn-home")
    page.click("#go-search")

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

    # v6.2: keyword hints
    dialogs = []
    page.on("dialog", lambda d: (dialogs.append(d.message), d.accept()))
    page.click("#go-search")
    page.click("#btn-hints")
    page.wait_for_selector("#hints [data-hint]", timeout=15000)
    first = page.get_attribute("#hints [data-hint]", "data-hint")
    page.click("#hints [data-hint]")
    assert first in page.input_value("#kw"), page.input_value("#kw")
    print("hint added:", first, "| note:", page.text_content("#hints-note")[:80])
    shot(page, "11_hints.png", full=False)

    # v6.2: new topic clears the form and the found records
    page.click("#btn-new-topic")
    page.wait_for_timeout(1500)
    assert page.input_value("#kw") == "" and page.is_hidden("#results-card"), "new topic did not clear"
    print("new topic: cleared")

    # v6.2: archive a list
    page.click("#btn-home"); page.click("#go-download")
    page.wait_for_selector("#lists [data-archive]")
    name = page.get_attribute("#lists [data-archive]", "data-archive")
    page.click("#lists [data-archive]")
    page.wait_for_function("document.getElementById('choose-note').textContent.includes('архив')")
    archived = list((B / "ArticleDownloader/inbox/_архив").glob("*.txt"))
    assert archived and name not in page.text_content("#lists"), (archived, name)
    print("archived:", [a.name for a in archived])

    # v6.2: thanks to the author
    page.click("#btn-home")
    page.click("#home-donate")
    page.wait_for_selector("#donate-body .donate-card .qr svg")
    page.wait_for_timeout(800)
    page.locator("#dlg-donate").screenshot(path=str(SH / "12_donate.png"))
    print("shot 12_donate.png")
    donate = json.loads((B / "ArticleDownloader/scripts/donate.json").read_text(encoding="utf-8"))
    expected = [donate["sbp"]["url"]] + [c["address"] for c in donate["crypto"]]
    try:
        import cv2
    except ImportError:
        cv2 = None
    if cv2:
        decoded = []
        for i, qr in enumerate(page.locator("#donate-body .qr").all()):
            path = SH / f"qr_{i}.png"
            qr.evaluate("el => el.scrollIntoView({block: 'center'})")
            page.wait_for_timeout(300)
            # A screen clip by the code's position: an element screenshot of a
            # code low in the scrolled dialog came out cut off.
            box = qr.bounding_box()
            page.screenshot(path=str(path), clip={"x": box["x"] - 8, "y": box["y"] - 8,
                                                  "width": box["width"] + 16, "height": box["height"] + 16})
            img = cv2.imread(str(path))
            text = cv2.QRCodeDetector().detectAndDecode(img)[0]
            if not text:  # OpenCV sometimes needs it larger, sometimes not
                big = cv2.resize(img, None, fx=3, fy=3, interpolation=cv2.INTER_NEAREST)
                text = cv2.QRCodeDetector().detectAndDecode(big)[0]
            decoded.append(text)
        assert decoded == expected, (decoded, expected)
        print("QR codes read back:", len(decoded), "match donate.json")

    # v6.3: one button on the start screen clears the search and the queue
    page.click("#dlg-donate [data-close]")
    asked = len(dialogs)
    page.click("#home-clear")          # the saved list is still downloading: one press stops and clears
    page.wait_for_function("document.getElementById('home-msg').textContent.includes('очищены')", timeout=30000)
    page.wait_for_timeout(2000)
    state = json.loads(page.evaluate("fetch('/api/state?v=-1', {headers: {'X-Token': TOKEN}}).then(r => r.text())"))
    assert not state["running"] and state["total"] == 0, (state["running"], state["total"])
    assert len(dialogs) == asked, dialogs[asked:]      # no questions on the way
    assert page.text_content("#home-note").strip() == "", page.text_content("#home-note")
    assert (B / "ArticleDownloader/inbox/_архив").is_dir()
    print("start-screen clear:", page.text_content("#home-msg"))
    shot(page, "13_home_cleared.png", full=False)

    # "Выход" stops the program and closes the window (or says how to close it)
    closed = []
    page.on("close", lambda _: closed.append(True))
    page.click("#btn-quit")
    page.wait_for_timeout(1500)
    if closed:
        print("quit: window closed itself")
    else:
        assert "Программа закрыта" in page.text_content("body"), page.text_content("body")[:200]
        print("quit: window kept open with the close message")
    assert not errors, errors
    br.close()
    print("ui flow: OK")
