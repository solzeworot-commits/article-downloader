import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "portable" / "scripts"))
from personal_alerts import PersonalAlerts


class PersonalAlertsTest(unittest.TestCase):
    def test_settings_only_expose_provider_and_survive_restart(self):
        with tempfile.TemporaryDirectory() as tmp, patch("personal_alerts.relay_request") as relay:
            path = Path(tmp) / "user" / "private.json"
            alerts = PersonalAlerts(path)
            alerts.manual()
            relay.assert_not_called()
            with self.assertRaises(ValueError):
                alerts.bind("https://example.org/pair#" + "a" * 32)
            relay.assert_not_called()
            origin = "https://article-downloader-notify.test.workers.dev"
            relay.return_value = {"provider":"telegram"}
            alerts.bind(origin + "/pair#" + "a" * 32)
            self.assertEqual(alerts.status(), {"provider":"telegram", "note":""})
            alerts = PersonalAlerts(path)
            self.assertEqual(alerts.status()["provider"], "telegram")
            token = relay.call_args.args[2]
            self.assertEqual(alerts.send(alerts.binding, test=True), "sent")
            relay.assert_called_with(origin, "/test", token)
            alerts.unlink()
            relay.assert_called_with(origin, "/unlink", token)
            self.assertFalse(path.exists())
            self.assertEqual(PersonalAlerts(path).status()["provider"], "")


if __name__ == "__main__":
    unittest.main()
