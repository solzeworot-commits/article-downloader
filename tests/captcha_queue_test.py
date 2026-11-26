"""Run with python tests/captcha_queue_test.py portable/scripts."""
import io
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, sys.argv.pop(1))
from contextlib import ExitStack


class CaptchaQueueTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(os.environ, ARTICLE_LOG_DIR=temp.name,
                                           ARTICLE_PDF_DIR=temp.name))
        import article_downloader as ad
        import app
        self.ad, self.app = ad, app
        self.stack.enter_context(patch.object(app, "QUEUE_PATH", Path(temp.name) / "queue.json"))
        self.stack.enter_context(patch.object(app, "S", app.State()))

    def test_attachments_survive_closed_article_and_manual_retry(self):
        app = self.app
        app.S.add_entries(["10.1016/paid"])
        row = app.S.items[0]
        with (patch.object(app, "spawn_downloader", return_value=Mock(stdout=io.StringIO(
                  "SUPPLEMENTS|1|data_folder\nRESULT|NO_ACCESS|Subscription required\n"))) as spawn,
              patch.object(app, "_report")):
            app._run_item(row)
            self.assertEqual((row["status"], row["file"], row["supp"]), ("no_access", "", 1))
            spawn.return_value = Mock(stdout=io.StringIO("SUPPLEMENTS|2|data_folder\nRESULT|SUCCESS|data_folder\n"))
            app.handle_api("/api/start_supplements", {"id": row["id"]})
            deadline = time.monotonic() + 5
            while app.S.running and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertFalse(app.S.running)
            self.assertIn("--supplements-only", spawn.call_args.args[0])
            self.assertEqual((row["status"], row["message"], row["attempts"], row["file"]),
                             ("no_access", "Subscription required", 1, ""))
            self.assertEqual((row["supp"], row["supp_dir"]), (2, "data_folder"))
            self.assertEqual(app.S.day_count, 1)

    def test_wiley_transfer_extends_queue_deadline(self):
        app = self.app
        app.S.settings.update(job_timeout_min=2)
        app.S.add_entries(["10.1002/slow"])
        clock = [0]
        transfer, consumed = threading.Event(), threading.Event()
        def output():
            yield "PUBLISHER|wiley\n"
            transfer.wait(3)
            yield "WILEY_TRANSFER|progress\n"
            consumed.set()
            yield "RESULT|SUCCESS|slow.pdf\n"
        proc = Mock(stdout=output())
        def wait(timeout):
            if clock[0] == 0:
                clock[0] = 90
                transfer.set()
                assert consumed.wait(3)
                clock[0] = 160
                raise app.subprocess.TimeoutExpired("job", timeout)
            return 0
        proc.wait.side_effect = wait
        with (patch.object(app.time, "monotonic", side_effect=lambda: clock[0]),
              patch.object(app, "spawn_downloader", return_value=proc),
              patch.object(app, "child_pids", return_value=set()),
              patch.object(app, "kill_job_tree") as kill, patch.object(app, "_report")):
            app._run_item(app.S.items[0])
            kill.assert_not_called()
        self.assertEqual(app.S.items[0]["status"], "success")

    def test_timeout_defers_one_item_then_finishes(self):
        app = self.app
        app.S.settings.update(pause_sec=0, daily_limit=0)
        app.S.add_entries(["first", "second"])
        def worker(args, deadline_sec):
            result = "CAPTCHA_REQUIRED|timeout" if args == ["first"] else "SUCCESS|second.pdf"
            return Mock(stdout=io.StringIO("RESULT|" + result + "\n"))
        with (patch.object(app, "spawn_downloader", side_effect=worker) as spawn,
              patch.object(app, "start_warmup") as warmup, patch.object(app, "_report")):
            app._runner()
        self.assertEqual([call.args[0] for call in spawn.call_args_list],
                         [["first"], ["second"], ["first"]])
        warmup.assert_not_called()
        self.assertEqual([item["status"] for item in app.S.items], ["captcha", "success"])

    def test_elsevier_hold_survives_restart_and_resumes(self):
        app = self.app
        blocked = "10.1016/example"
        link = "https://www.sciencedirect.com/science/article/pii/EXAMPLE"
        other = "10.1007/example"
        app.S.settings.update(pause_sec=0, daily_limit=0)
        app.S.add_entries([blocked, link, other])
        def worker(args, deadline_sec):
            result = "CAPTCHA_REQUIRED|Elsevier: timeout" if args == [blocked] else "SUCCESS|example.pdf"
            return Mock(stdout=io.StringIO("RESULT|" + result + "\n"))
        with (patch.object(app, "spawn_downloader", side_effect=worker) as spawn,
              patch.object(app, "_report") as report):
            app._runner()
            self.assertEqual([c.args[0] for c in spawn.call_args_list], [[blocked], [other]])
            self.assertEqual(report.call_count, 1)
        app.S = app.State()
        app.S.load_queue()
        self.assertEqual(app.S.elsevier_waiting(), 2)
        self.assertEqual([i["attempts"] for i in app.S.items], [1, 0, 1])
        app.S.add_entries(["https://doi.org/10.1016/another"])
        self.assertIsNone(app.S.next_pending())
        self.assertEqual(app.S.elsevier_waiting(), 3)
        app.S.settings.update(pause_sec=0, daily_limit=0)
        with (patch.object(app, "spawn_downloader", side_effect=lambda *a, **k:
                           Mock(stdout=io.StringIO("RESULT|SUCCESS|example.pdf\n"))) as spawn,
              patch.object(app, "start_warmup") as warmup, patch.object(app, "_report")):
            app.handle_api("/api/resume_elsevier", {})
            deadline = time.monotonic() + 5
            while app.S.running and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertFalse(app.S.running)
            warmup.assert_not_called()
            self.assertEqual(spawn.call_count, 3)
        self.assertTrue(all(i["status"] == "success" for i in app.S.items))

    def test_old_terminal_captcha_is_recovered(self):
        app = self.app
        app.S.add_entries(["10.1016/old", "10.1007/other"])
        for i in app.S.items:
            i["status"] = "captcha"
        app.S.save_queue()
        app.S.load_queue()
        self.assertEqual(app.S.elsevier_waiting(), 1)
        self.assertEqual([i["status"] for i in app.S.items], ["pending", "captcha"])

    def test_redirect_needs_two_ready_readings(self):
        ad = self.ad
        states = ["captcha", "loading", "ok", "captcha", "ok", "ok"]
        with (patch.object(ad, "_verification_state", side_effect=[(s, "") for s in states]) as probe,
              patch.object(ad.time, "sleep")):
            ad.wait_for_manual_verification(Mock(), "test")
        self.assertEqual(probe.call_count, len(states))

    def test_prompt_clears_during_redirect_and_after_timeout(self):
        ad = self.ad
        states = ["checking", "checking", "captcha", "loading", "ok", "ok"]
        with (patch.object(ad, "_verification_state", side_effect=[(s, "") for s in states]),
              patch.object(ad.time, "sleep"), patch.object(ad, "safe_console_print") as emit):
            ad.wait_for_manual_verification(Mock(), "test")
        self.assertEqual([c.args[0] for c in emit.call_args_list],
                         ["VERIFICATION|checking", "VERIFICATION|manual", "VERIFICATION|clear"])
        with (patch.object(ad, "_verification_state", return_value=("captcha", "")),
              patch.object(ad.time, "monotonic", side_effect=[0, 0, 0, 2]),
              patch.object(ad.time, "sleep"), patch.object(ad, "safe_console_print") as emit,
              self.assertRaises(ad.CaptchaRequired)):
            ad.wait_for_manual_verification(Mock(), "test", timeout_sec=1)
        self.assertEqual([c.args[0] for c in emit.call_args_list],
                         ["VERIFICATION|manual", "VERIFICATION|clear"])

    def test_held_queue_and_closed_warmup_do_not_signal_live_captcha(self):
        app = self.app
        app.S.items = [{"id": 1, "query": "10.1016/test", "status": "pending", "elsevier_hold": True}]
        app.S.warm["bases"] = {"elsevier": {"state": "captcha"}}
        for running, skipped, finished, expected in [(True, False, False, True),
                (True, True, False, False), (True, False, True, False), (False, False, False, False)]:
            app.S.warm.update(running=running, skipped=skipped, observations_done=finished)
            status = app.cloud_work_status(Path("."), {})
            self.assertEqual(status["captcha"], expected)
            self.assertTrue(status["elsevier_waiting"])

    def test_tab_closed_while_waiting(self):
        ad = self.ad
        with (patch.object(ad, "_verification_state", side_effect=[("captcha", ""), ("closed", "")]),
              self.assertRaises(ad.CaptchaRequired)):
            ad.wait_for_manual_verification(Mock(), "test")


if __name__ == "__main__":
    unittest.main()
