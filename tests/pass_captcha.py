"""Play the user: click "I am human" on the fake check page in the dedicated browser."""
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp("http://127.0.0.1:9223")
    for page in browser.contexts[0].pages:
        if "moment" in page.title().lower():
            print("check page:", page.url)
            page.click("#pass")
            print("passed")
