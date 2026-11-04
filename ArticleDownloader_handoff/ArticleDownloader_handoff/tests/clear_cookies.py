from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.connect_over_cdp("http://127.0.0.1:9223")
    b.contexts[0].clear_cookies()
    print("cookies cleared")
