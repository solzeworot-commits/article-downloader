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

    # automatic update: the zip is downloaded and its checksum checked
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "update.zip"
        seen = []
        sp.download_update(newer["zip"], dest, newer["sha256"], lambda d, t: seen.append((d, t)))
        check(dest.read_bytes() == fake_api.UPDATE_ZIP, "downloaded zip")
        check(seen and seen[-1][0] == len(fake_api.UPDATE_ZIP), f"progress: {seen[-1:]}")
        try:
            sp.download_update(newer["zip"], Path(tmp) / "bad.zip", "0" * 64)
            check(False, "a wrong checksum must fail")
        except sp.UpdateError:
            check(not (Path(tmp) / "bad.zip").exists() and not (Path(tmp) / "bad.zip.part").exists(),
                  "damaged download removed")

        # v8.8.2: the checksum remembered at the start is old (the release was published again
        # since): version.json is read again right before the download
        stale = dict(newer, sha256="0" * 64)
        used = sp.fetch_update("7.2", f"http://127.0.0.1:{PORT}/version.json", stale, newer["zip"],
                               Path(tmp) / "fresh.zip")
        check(used.get("sha256") == newer["sha256"], f"fresh checksum used: {used}")
        check((Path(tmp) / "fresh.zip").read_bytes() == fake_api.UPDATE_ZIP, "fresh zip")
        # version.json out of reach and the remembered checksum wrong: still refused (after a second try)
        try:
            sp.fetch_update("7.2", f"http://127.0.0.1:{PORT}/nothing.json", stale, newer["zip"],
                            Path(tmp) / "never.zip")
            check(False, "a wrong checksum must still fail")
        except sp.ChecksumError:
            check(not (Path(tmp) / "never.zip").exists(), "nothing left behind")

    # a connection cut in the middle of the zip: a clear error, nothing left behind
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Cut(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", "1000000")
            self.end_headers()
            self.wfile.write(b"x" * 1000)        # then the connection closes

    cut = ThreadingHTTPServer(("127.0.0.1", PORT + 1), Cut)
    threading.Thread(target=cut.serve_forever, daemon=True).start()
    with tempfile.TemporaryDirectory() as tmp:
        try:
            sp.download_update(f"http://127.0.0.1:{PORT + 1}/a.zip", Path(tmp) / "cut.zip", "")
            check(False, "a cut download must fail")
        except sp.UpdateError as exc:
            check(not isinstance(exc, sp.ChecksumError), f"cut: not a checksum error: {exc}")
            check(not list(Path(tmp).iterdir()), "cut: nothing left behind")
    cut.shutdown()
finally:
    server.shutdown()

# the updater script: ASCII (PowerShell 5 reads it in the ANSI code page),
# waits for the program, copies with robocopy, starts the program again
ps1 = sp.UPDATER_PS1
check(ps1.isascii(), "updater script must be ASCII")
for part in ("Wait-Process -Id $AppPid", "Expand-Archive", "robocopy $src $Root /E", "pythonw.exe", "update.log"):
    check(part in ps1, f"updater: {part}")

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
