"""Verify the browser used for Wiley login and downloads."""
import io
import os
import time
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, sys.argv.pop(1))


class WileyBrowserTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        env = patch.dict(os.environ, ARTICLE_LOG_DIR=temp.name, ARTICLE_PDF_DIR=temp.name,
                         ARTICLE_WILEY_CHROME="1", ARTICLE_BROWSER="edge",
                         ARTICLE_PROFILE_DIR=temp.name)
        env.start()
        self.addCleanup(env.stop)
        import article_downloader as ad
        self.ad = ad

    def test_wiley_doi_and_direct_link_use_chrome(self):
        ad = self.ad
        wiley_url = "https://onlinelibrary.wiley.com/doi/10.1002/example"
        contexts = []

        def connect(playwright):
            context = ad.browser_cdp_url()
            contexts.append(context)
            return context

        def download(context, source, raw, doi, resolved):
            self.assertEqual(source, "wiley")
            self.assertTrue(context.endswith(f":{ad.WILEY_BROWSER_PORT}"))
            self.assertEqual(resolved, wiley_url)
            return Path("example.pdf")

        with (patch.object(ad, "sync_playwright"), patch.object(ad, "connect_dedicated_context", connect),
              patch.object(ad, "find_browser_exe", return_value="browser"),
              patch.object(ad, "identify_source", return_value=("wiley", wiley_url)),
              patch.object(ad, "_download_routed", side_effect=download),
              patch.object(ad, "download_supplements", return_value=(0, ""))):
            self.assertEqual(ad.process_one("10.1002/example"), Path("example.pdf"))
            self.assertEqual(ad.process_one(wiley_url), Path("example.pdf"))
        self.assertEqual(contexts, [f"http://127.0.0.1:{ad.WILEY_BROWSER_PORT}"] * 2)
        self.assertEqual(ad.BROWSER_PORT, ad.MAIN_BROWSER_PORT)

    def test_open_article_button_respects_browser_setting(self):
        import app
        with (patch.dict(os.environ, ARTICLE_UI_NO_WINDOW="0"),
              patch.dict(app.S.settings, wiley_chrome=True),
              patch.object(app, "open_in_dedicated_browser") as dedicated,
              patch.object(app.webbrowser, "open") as system):
            for doi in ("10.1002/example", "10.1111/example"):
                app.handle_api("/api/open_doi", {"doi": doi})
                dedicated.assert_called_with(["https://doi.org/" + doi], wiley=True)
            system.assert_not_called()
            app.handle_api("/api/open_doi", {"doi": "10.1016/example"})
            system.assert_called_once_with("https://doi.org/10.1016/example")
            with patch.dict(app.S.settings, wiley_chrome=False):
                app.handle_api("/api/open_doi", {"doi": "10.1111/example"})
                system.assert_called_with("https://doi.org/10.1111/example")

    def test_migrated_journal_uses_its_publishers_browser_after_resolution(self):
        ad = self.ad
        ports = []
        def connect(playwright):
            ports.append(ad.BROWSER_PORT)
            return ad.BROWSER_PORT
        with (patch.object(ad, "sync_playwright"), patch.object(ad, "connect_dedicated_context", connect),
              patch.object(ad, "identify_source", return_value=("oxford", "https://academic.oup.com/example")),
              patch.object(ad, "_download_routed", return_value=Path("example.pdf")) as download,
              patch.object(ad, "download_supplements", return_value=(0, ""))):
            ad.process_one("10.1111/moved")
        self.assertEqual(ports, [ad.WILEY_BROWSER_PORT, ad.MAIN_BROWSER_PORT])
        self.assertEqual(download.call_args.args[:2], (ad.MAIN_BROWSER_PORT, "oxford"))

    def test_manual_wiley_button_uses_chrome_even_when_setting_is_off(self):
        ad = self.ad
        url = "https://agupubs.onlinelibrary.wiley.com/doi/10.1029/example"
        self.assertEqual(ad.classify_direct(url, "10.1029/example"), "wiley")
        for invalid in (url.replace(".com/", ".com.example/"),
                        "https://example.com/?next=" + url):
            self.assertNotEqual(ad.classify_direct(invalid, "10.1029/example"), "wiley")
        def download(context, source, raw, doi, resolved):
            self.assertEqual((source, resolved), ("wiley", url))
            self.assertEqual(ad.BROWSER_PORT, ad.WILEY_BROWSER_PORT)
            self.assertEqual(ad.dedicated_browser()[0], "chrome")
            return Path("download.pdf")
        with (patch.dict(os.environ, ARTICLE_BROWSER="yandex", ARTICLE_WILEY_CHROME="0"),
              patch.object(sys, "argv", ["ad", "--wiley", url]),
              patch.object(ad, "sync_playwright"), patch.object(ad, "connect_dedicated_context"),
              patch.object(ad, "find_browser_exe", return_value="browser"),
              patch.object(ad, "_download_routed", side_effect=download),
              patch.object(ad, "download_supplements", return_value=(0, ""))):
            self.assertEqual(ad.main(), ad.EXIT_SUCCESS)
        self.assertEqual(ad.BROWSER_PORT, ad.MAIN_BROWSER_PORT)

    def test_queue_button_keeps_subdomain_and_other_publishers(self):
        import app
        doi = "10.1029/example"
        url = "https://agupubs.onlinelibrary.wiley.com/doi/" + doi
        text = url.replace("/doi/", "/doi/full/") + "?from=list#section" + "\n" + doi + "\n10.1016/other"
        with (tempfile.TemporaryDirectory() as tmp,
              patch.object(app, "QUEUE_PATH", Path(tmp) / "queue.json"),
              patch.object(app, "S", app.State()), patch.object(app, "_report"),
              patch.object(app, "start_warmup") as warmup,
              patch.object(app, "spawn_downloader", side_effect=lambda *a, **kw:
                           Mock(stdout=io.StringIO("RESULT|SUCCESS|example.pdf\n"))) as worker):
            app.S.settings.update(pause_sec=0, daily_limit=0)
            app.S.add_entries(["10.1111/retry", "10.1002/done", "10.1007/later"])
            app.S.items[0]["status"] = "no_access"
            app.S.items[1]["status"] = "success"
            self.assertTrue(app.handle_api("/api/parse", {"text": text})["wiley"])
            app.handle_api("/api/start_wiley", {"text": text})
            self.wait_for_queue(app)
            self.assertEqual([c.args[0] for c in worker.call_args_list],
                             [["--wiley", "10.1111/retry"], ["--wiley", url]])
            self.assertEqual([i["status"] for i in app.S.items],
                             ["success", "success", "pending", "success", "pending"])
            self.assertEqual(app.S.items[3]["query"], doi)
            app.handle_api("/api/start_wiley", {"text": text})
            self.assertEqual(worker.call_count, 2)
            warmup.assert_not_called()

            app.S.add_entries(["10.1111/unrelated"])
            file = Path(tmp) / "inbox.txt"
            file.write_text("10.1111/in-list\n10.1007/later", encoding="utf-8")
            with patch.object(app, "INBOX", Path(tmp)):
                app.handle_api("/api/queue_file", {"name": file.name, "wiley_only": True})
                self.wait_for_queue(app)
            self.assertEqual(worker.call_count, 3)
            self.assertEqual(worker.call_args.args[0], ["--wiley", "10.1111/in-list"])
            self.assertEqual(app.S.next_pending()["query"], "10.1007/later")

    def wait_for_queue(self, app):
        deadline = time.monotonic() + 5
        while app.S.running and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertFalse(app.S.running)

    def test_login_uses_separate_profile_only_for_wiley(self):
        ad = self.ad
        main_profile = ad.browser_profile_dir()

        def browser_details(urls):
            return ad.BROWSER_PORT, ad.dedicated_browser()[0], ad.browser_profile_dir()

        with (patch.object(ad, "find_browser_exe", return_value="browser"),
              patch.object(ad, "open_in_dedicated_browser", side_effect=browser_details)):
            with patch.object(sys, "argv", ["ad", "--wiley", "--open", "https://example.com/"]):
                port, kind, profile = ad.main()
                self.assertEqual((port, kind), (ad.WILEY_BROWSER_PORT, "chrome"))
                self.assertNotEqual(profile, main_profile)
            with patch.object(sys, "argv", ["ad", "--open", "https://example.com/"]):
                self.assertEqual(ad.main(), (ad.MAIN_BROWSER_PORT, "edge", main_profile))

    def test_wiley_uses_own_endpoint_even_if_main_browser_setting_changed(self):
        ad = self.ad
        with patch.object(ad, "find_browser_exe", return_value="browser"):
            self.assertFalse(ad.use_wiley_chrome("elsevier"))
            with patch.dict(os.environ, ARTICLE_BROWSER="chrome"):
                self.assertTrue(ad.use_wiley_chrome("wiley"))
            with patch.dict(os.environ, ARTICLE_WILEY_CHROME="0"):
                self.assertFalse(ad.use_wiley_chrome("wiley"))


if __name__ == "__main__":
    unittest.main()
