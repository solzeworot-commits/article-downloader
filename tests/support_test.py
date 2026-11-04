"""
Update check and problem report (support.py): python support_test.py <portable/scripts>

Starts the fake API (it serves version.json like the release page) on its own
port, checks the version comparison, the masking of keys / tokens / e-mail /
the Windows user name in reports, and that the GitHub link stays short
enough. Run by the Windows workflow.
"""
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, sys.argv[1])
sys.path.insert(0, str(HERE))
import support as sp  # noqa: E402
import fake_api  # noqa: E402

PORT = 8017


def check(cond, what):
    if not cond:
        raise SystemExit("support test FAILED: " + what)


# versions
check(sp.version_tuple("7.10") > sp.version_tuple("7.9"), "7.10 > 7.9")
check(sp.version_tuple("7.2") > sp.version_tuple("7.1"), "7.2 > 7.1")
check(sp.version_tuple("v7.2") == sp.version_tuple("7.2"), "v prefix")
check(not sp.version_tuple("7.2") > sp.version_tuple("7.2"), "same version")

# update check against the fake release page
server = fake_api.start(PORT)
try:
    newer = sp.check_update("7.2", f"http://127.0.0.1:{PORT}/version.json")
    check(newer.get("newer") is True and newer["latest"] == "9.9", f"newer: {newer}")
    same = sp.check_update("9.9", f"http://127.0.0.1:{PORT}/version.json")
    check(same.get("newer") is False, f"same: {same}")
    missing = sp.check_update("7.2", f"http://127.0.0.1:{PORT}/nothing.json", timeout=3)
    check("error" in missing and not missing.get("newer"), f"missing: {missing}")
finally:
    server.shutdown()

# report: secrets and personal data are masked
home = str(Path.home())
with tempfile.TemporaryDirectory() as tmp:
    log = Path(tmp) / "app.log"
    log.write_text("\n".join([
        "[2026-11-04 10:00:00] Started",
        f"[2026-11-04 10:00:01] PDF saved to {home}\\Downloads\\paper.pdf",
        "[2026-11-04 10:00:02] Scopus key apikey=0123456789abcdef0123456789abcdef checked",
        "[2026-11-04 10:00:03] X-ELS-APIKey: abcdefabcdefabcdefabcdefabcdef12",
        "[2026-11-04 10:00:04] window http://127.0.0.1:17865/?t=SECRETtoken123456",
        "[2026-11-04 10:00:05] mail me at ivan.petrov@example.com",
        "[2026-11-04 10:00:06] other C:\\Users\\Ivan\\Desktop\\list.txt",
        "[2026-11-04 10:00:07] DOI 10.1016/j.foodchem.2020.127845 done",
    ]) + "\n", encoding="utf-8")
    report = sp.build_report("7.2", "Скачивание статей", "Не качается статья", browser="Edge", logs=[log])
    text = sp.report_text(report)
    for secret in ("0123456789abcdef0123456789abcdef", "abcdefabcdefabcdefabcdefabcdef12",
                   "SECRETtoken123456", "ivan.petrov@example.com", "\\Ivan\\"):
        check(secret not in text, f"leaked {secret!r}")
    if len(home) > 3:
        check(home not in text, "home folder leaked")
    check("10.1016/j.foodchem.2020.127845" in text, "DOI kept")
    check("Версия программы: 7.2" in text and "Не качается статья" in text, "head")

    saved = sp.save_report(Path(tmp), text)
    check(saved.read_bytes().startswith(b"\xef\xbb\xbf"), "report file has BOM")

    big = dict(report, log="\n".join(f"строка журнала номер {i} " + "ы" * 120 for i in range(400)))
    url = sp.issue_url("https://github.com/x/y/issues/new", big)
    check(len(url) <= sp.MAX_URL, f"url too long: {len(url)}")
    check("title=" in url and "body=" in url, "url parts")

print("support tests: OK")
