# -*- coding: utf-8 -*-
"""
ArticleDownloader PORTABLE v6.0 (v5.24 STABLE CDP + JOB DEADLINE + TITLE SEARCH)
v6.0:  portable build driven by app.py (window with the queue). The dedicated
       browser is Yandex Browser, Edge or Chrome (ARTICLE_BROWSER, "auto"
       prefers Yandex). --warmup opens every database first so CAPTCHAs are
       passed before the queue starts; --open URL opens a tab in the
       dedicated browser (institution sign-in); --restart-browser.
v5.24: "title: <title> || <year> || <journal> || #<no>" searches the open web
       by title (CyberLeninka, OpenAlex, Europe PMC, Crossref, Bing);
       a DOI found that way goes through the normal publisher routes.
       Found files must match the title (search heading, page title and the
       PDF text, word by word); misses go to pdf/_rejected.
       Supporting-information PDFs are never taken for the article.
       A direct PDF on an unreachable host is taken from the Wayback Machine.
       CNKI: a failing session page no longer stops a direct article link.
       ARTICLE_PDF_DIR / ARTICLE_LOG_DIR override the output folders.
v5.23: after a Cloudflare check the real page is awaited and the PDF link
       search is repeated for up to 20 s (unless a paywall is visible);
       DOI registry URLs are stripped of trailing whitespace.
v5.22: article pages are searched for PDF links only after they have loaded
       (up to 25 s) instead of 2-3 s after the first response bytes.
v5.21 fixes:
  - stale/hung tabs in the dedicated Yandex profile are closed before a
    CDP attach is retried; a hard restart no longer restores those tabs
  - all long manual waits respect ARTICLE_JOB_DEADLINE set by the watcher,
    so a CAPTCHA ends as CAPTCHA_REQUIRED (inbox .blocked) instead of the
    watcher killing the job and moving it to failed
  - DOI resolution falls back to the DOI/Handle registry and Crossref API
    when doi.org redirects are reset
  - 10.1016 DOIs go to Elsevier again when DOI resolution is unavailable
  - Springer 10.1007/978-... DOIs open /chapter/ or /book/, not /article/
Supported:
  - Springer / link.springer.com
  - Nature / Springer Nature article pages
  - Elsevier / ScienceDirect
  - Wiley Online Library
  - Oxford Academic
  - MDPI
  - ACS Publications
  - Frontiers
  - Taylor & Francis Online (downloads when the article is accessible)
  - CNKI Overseas via CHN DOI and direct CNKI article/URLID links
  - eLIBRARY.RU item.asp + open-web fallback (Crossref/OpenAlex/Bing)
  - search by title (title: ...) on the open web
  - Generic DOI fallback for other publishers after doi.org resolution

Browser routes:
  - all supported browser routes -> dedicated browser, CDP port 9223

No CAPTCHA solving/bypass is used. If a supported site asks for human
verification, the real browser is brought to the foreground and the script
waits for manual completion.
"""

from __future__ import annotations

import argparse
import base64
import csv
import difflib
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path
from urllib.parse import urljoin, urlparse, unquote, quote_plus, quote, parse_qs

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout


SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
# Overrides let a second computer run the CLI without writing into the
# OneDrive pdf/logs folders the work PC uses (OneDrive turns two writers of
# one log into "-<PC name>" conflict copies).
PDF_DIR = Path(os.environ.get("ARTICLE_PDF_DIR") or ROOT / "pdf")
LOG_DIR = Path(os.environ.get("ARTICLE_LOG_DIR") or ROOT / "logs")

PDF_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

EDGE_CDP_URL = "http://127.0.0.1:9223"
BROWSER_CDP_URL = "http://127.0.0.1:9223"

EXIT_SUCCESS = 0
EXIT_FAILURE = 1
EXIT_CAPTCHA = 10
EXIT_NO_ACCESS = 11
EXIT_UNSUPPORTED = 12


class DownloadError(RuntimeError):
    pass


class CaptchaRequired(DownloadError):
    pass


class NoAccess(DownloadError):
    pass


class UnsupportedSource(DownloadError):
    pass


def safe_console_print(text: str):
    """
    Windows watcher/Task Scheduler may start Python with a legacy console
    encoding (for example cp1251/cp866). CNKI UI labels contain Chinese
    characters, so a normal print() can raise UnicodeEncodeError and abort the
    download immediately before the click. Never let console encoding kill a
    download job.
    """
    try:
        print(text, flush=True)
        return
    except UnicodeEncodeError:
        pass

    try:
        enc = getattr(sys.stdout, "encoding", None) or "ascii"
        safe = text.encode(enc, errors="backslashreplace").decode(
            enc, errors="replace"
        )
        print(safe, flush=True)
    except Exception:
        # Logging must never be fatal.
        try:
            print(text.encode("ascii", errors="backslashreplace").decode("ascii"), flush=True)
        except Exception:
            pass


def emit(status: str, message: str = ""):
    safe_console_print(f"RESULT|{status}|{message}")


def log_line(text: str):
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{stamp}] {text}"

    # Write the real Unicode text to the UTF-8 logfile first.
    try:
        with (LOG_DIR / "article_downloader.log").open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

    # Then mirror it to the console safely.
    safe_console_print(line)


def sanitize_filename(value: str) -> str:
    value = re.sub(r'[<>:"/\\|?*]+', "_", value)
    value = re.sub(r"\s+", "_", value).strip("._ ")
    return value[:180] or "article"


def next_destination(prefix: str, identity: str) -> Path:
    stem = sanitize_filename(f"{prefix}_{identity}")
    path = PDF_DIR / f"{stem}.pdf"
    n = 2
    while path.exists():
        path = PDF_DIR / f"{stem}_{n}.pdf"
        n += 1
    return path


def valid_pdf(path: Path) -> bool:
    try:
        if not path.is_file() or path.stat().st_size <= 1000:
            return False

        with path.open("rb") as f:
            if f.read(5) != b"%PDF-":
                return False
            size = path.stat().st_size
            f.seek(max(0, size - 65536))
            tail = f.read()

        return re.search(
            rb"startxref\s+\d+\s+%%EOF[ \t\r\n\x00]*\Z",
            tail
        ) is not None
    except Exception:
        return False


def extract_doi(text: str) -> str | None:
    if not text:
        return None

    raw = unquote(text.strip())

    # DOI URLs
    raw = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", raw, flags=re.I)

    m = re.search(r"(10\.\d{4,9}/[^\s<>\"'\]]+)", raw, flags=re.I)
    if not m:
        return None

    doi = m.group(1)

    # Remove URL query/fragment if it got captured after the DOI.
    doi = doi.split("?")[0].split("#")[0]

    # Typical trailing punctuation copied from prose.
    doi = doi.rstrip(".,;:)]}")

    return doi


def looks_like_captcha(page) -> bool:
    words = (
        "verify you are human",
        "checking your browser",
        "security verification",
        "cloudflare",
    )

    try:
        title = page.title().lower()
    except Exception:
        title = ""

    try:
        body = page.locator("body").inner_text(timeout=5000).lower()
    except Exception:
        body = ""

    hay = title + "\n" + body
    return any(w in hay for w in words)


def job_bounded_wait(timeout_sec: int, margin_sec: int = 25) -> int:
    """
    v5.21: the watcher kills a job at ARTICLE_JOB_DEADLINE (epoch seconds).
    A killed job is reported as FAILED and leaves its browser tabs open, so
    every long wait must end early enough for a clean exit (CAPTCHA_REQUIRED,
    NO_ACCESS, ...) and for the finally blocks that close the tabs.
    """
    raw = os.environ.get("ARTICLE_JOB_DEADLINE", "").strip()
    if not raw:
        return timeout_sec
    try:
        left = int(float(raw) - time.time()) - margin_sec
    except ValueError:
        return timeout_sec
    return max(0, min(timeout_sec, left))


def wait_for_manual_verification(page, source: str, timeout_sec: int = 900):
    """
    If the site presents a human-verification page, keep the real browser open
    and wait for the user to complete it manually. No CAPTCHA solving or bypass.

    Returns when the verification page disappears. Raises CaptchaRequired only
    after the timeout expires.
    """
    if not looks_like_captcha(page):
        return

    timeout_sec = job_bounded_wait(timeout_sec)
    if timeout_sec <= 0:
        raise CaptchaRequired(
            f"{source}: human verification required; no job time left to wait"
        )

    try:
        page.bring_to_front()
    except Exception:
        pass

    log_line(
        f"{source}: human verification detected. "
        f"Waiting up to {timeout_sec} seconds for manual completion."
    )
    print("", flush=True)
    print("=" * 70, flush=True)
    print(f"{source}: НУЖНО ВРУЧНУЮ ПРОЙТИ ПРОВЕРКУ В ОКНЕ БРАУЗЕРА ЗАГРУЗЧИКА", flush=True)
    print(
        f"Скрипт НЕ ОСТАНОВЛЕН. Он ждёт до {timeout_sec} секунд "
        "и продолжит автоматически после прохождения проверки.",
        flush=True,
    )
    print("=" * 70, flush=True)

    deadline = time.time() + timeout_sec
    last_print = 0

    while time.time() < deadline:
        if not looks_like_captcha(page):
            log_line(f"{source}: manual verification completed; continuing.")
            print(
                f"{source}: проверка пройдена — продолжаю автоматически.",
                flush=True,
            )
            # The check reloads the real page; give it time to arrive.
            page.wait_for_timeout(1500)
            wait_page_ready(page, source, 20)
            page.wait_for_timeout(1500)
            return

        remaining = int(deadline - time.time())

        # Avoid filling logs every few seconds.
        if time.time() - last_print >= 15:
            print(
                f"{source}: жду ручную проверку... "
                f"осталось примерно {remaining // 60} мин {remaining % 60} сек",
                flush=True,
            )
            last_print = time.time()

        page.wait_for_timeout(2000)

    raise CaptchaRequired(
        f"{source}: manual verification was not completed within "
        f"{timeout_sec} seconds"
    )


def _transient_navigation_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    markers = (
        "err_connection_reset",
        "err_name_not_resolved",
        "err_timed_out",
        "err_connection_timed_out",
        "err_connection_closed",
        "err_network_changed",
        "err_internet_disconnected",
        "err_address_unreachable",
        "err_proxy_connection_failed",
    )
    return any(m in msg for m in markers)


def _target_closed_error(exc: Exception) -> bool:
    msg = (type(exc).__name__ + ": " + str(exc)).lower()
    return (
        "targetclosederror" in msg
        or "target page, context or browser has been closed" in msg
        or "browser has been closed" in msg
        or "context has been closed" in msg
    )


def close_page_safely(page):
    """
    23.10: after a failed navigation to an unreachable host (connection
    closed) page.close() over CDP blocked for 20 minutes, until the tab was
    closed from outside. Close the target through the DevTools HTTP endpoint
    first; page.close() then returns at once.
    """
    try:
        session = page.context.new_cdp_session(page)
        target_id = session.send("Target.getTargetInfo")["targetInfo"]["targetId"]
        try:
            session.detach()
        except Exception:
            pass
        urllib.request.urlopen(f"{BROWSER_CDP_URL}/json/close/{target_id}", timeout=5).read()
    except Exception:
        pass
    try:
        page.close()
    except Exception:
        pass


def safe_goto(page, url: str, timeout: int = 60000, attempts: int = 3):
    """Navigate with bounded retries for transient DNS/network failures."""
    last_exc = None
    if os.environ.get("ARTICLE_SAFE_MODE") == "1":
        attempts = 1
        timeout = min(timeout, 15000)

    for attempt in range(1, max(1, attempts) + 1):
        try:
            return page.goto(url, wait_until="commit", timeout=timeout)
        except PWTimeout as exc:
            last_exc = exc
            if attempt >= attempts:
                log_line(f"Navigation timeout after {attempt} attempt(s): {url}")
                return None
            delay = 2 * attempt
            log_line(
                f"Navigation timeout; retry {attempt + 1}/{attempts} "
                f"after {delay}s: {url}"
            )
            time.sleep(delay)
        except Exception as exc:
            last_exc = exc
            if _transient_navigation_error(exc) and attempt < attempts:
                delay = 2 * attempt
                log_line(
                    f"Transient navigation error ({type(exc).__name__}: {exc}); "
                    f"retry {attempt + 1}/{attempts} after {delay}s"
                )
                time.sleep(delay)
                continue
            raise

    if last_exc:
        raise last_exc
    return None


def wait_page_ready(page, label: str, timeout_sec: int = 25):
    """
    v5.22: safe_goto() returns at 'commit', i.e. as soon as the first bytes
    arrive. Callers then slept 2-3 s and searched for PDF links; on a slow
    site the page was still empty, nothing was found and the tab was closed.
    """
    timeout_sec = job_bounded_wait(timeout_sec)
    if timeout_sec <= 0:
        return
    started = time.time()
    try:
        page.wait_for_load_state("domcontentloaded", timeout=timeout_sec * 1000)
    except Exception as exc:
        log_line(
            f"{label}: page not loaded after {timeout_sec}s "
            f"({type(exc).__name__}); searching anyway"
        )
        return
    waited = time.time() - started
    if waited >= 3:
        log_line(f"{label}: page loaded after {waited:.0f}s")


def _resolve_doi_http_fallback(doi: str) -> str | None:
    """Fast DOI resolution without waiting for a browser network timeout."""
    url = "https://doi.org/" + doi
    headers = {
        "User-Agent": "Mozilla/5.0 ArticleDownloader/5.16",
        "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.9,*/*;q=0.8",
    }

    for method in ("HEAD", "GET"):
        try:
            req = urllib.request.Request(url, headers=headers, method=method)
            with urllib.request.urlopen(req, timeout=6) as resp:
                final_url = resp.geturl()
                host = (urlparse(final_url).netloc or "").lower()
                if host and host not in ("doi.org", "www.doi.org", "dx.doi.org"):
                    log_line(f"DOI fast HTTP resolved to: {final_url}")
                    return final_url
        except urllib.error.HTTPError as exc:
            # Even a 403/401 after redirect is enough to identify the publisher.
            try:
                final_url = exc.geturl()
                host = (urlparse(final_url).netloc or "").lower()
                if host and host not in ("doi.org", "www.doi.org", "dx.doi.org"):
                    log_line(f"DOI fast HTTP resolved via HTTP {exc.code}: {final_url}")
                    return final_url
            except Exception:
                pass
            log_line(f"DOI fast HTTP {method} failed: HTTPError: {exc}")
        except Exception as exc:
            log_line(
                f"DOI fast HTTP {method} failed: {type(exc).__name__}: {exc}"
            )

    return _resolve_doi_registry(doi)


def _resolve_doi_registry(doi: str) -> str | None:
    """
    v5.21: ask the DOI/Handle registry (and Crossref) for the registered
    landing URL. This never contacts the publisher and works when the
    doi.org redirect itself is reset (ERR_CONNECTION_RESET in older logs).
    """
    headers = {
        "User-Agent": "Mozilla/5.0 ArticleDownloader/5.21",
        "Accept": "application/json",
    }
    path = quote(doi, safe="/")

    for base in (
        "https://doi.org/api/handles/",
        "https://hdl.handle.net/api/handles/",
    ):
        try:
            req = urllib.request.Request(base + path, headers=headers)
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="replace"))
            for value in data.get("values") or []:
                if str(value.get("type", "")).upper() != "URL":
                    continue
                # Some registrations end with a newline (fishscichina.com).
                url = str((value.get("data") or {}).get("value") or "").strip()
                if url.startswith(("http://", "https://")):
                    log_line(f"DOI registry resolved to: {url}")
                    return url
        except Exception as exc:
            log_line(
                f"DOI registry {urlparse(base).netloc} failed: "
                f"{type(exc).__name__}: {exc}"
            )

    try:
        req = urllib.request.Request(
            "https://api.crossref.org/works/" + path, headers=headers
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        url = (
            ((data.get("message") or {}).get("resource") or {})
            .get("primary", {})
            .get("URL", "")
        ).strip()
        if url.startswith(("http://", "https://")):
            log_line(f"DOI Crossref resolved to: {url}")
            return url
    except Exception as exc:
        log_line(f"DOI Crossref lookup failed: {type(exc).__name__}: {exc}")

    return None


def resolve_doi(context, doi: str, wait_ms: int = 5000) -> str:
    # v5.16: resolve DOI cheaply first. Browser is a short fallback only.
    url = "https://doi.org/" + doi
    fallback = _resolve_doi_http_fallback(doi)

    if fallback and "chooser.crossref.org" in urlparse(fallback).netloc.lower():
        log_line("Crossref chooser detected; locating actual publisher")
        chooser = context.new_page()
        try:
            safe_goto(chooser, fallback, timeout=12000, attempts=1)
            chooser.wait_for_timeout(1200)
            links = chooser.locator("a[href]")
            for domain in ("theiet.org", "ieee.org"):
                found = False
                for i in range(min(links.count(), 80)):
                    href = links.nth(i).get_attribute("href") or ""
                    target = urljoin(chooser.url, href)
                    host = urlparse(target).netloc.lower()
                    if host == domain or host.endswith("." + domain):
                        fallback = target
                        log_line(f"Crossref publisher selected: {fallback}")
                        found = True
                        break
                if found:
                    break
        finally:
            chooser.close()

        if "chooser.crossref.org" in urlparse(fallback).netloc.lower():
            raise DownloadError("CROSSREF_CHOOSER_NO_PUBLISHER_LINK")

    if fallback:
        return fallback

    if os.environ.get("ARTICLE_SAFE_MODE") == "1":
        log_line("RESCUE_SAFE_MODE: DOI HTTP resolution failed; skipping browser fallback")
        return url

    page = context.new_page()
    try:
        log_line(f"Resolve DOI browser fallback: {url}")
        try:
            safe_goto(page, url, 15000, attempts=1)
        except Exception as exc:
            log_line(
                f"DOI browser fallback failed fast: {type(exc).__name__}: {exc}"
            )
            return url

        deadline = time.time() + 6
        final_url = page.url or url
        while time.time() < deadline:
            final_url = page.url or final_url
            try:
                host = (urlparse(final_url).netloc or "").lower()
            except Exception:
                host = ""
            if host and host not in ("doi.org", "www.doi.org", "dx.doi.org"):
                if host == "chooser.crossref.org":
                    log_line("CROSSREF_BROWSER_CHOOSER: selecting publisher")
                    for _ in range(12):
                        try:
                            links = page.locator("a[href]")
                            for domain in ("theiet.org", "ieee.org"):
                                for i in range(min(links.count(), 80)):
                                    href = links.nth(i).get_attribute("href") or ""
                                    target = urljoin(final_url, href)
                                    ph = urlparse(target).netloc.lower()
                                    if ph == domain or ph.endswith("." + domain):
                                        log_line(f"CROSSREF_PUBLISHER_SELECTED: {target}")
                                        return target
                        except Exception as exc:
                            log_line(f"Crossref link scan: {exc}")
                        page.wait_for_timeout(500)
                    raise DownloadError("CROSSREF_PUBLISHER_LINK_NOT_FOUND")
                log_line(f"DOI browser fallback resolved to: {final_url}")
                return final_url
            page.wait_for_timeout(500)

        log_line(f"DOI unresolved fast; final URL: {final_url}")
        return final_url
    finally:
        try:
            close_page_safely(page)
        except Exception:
            pass


def classify_direct(value: str, doi: str | None) -> str | None:
    low = unquote(value).lower()

    if doi:
        d = doi.lower()
        if d.startswith("10.13386/") or d.startswith("10.16043/"):
            return "cnki"

    if "tandfonline.com" in low:
        return "taylor"
    if "sciencedirect.com" in low or "elsevier.com" in low:
        return "elsevier"
    if "link.springer.com" in low or "springer.com" in low:
        return "springer"
    if "nature.com" in low or "biomedcentral.com" in low:
        return "nature"
    if "onlinelibrary.wiley.com" in low:
        return "wiley"
    if "academic.oup.com" in low or "oup.silverchair-cdn.com" in low:
        return "oxford"
    if "mdpi.com" in low:
        return "mdpi"
    if "pubs.acs.org" in low:
        return "acs"
    if "frontiersin.org" in low or "frontiersin.com" in low:
        return "frontiers"
    if "oversea.cnki.net" in low or "chndoi.org" in low:
        return "cnki"
    if "elibrary.ru" in low:
        return "elibrary"

    if doi:
        d = doi.lower()
        if d.startswith("10.1007/"):
            return "springer"
        if d.startswith("10.1038/"):
            return "nature"
        # A 10.1016 DOI may land on a journal-operated site (e.g. arabjchem.org).
        # Resolve the DOI and classify the actual landing host first.
        if d.startswith("10.3390/"):
            return "mdpi"
        if d.startswith("10.1021/"):
            return "acs"
        if d.startswith("10.3389/"):
            return "frontiers"
        if d.startswith("10.1093/"):
            return "oxford"
        if d.startswith("10.1080/"):
            return "taylor"

        # Intentionally do not force 10.1002/ or 10.1111/ to Wiley.
        # Migrated titles can now resolve to Oxford Academic or elsewhere.

    return None



def probe_cnki(context, doi: str) -> bool:
    page = context.new_page()
    try:
        url = "https://www.chndoi.org/Resolution/Handler?doi=" + doi
        log_line(f"Probe CNKI via CHN DOI: {url}")
        safe_goto(page, url, 60000)

        deadline = time.time() + 25
        while time.time() < deadline:
            try:
                if page.locator('a[href*="link.oversea.cnki.net/doi/"]').count() > 0:
                    return True
            except Exception:
                pass
            page.wait_for_timeout(1000)

        return False
    finally:
        try:
            close_page_safely(page)
        except Exception:
            pass


def identify_source(context, raw: str, doi: str | None) -> tuple[str, str | None]:
    """
    Resolve known publishers normally. For an unknown publisher, do not reject
    the DOI immediately: if doi.org reaches a real publisher/article page,
    route it through the generic PDF finder using the same authorized Edge
    session. This is especially useful for SAGE, RSC, Scientific.Net, OJS
    journals and smaller publishers.
    """
    source = classify_direct(raw, doi)

    if source:
        if source == "elsevier" and doi and "sciencedirect.com" not in raw.lower():
            resolved = resolve_doi(context, doi)
            return source, resolved
        return source, raw if raw.startswith("http") else None

    if doi:
        resolved = resolve_doi(context, doi)
        source = classify_direct(resolved, doi)

        if source:
            return source, resolved

        host = (urlparse(resolved).netloc or "").lower()
        if (
            resolved
            and resolved.startswith("http")
            and host
            and host not in ("doi.org", "www.doi.org", "dx.doi.org")
        ):
            log_line(
                f"Unknown publisher after DOI resolution; "
                f"using generic PDF fallback: {host}"
            )
            return "generic", resolved

        # v5.21: before route1016, every 10.1016 DOI went to Elsevier without
        # any resolution. Keep that behaviour when the DOI cannot be resolved,
        # instead of ending as UNSUPPORTED / DOI_RESOLUTION_UNAVAILABLE.
        if doi.lower().startswith("10.1016/"):
            log_line("10.1016 DOI unresolved; using the Elsevier route")
            return "elsevier", None

        # SAFE RESCUE: when DOI service is unreachable, do not blindly probe CHN DOI.
        if os.environ.get("ARTICLE_SAFE_MODE") == "1":
            if raw.startswith(("http://", "https://")):
                raw_host = (urlparse(raw).hostname or "").lower()
                if raw_host and raw_host not in ("doi.org", "www.doi.org", "dx.doi.org"):
                    return "generic", raw
            raise DownloadError(f"DOI_RESOLUTION_UNAVAILABLE: {doi}")
        # If doi.org itself did not resolve use the old CNKI mirror probe.
        if not doi.lower().startswith("10.1016/") and probe_cnki(context, doi):
            return "cnki", None

    # Direct non-DOI publisher URL: also try the generic browser fallback.
    if raw.startswith("http"):
        host = (urlparse(raw).netloc or "").lower()
        if host and host not in ("doi.org", "www.doi.org", "dx.doi.org"):
            return "generic", raw

    raise UnsupportedSource(f"Источник не определён: {raw}")


# ---------------------------------------------------------------------------
# SPRINGER
# ---------------------------------------------------------------------------

def download_springer(context, raw: str, doi: str | None) -> Path:
    if doi:
        # Known Springer URLs and 10.1007 DOIs do not require doi.org.
        raw_host = (urlparse(raw).hostname or "").lower() if raw.startswith(("http://", "https://")) else ""
        if raw_host in ("link.springer.com", "springer.com"):
            article_url = raw
            log_line("Springer direct URL: skipping doi.org resolution")
        elif doi.lower().startswith("10.1007/"):
            # v5.21: 10.1007/978-... are book chapters/books. /article/<doi>
            # does not open them, which made every chapter end with
            # SPRINGER_PDF_CONTROL_NOT_FOUND.
            suffix = doi.split("/", 1)[1]
            if re.match(r"978-", suffix, flags=re.I):
                kind = "chapter" if "_" in suffix else "book"
            else:
                kind = "article"
            article_url = f"https://link.springer.com/{kind}/{doi}"
            log_line(f"Springer 10.1007 DOI: using direct publisher {kind} URL")
        else:
            resolved = resolve_doi(context, doi)
            host = (urlparse(resolved).netloc or "").lower()
            if "link.springer.com" in host or "springer.com" in host:
                article_url = resolved
            else:
                article_url = f"https://link.springer.com/article/{doi}"
        identity = doi
    else:
        article_url = raw
        identity = urlparse(raw).path.strip("/") or "springer"

    page = context.new_page()
    try:
        log_line(f"Springer open: {article_url}")
        safe_goto(page, article_url, 25000, attempts=2)
        wait_page_ready(page, "Springer")
        page.wait_for_timeout(2500)
        wait_for_manual_verification(page, "Springer", 900)

        if page.url:
            article_url = page.url

        dest = next_destination("Springer", identity)

        # Springer normally exposes a stable /content/pdf/<encoded DOI>.pdf route.
        if doi:
            direct_pdf = (
                "https://link.springer.com/content/pdf/"
                + quote(doi, safe="")
                + ".pdf"
            )
            log_line(f"Springer direct PDF fallback: {direct_pdf}")
            if capture_pdf_with_fetch(
                context, direct_pdf, dest, 20, source_label="Springer PDF"
            ):
                return dest
            try:
                dest.unlink()
            except Exception:
                pass

        # SPRINGER_PAYWALL_CHECK_V1
        # After the authorized direct-PDF attempt fails, avoid clicking the
        # publisher's paid 'Buy article PDF' button as a PDF download control.
        # A real PDF captured above always takes precedence.
        if re.search(r"\bbuy\s+article\s+pdf\b", page_text_lower(page, timeout=2500)):
            log_line("Springer paywall: Buy article PDF visible; no PDF access in current browser session")
            raise NoAccess("SPRINGER_SUBSCRIPTION_REQUIRED: Buy article PDF; check institutional entitlement")

        # Prefer citation metadata and actual PDF controls from the resolved page.
        candidates = discover_pdf_candidates(
            page, article_url, "springer", doi
        )

        for score, pdf_url, why in candidates:
            log_line(
                f"Springer PDF candidate score={score} reason={why}: {pdf_url}"
            )
            dest = next_destination("Springer", identity)
            if capture_pdf_with_fetch(
                context, pdf_url, dest, 20, source_label="Springer PDF"
            ):
                return dest
            try:
                dest.unlink()
            except Exception:
                pass

        # Last browser-level fallback for JS-driven PDF buttons.
        dest = next_destination("Springer", identity)
        if try_click_pdf_control(context, page, dest, "Springer"):
            return dest
        try:
            dest.unlink()
        except Exception:
            pass

        denied = access_denied_hint(page, "Springer")
        if denied:
            raise NoAccess(denied)

        raise DownloadError("SPRINGER_PDF_CONTROL_NOT_FOUND")

    finally:
        try:
            close_page_safely(page)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# WILEY
# ---------------------------------------------------------------------------

def wiley_viewer_evidence(page) -> dict:
    evidence = {
        "button": False,
        "page_indicator": False,
        "rendered": False,
    }

    try:
        evidence.update(
            page.evaluate(
                r"""
                () => {
                  const visible = e => {
                    if (!e) return false;
                    const s = getComputedStyle(e);
                    const r = e.getBoundingClientRect();
                    return s.display !== 'none' &&
                           s.visibility !== 'hidden' &&
                           parseFloat(s.opacity || '1') > 0 &&
                           r.width > 0 && r.height > 0;
                  };

                  const btn = document.querySelector('#new-download-btn');
                  const text = (document.body.innerText || '').replace(/\s+/g, ' ');

                  let rendered = false;
                  for (const e of document.querySelectorAll('canvas,img')) {
                    const r = e.getBoundingClientRect();
                    if (visible(e) && r.width > 400 && r.height > 400) {
                      rendered = true;
                      break;
                    }
                  }

                  return {
                    button: visible(btn),
                    page_indicator: /Page\s*1\s*\/\s*\d+/i.test(text),
                    rendered
                  };
                }
                """
            )
        )
    except Exception:
        pass

    if not evidence["rendered"]:
        for fr in page.frames:
            try:
                rendered = fr.evaluate(
                    r"""
                    () => {
                      const visible = e => {
                        const s = getComputedStyle(e);
                        const r = e.getBoundingClientRect();
                        return s.display !== 'none' &&
                               s.visibility !== 'hidden' &&
                               r.width > 0 && r.height > 0;
                      };
                      for (const e of document.querySelectorAll('canvas,img')) {
                        const r = e.getBoundingClientRect();
                        if (visible(e) && r.width > 400 && r.height > 400) return true;
                      }
                      return false;
                    }
                    """
                )
                if rendered:
                    evidence["rendered"] = True
                    break
            except Exception:
                pass

    return evidence


def wait_wiley_viewer(page, timeout_sec: int = 240):
    deadline = time.time() + job_bounded_wait(timeout_sec, margin_sec=60)
    stable = 0
    n = 0

    while time.time() < deadline:
        n += 1

        if looks_like_captcha(page):
            wait_for_manual_verification(page, "Wiley", 900)

        ev = wiley_viewer_evidence(page)

        # v5.3: the Download button is NOT required for viewer readiness.
        # Some Wiley articles fully render Page 1 but do not expose the
        # turquoise button. In v5.2 this caused a pointless ~3 minute wait
        # before the already-available direct pdfdirect fallback was tried.
        ready = ev["page_indicator"] or ev["rendered"]
        stable = stable + 1 if ready else 0

        log_line(
            f"Wiley wait {n}: button={ev['button']} "
            f"page1={ev['page_indicator']} rendered={ev['rendered']} "
            f"stable={stable}/3"
        )

        if stable >= 3:
            log_line(
                "Wiley viewer rendered; proceeding to direct pdfdirect route "
                f"(Download button {'present' if ev['button'] else 'not required'})."
            )
            page.wait_for_timeout(1200)
            return

        page.wait_for_timeout(2000)

    raise DownloadError("WILEY_VIEWER_TIMEOUT")


def find_wiley_pdfdirect(page, timeout_sec: int = 45):
    deadline = time.time() + timeout_sec

    while time.time() < deadline:
        for fr in page.frames:
            for selector in (
                'a[href*="/doi/pdfdirect/"][href*="download=true"]',
                'a[href*="/doi/pdfdirect/"]',
            ):
                try:
                    loc = fr.locator(selector)

                    for i in range(min(loc.count(), 30)):
                        el = loc.nth(i)
                        try:
                            if el.is_visible():
                                href = el.get_attribute("href") or ""
                                if "/doi/pdfdirect/" in href:
                                    return fr, el, href
                        except Exception:
                            pass
                except Exception:
                    pass

        page.wait_for_timeout(500)

    return None, None, None


def download_wiley(context, raw: str, doi: str | None, resolved_url: str | None) -> Path:
    """
    Wiley v5.8: click the REAL visible PDF/ePDF control first.

    Important for older Wiley pages such as 10.1002/jib.703:
    the article page visibly exposes a PDF toolbar link whose href is
    /doi/epdf/<DOI>. Directly guessing /pdfdirect before clicking that control
    can leave Wiley spinning on HTML/redirect pages.

    Flow:
      1) open the article page;
      2) find the visible toolbar anchor whose href contains /doi/epdf/ or
         /doi/pdf/ and the DOI;
      3) click that exact control;
      4) once the ePDF viewer/session is open, try the actual PDF routes;
      5) if needed, click the viewer Download button and capture pdfdirect;
      6) save only a real %PDF response.
    """
    if not doi:
        raise DownloadError("Wiley handler requires DOI")

    resolved = resolved_url or resolve_doi(context, doi)
    parsed = urlparse(resolved)

    if "onlinelibrary.wiley.com" not in parsed.netloc.lower():
        raise DownloadError(f"Wiley domain not resolved: {resolved}")

    host = f"{parsed.scheme or 'https'}://{parsed.netloc}"
    article_url = f"{host}/doi/abs/{doi}"
    epdf_url = f"{host}/doi/epdf/{doi}"
    pdf_url = f"{host}/doi/pdf/{doi}?download=true"
    pdfdirect_url = f"{host}/doi/pdfdirect/{doi}?download=true"

    page = context.new_page()

    def wiley_access_denied() -> bool:
        """
        Detect a genuine Wiley subscription/access block after the PDF/ePDF
        control has been opened. This prevents repeated retries when the
        institutional session simply has no entitlement to the article.
        """
        try:
            body = (page.locator("body").inner_text(timeout=3000) or "").lower()
        except Exception:
            return False

        phrases = (
            "you do not have access",
            "access denied",
            "purchase access",
            "purchase this article",
            "get access",
            "institutional access",
            "sign in to access",
            "subscribe to access",
            "this content is not available",
            "not entitled to access",
            "you are not authorized to access",
        )
        return any(p in body for p in phrases)

    def try_capture(url: str, label: str, timeout_s: int = 45) -> Path | None:
        dest = next_destination("Wiley", doi)
        log_line(f"{label}: {url}")

        if capture_pdf_with_fetch(
            context,
            url,
            dest,
            timeout_s,
            source_label="Wiley PDF",
        ):
            return dest

        try:
            dest.unlink()
        except Exception:
            pass
        return None

    try:
        log_line(f"Wiley article open: {article_url}")
        safe_goto(page, article_url, 60000)
        page.wait_for_timeout(2500)

        # Manual verification only; no bypass.
        wait_for_manual_verification(page, "Wiley", 900)

        # ------------------------------------------------------------------
        # PRIMARY v5.8 ROUTE: click the exact visible PDF/ePDF toolbar anchor.
        # ------------------------------------------------------------------
        pdf_control = None
        pdf_href = None

        for selector in (
            'a[href*="/doi/epdf/"]',
            'a[href*="/doi/pdf/"]',
            'a:has-text("PDF")',
        ):
            try:
                loc = page.locator(selector)
                for i in range(min(loc.count(), 80)):
                    el = loc.nth(i)
                    try:
                        if not el.is_visible():
                            continue
                        href = el.get_attribute("href") or ""
                        href_low = href.lower()
                        if doi.lower() not in href_low:
                            continue
                        if "/doi/epdf/" not in href_low and "/doi/pdf/" not in href_low:
                            continue
                        pdf_control = el
                        pdf_href = urljoin(page.url, href)
                        break
                    except Exception:
                        pass
                if pdf_control is not None:
                    break
            except Exception:
                pass

        if pdf_control is not None:
            log_line(f"Wiley visible PDF/ePDF control found: {pdf_href}")

            # Click the exact toolbar control. It usually opens the ePDF viewer.
            try:
                pdf_control.click(force=True, timeout=15000)
                page.wait_for_timeout(3500)
                log_line(f"Wiley after PDF control click: {page.url}")
            except Exception as exc:
                log_line(f"Wiley PDF control click error: {exc}")

            wait_for_manual_verification(page, "Wiley", 900)

            if wiley_access_denied():
                raise NoAccess(
                    "Wiley: institutional session has no access to this article"
                )

            # The click refreshes Wiley session/cookies. Now direct PDF routes
            # are much more reliable.
            for url, label in (
                (pdfdirect_url, "Wiley pdfdirect after visible PDF click"),
                (pdf_url, "Wiley /doi/pdf after visible PDF click"),
            ):
                result = try_capture(url, label, 45)
                if result is not None:
                    return result

            # If the ePDF viewer itself exposes a Download button, use it.
            viewer_button = None
            for selector in (
                "#new-download-btn",
                'button:has-text("Download")',
                'a:has-text("Download PDF")',
                'a[href*="/doi/pdfdirect/"]',
            ):
                try:
                    loc = page.locator(selector)
                    for i in range(min(loc.count(), 50)):
                        el = loc.nth(i)
                        try:
                            if el.is_visible():
                                viewer_button = el
                                break
                        except Exception:
                            pass
                    if viewer_button is not None:
                        break
                except Exception:
                    pass

            if viewer_button is not None:
                try:
                    href = viewer_button.get_attribute("href") or ""
                except Exception:
                    href = ""

                if href:
                    target = urljoin(page.url, href)
                    log_line(f"Wiley viewer Download href: {target}")
                    result = try_capture(
                        target,
                        "Wiley viewer Download href capture",
                        75,
                    )
                    if result is not None:
                        return result

                # Some Wiley viewers generate the pdfdirect link only after
                # the Download control is clicked.
                try:
                    viewer_button.click(force=True, timeout=15000)
                    page.wait_for_timeout(2500)
                except Exception as exc:
                    log_line(f"Wiley viewer Download click error: {exc}")

                try:
                    fr, pdf_link, href = find_wiley_pdfdirect(page, 30)
                except Exception:
                    fr, pdf_link, href = None, None, None

                if href:
                    target = urljoin(page.url, href)
                    log_line(f"Wiley generated pdfdirect link: {target}")
                    result = try_capture(
                        target,
                        "Wiley generated pdfdirect capture",
                        75,
                    )
                    if result is not None:
                        return result

        else:
            log_line(
                "Wiley visible PDF/ePDF control was not found on article page; "
                "using direct fallback."
            )

        if wiley_access_denied():
            raise NoAccess(
                "Wiley: institutional session has no access to this article"
            )

        # ------------------------------------------------------------------
        # FALLBACK: try known Wiley routes even if toolbar click was absent.
        # ------------------------------------------------------------------
        for url, label in (
            (epdf_url, "Wiley direct /doi/epdf fallback"),
            (pdf_url, "Wiley direct /doi/pdf fallback"),
            (pdfdirect_url, "Wiley direct /doi/pdfdirect fallback"),
        ):
            result = try_capture(url, label, 45)
            if result is not None:
                return result

        raise DownloadError(
            "WILEY_PDF_NOT_CAPTURED_AFTER_VISIBLE_PDF_CLICK_AND_FALLBACKS"
        )

    finally:
        try:
            close_page_safely(page)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# ELSEVIER / SCIENCEDIRECT
# ---------------------------------------------------------------------------

def find_elsevier_pdf_href(page, timeout_sec: int = 45) -> str:
    deadline = time.time() + timeout_sec

    while time.time() < deadline:
        if looks_like_captcha(page):
            wait_for_manual_verification(page, "Elsevier", 900)

        # Machine-readable metadata is more stable than the visible toolbar.
        for selector in (
            'meta[name="citation_pdf_url"]',
            'meta[property="citation_pdf_url"]',
        ):
            try:
                loc = page.locator(selector)
                if loc.count():
                    value = loc.first.get_attribute("content") or ""
                    if value:
                        return urljoin(page.url, value)
            except Exception:
                pass

        selectors = (
            'a:has-text("View PDF")',
            'a[href*="/pdfft"]',
            'a[href*="/pdf"]',
            'a[data-aa-name*="pdf" i]',
        )

        for selector in selectors:
            try:
                loc = page.locator(selector)
                for i in range(min(loc.count(), 80)):
                    el = loc.nth(i)
                    try:
                        href = el.get_attribute("href") or ""
                        text_value = ""
                        try:
                            text_value = (el.inner_text(timeout=300) or "").strip()
                        except Exception:
                            pass

                        if href and (
                            "view pdf" in text_value.lower()
                            or "pdfft" in href.lower()
                            or "/pdf" in href.lower()
                        ):
                            return urljoin(page.url, href)
                    except Exception:
                        pass
            except Exception:
                pass

        # ScienceDirect sometimes leaves the pdfft URL only in page HTML/JSON.
        try:
            html = page.content()
            patterns = (
                r'https?://[^"\'<>\\s]+/pdfft[^"\'<>\\s]*',
                r'(/science/article/pii/[^"\'<>\\s]+/pdfft[^"\'<>\\s]*)',
            )
            for pat in patterns:
                m = re.search(pat, html, flags=re.I)
                if m:
                    return urljoin(page.url, m.group(1))
        except Exception:
            pass

        page.wait_for_timeout(1000)

    raise DownloadError("ELSEVIER_PDF_CONTROL_NOT_FOUND")


def capture_pdf_with_fetch(context, target_url: str, dest: Path, timeout_sec: int = 120, source_label: str = "Elsevier") -> bool:
    page = context.new_page()
    cdp = context.new_cdp_session(page)

    saved = {"ok": False}
    normal_downloads = []

    page.on("download", lambda d: normal_downloads.append(d))

    cdp.send(
        "Fetch.enable",
        {
            "patterns": [
                {"urlPattern": "*", "requestStage": "Response"}
            ]
        }
    )

    def on_paused(params):
        request_id = params.get("requestId")
        request = params.get("request") or {}
        url = request.get("url", "")
        status = params.get("responseStatusCode")
        headers = params.get("responseHeaders") or []

        ctype = ""
        for h in headers:
            if str(h.get("name", "")).lower() == "content-type":
                ctype = str(h.get("value", "")).lower()
                break

        candidate = (
            status == 200 and (
                "application/pdf" in ctype
                or "application/octet-stream" in ctype
                or ".pdf" in url.lower()
                or "pdfft" in url.lower()
            )
        )

        if candidate and not saved["ok"]:
            try:
                body = cdp.send("Fetch.getResponseBody", {"requestId": request_id})
                raw = body.get("body", "")
                data = (
                    base64.b64decode(raw)
                    if body.get("base64Encoded")
                    else raw.encode("latin-1", errors="ignore")
                )

                if data[:5] == b"%PDF-":
                    dest.write_bytes(data)
                    saved["ok"] = True
                    log_line(f"{source_label} raw PDF captured: {len(data)} bytes")
                    # v5.17: once the PDF bytes are safely written, do not keep
                    # the intercepted browser response alive. On Chromium/Yandex
                    # Fetch.continueResponse may block for a long time after a
                    # direct PDF navigation. Abort only this already-captured
                    # response so page.goto can unwind immediately.
                    try:
                        cdp.send(
                            "Fetch.failRequest",
                            {"requestId": request_id, "errorReason": "Aborted"},
                        )
                    except Exception:
                        pass
                    return
            except Exception as exc:
                log_line(f"{source_label} Fetch body error: {exc}")

        try:
            cdp.send("Fetch.continueResponse", {"requestId": request_id})
        except Exception:
            try:
                cdp.send("Fetch.continueRequest", {"requestId": request_id})
            except Exception:
                pass

    cdp.on("Fetch.requestPaused", on_paused)

    log_line(f"{source_label} capture URL: {target_url}")

    try:
        safe_goto(page, target_url, min(20000, max(7000, timeout_sec * 1000)), attempts=1)
    except Exception as exc:
        # A direct PDF navigation may report "Download is starting".
        log_line(f"{source_label} navigation message: {exc}")

    deadline = time.time() + timeout_sec

    while time.time() < deadline and not saved["ok"] and not normal_downloads:
        if looks_like_captcha(page):
            # Keep the visible page open and wait for the user to complete
            # verification manually. Fetch remains attached and processing
            # resumes afterwards.
            wait_for_manual_verification(page, source_label, 900)
        page.wait_for_timeout(500)

    if not saved["ok"] and normal_downloads:
        try:
            normal_downloads[0].save_as(dest)
            saved["ok"] = valid_pdf(dest)
        except Exception:
            pass

    try:
        cdp.send("Fetch.disable")
    except Exception:
        pass

    try:
        close_page_safely(page)
    except Exception:
        pass

    if saved["ok"] and valid_pdf(dest):
        return True

    # v5.14: authenticated request fallback. BrowserContext.request shares the
    # browser cookie jar, so this remains ordinary authorized publisher access.
    try:
        log_line(f"{source_label}: browser capture missed; trying authenticated request fallback.")
        response = context.request.get(
            target_url,
            timeout=max(6000, min(timeout_sec * 600, 10000)),
            fail_on_status_code=False,
            headers={
                "Accept": "application/pdf,application/octet-stream;q=0.9,*/*;q=0.5",
                "User-Agent": "Mozilla/5.0",
            },
        )
        data = response.body()
        ctype = str(response.headers.get("content-type", "")).lower()
        if data[:5] == b"%PDF-":
            dest.write_bytes(data)
            log_line(
                f"{source_label} authenticated request captured PDF: "
                f"{len(data)} bytes ({ctype or 'content-type unknown'})"
            )
            return valid_pdf(dest)
        log_line(
            f"{source_label} authenticated request was not PDF: "
            f"status={response.status} content-type={ctype} bytes={len(data)}"
        )
    except Exception as exc:
        log_line(
            f"{source_label} authenticated request fallback failed: "
            f"{type(exc).__name__}: {exc}"
        )

    return False


def download_elsevier(context, raw: str, doi: str | None, resolved_url: str | None) -> Path:
    if raw.startswith("http") and "sciencedirect.com" in raw.lower():
        article_url = raw
    elif resolved_url and "sciencedirect.com" in resolved_url.lower():
        article_url = resolved_url
    elif doi:
        article_url = resolve_doi(context, doi)
    else:
        article_url = raw

    page = context.new_page()
    try:
        log_line(f"Elsevier article open: {article_url}")
        safe_goto(page, article_url, 25000, attempts=2)
        wait_page_ready(page, "Elsevier")
        page.wait_for_timeout(3500)

        if looks_like_captcha(page):
            wait_for_manual_verification(page, "Elsevier", 900)

        identity = doi or urlparse(article_url).path.strip("/").split("/")[-1] or "elsevier"

        candidates = []
        try:
            candidates.append(find_elsevier_pdf_href(page, 35))
        except DownloadError as exc:
            log_line(f"Elsevier visible PDF control not found: {exc}")

        # Stable ScienceDirect PII fallback.
        m = re.search(r"/science/article/pii/([^/?#]+)", page.url or article_url, flags=re.I)
        if m:
            pii = m.group(1)
            candidates.extend(
                [
                    f"https://www.sciencedirect.com/science/article/pii/{pii}/pdfft?isDTMRedir=true&download=true",
                    f"https://www.sciencedirect.com/science/article/pii/{pii}/pdfft?download=true",
                ]
            )

        # Metadata/DOM fallback as a final source of URLs.
        for score, url, why in discover_pdf_candidates(
            page, page.url or article_url, "generic", doi
        ):
            if "pdf" in url.lower() or "pdfft" in url.lower():
                candidates.append(url)

        seen = set()
        for pdf_href in candidates:
            if not pdf_href:
                continue
            key = pdf_href.lower()
            if key in seen:
                continue
            seen.add(key)
            log_line(f"Elsevier PDF attempt: {pdf_href}")
            dest = next_destination("Elsevier", identity)
            if capture_pdf_with_fetch(context, pdf_href, dest, 20, "Elsevier PDF"):
                return dest
            try:
                dest.unlink()
            except Exception:
                pass

        dest = next_destination("Elsevier", identity)
        if try_click_pdf_control(context, page, dest, "Elsevier"):
            return dest
        try:
            dest.unlink()
        except Exception:
            pass

        denied = access_denied_hint(page, "Elsevier")
        if denied:
            raise NoAccess(denied)

        if not candidates:
            raise DownloadError("ELSEVIER_PDF_CONTROL_NOT_FOUND")
        raise DownloadError("ELSEVIER_PDF_NOT_CAPTURED")

    finally:
        try:
            close_page_safely(page)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# MDPI / ACS / NATURE / FRONTIERS / OXFORD ACADEMIC
# ---------------------------------------------------------------------------

def page_text_lower(page, timeout: int = 5000) -> str:
    try:
        return page.locator("body").inner_text(timeout=timeout).lower()
    except Exception:
        return ""


def access_denied_hint(page, source_label: str) -> str | None:
    body = page_text_lower(page)
    phrases = (
        "this pdf is available to subscribers only",
        "available to purchase",
        "purchase access",
        "buy this article",
        "rent or buy",
        "you do not have access",
        "purchase this article",
        "access options",
        "get access",
        "institutional access",
        "sign in to access",
        "subscribe to access",
        "purchase pdf",
        "purchase instant access",
        "article can be downloaded after successful payment",
        "download the full article in pdf format eur",
        "full paper access choose",
        "доступ к данной странице для незарегистрированных пользователей",
        "укажите имя пользователя и пароль",
        "доступ к полному тексту закрыт",
        "полный текст отсутствует",
        "текст документа недоступен",
        # Russian publishers that sell single articles, and library catalogue
        # records of print-only journals.
        "купить статью",
        "в корзину",
        "заказать копию статьи",
        "текст : непосредственный",
    )
    for phrase in phrases:
        if phrase in body:
            return f"{source_label}: {phrase}"
    return None


# Supporting-information files are PDFs too. v5.23 only lowered their score,
# so when the article PDF was closed the SI file was saved as the article
# (ACS article-supplement/..._si_001, Springer ESM, Elsevier mmc1.pdf).
SUPPLEMENT_URL_RE = re.compile(
    r"supplement|supporting|suppl[_/.-]|[_/-]si[_-]?\d{2,3}\b|/esm/|"
    r"mediaobjects/.*esm|[-_/]mmc\d+\.",
    re.I,
)


def is_supplement_url(url: str) -> bool:
    return bool(SUPPLEMENT_URL_RE.search(unquote(url or "")))


def discover_pdf_candidates(page, article_url: str, source: str, doi: str | None):
    candidates = []
    seen = set()

    def add(url, score=0, why=""):
        if not url:
            return
        absolute = urljoin(page.url or article_url, str(url).strip())
        key = absolute.lower()
        if (
            not absolute
            or key in seen
            or key == "about:blank"
            or key.startswith("about:")
            or key.startswith("javascript:")
            or key.startswith("data:")
        ):
            return

        if is_supplement_url(absolute):
            return

        if any(
            bad in key
            for bad in (
                "figure",
                "figshare",
                "slide",
                "graphical",
                "cover",
            )
        ):
            score -= 80

        seen.add(key)
        candidates.append((score, absolute, why))

    clean_article = article_url.split("#")[0]

    if source == "mdpi":
        base = clean_article.split("?")[0].rstrip("/")
        add(base + "/pdf", 160, "MDPI /pdf")
        add(base + "/pdf-vor", 155, "MDPI /pdf-vor")

    elif source == "nature":
        base = clean_article.split("?")[0].rstrip("/")
        if "/articles/" in base and not base.lower().endswith(".pdf"):
            add(base + ".pdf", 165, "Nature article .pdf")

    elif source == "frontiers":
        base = clean_article.split("?")[0].rstrip("/")
        if not base.lower().endswith("/pdf"):
            add(base + "/pdf", 165, "Frontiers /pdf")

    elif source == "acs" and doi:
        add(f"https://pubs.acs.org/doi/pdf/{doi}", 165, "ACS /doi/pdf")
        add(f"https://pubs.acs.org/doi/epdf/{doi}", 160, "ACS /doi/epdf")

    elif source == "taylor" and doi:
        # Taylor & Francis exposes article PDFs through normal /doi/pdf and
        # /doi/epdf routes when the current browser session has access.
        add(
            f"https://www.tandfonline.com/doi/pdf/{doi}?download=true",
            170,
            "Taylor & Francis /doi/pdf",
        )
        add(
            f"https://www.tandfonline.com/doi/epdf/{doi}?needAccess=true&role=button",
            165,
            "Taylor & Francis /doi/epdf",
        )

    elif source == "generic":
        host = (urlparse(article_url).netloc or "").lower()
        base = clean_article.split("?")[0].rstrip("/")

        # Common publisher patterns. These are ordinary publisher PDF routes;
        # access is still determined by the current institutional browser
        # session. No paywall or verification bypass is attempted.
        if doi and "journals.sagepub.com" in host:
            add(
                f"https://journals.sagepub.com/doi/pdf/{doi}?download=true",
                180,
                "SAGE /doi/pdf",
            )

        if "scientific.net" in host and not base.lower().endswith(".pdf"):
            add(base + ".pdf", 170, "Scientific.Net article .pdf")

        # SCIndeks (Serbian journals): the article page often does not load
        # (25.10), but the PDF sits at a fixed address built from the artid:
        # 1821-44872002057M -> /data/pdf/1821-4487/2020/1821-44872002057M.pdf
        if "scindeks.ceon.rs" in host:
            m = re.search(r"artid=((\d{4}-\d{3}[\dXx])(\d{2})\w+)", article_url, re.I)
            if m:
                yy = int(m.group(3))
                year = 2000 + yy if yy <= 50 else 1900 + yy
                add(
                    f"https://scindeks-clanci.ceon.rs/data/pdf/{m.group(2)}/{year}/{m.group(1)}.pdf",
                    185,
                    "SCIndeks PDF address",
                )

    # Standard citation metadata.
    for selector in (
        'meta[name="citation_pdf_url"]',
        'meta[name="pdf_url"]',
        'meta[property="citation_pdf_url"]',
        'meta[property="og:pdf"]',
    ):
        try:
            loc = page.locator(selector)
            for i in range(min(loc.count(), 10)):
                add(loc.nth(i).get_attribute("content"), 150, selector)
        except Exception:
            pass

    # Additional machine-readable PDF links used by OJS and smaller
    # publisher platforms.
    for selector, attr, score in (
        ('link[type="application/pdf"]', "href", 165),
        ('link[rel="alternate"][type="application/pdf"]', "href", 165),
        ('iframe[src*=".pdf" i]', "src", 155),
        ('embed[type="application/pdf"]', "src", 155),
        ('object[type="application/pdf"]', "data", 155),
    ):
        try:
            loc = page.locator(selector)
            for i in range(min(loc.count(), 20)):
                add(
                    loc.nth(i).get_attribute(attr),
                    score,
                    f"{selector}[{attr}]",
                )
        except Exception:
            pass

    try:
        anchors = page.locator("a")
        for i in range(min(anchors.count(), 1200)):
            a = anchors.nth(i)
            try:
                href = a.get_attribute("href") or ""
                if not href:
                    continue

                try:
                    text_value = (a.inner_text(timeout=300) or "").strip()
                except Exception:
                    text_value = ""

                low_href = href.lower()
                low_text = text_value.lower()
                score = 0

                if "download pdf" in low_text:
                    score += 135
                elif "full text pdf" in low_text or "full-text pdf" in low_text:
                    score += 130
                elif "article pdf" in low_text:
                    score += 125
                elif low_text == "pdf" or low_text.startswith("pdf "):
                    score += 120
                elif "view pdf" in low_text:
                    score += 120
                elif "download article" in low_text or "download full text" in low_text:
                    score += 110
                elif "полный текст" in low_text or "текст статьи" in low_text:
                    score += 125
                elif "скачать" in low_text or "загрузить" in low_text:
                    score += 115

                if ".pdf" in low_href:
                    score += 90
                if "/pdf" in low_href:
                    score += 70
                if "article-pdf" in low_href:
                    score += 95
                if "pdfdirect" in low_href:
                    score += 95
                if "download" in low_href and "pdf" in low_text:
                    score += 40
                if "/download/" in low_href and source == "elibrary":
                    score += 120

                if score >= 70:
                    add(href, score, f"anchor:{text_value[:60]}")
            except Exception:
                pass
    except Exception:
        pass

    # Some publisher themes put the target URL in data-* attributes
    # rather than a normal anchor href.
    for selector in ("button", '[role="button"]'):
        try:
            controls = page.locator(selector)
            for i in range(min(controls.count(), 400)):
                el = controls.nth(i)
                try:
                    text_value = (el.inner_text(timeout=250) or "").strip()
                except Exception:
                    text_value = ""

                low_text = text_value.lower()
                score = 0
                if "download pdf" in low_text:
                    score = 140
                elif "full text pdf" in low_text or "full-text pdf" in low_text:
                    score = 135
                elif low_text == "pdf" or "view pdf" in low_text:
                    score = 125

                if score:
                    for attr in (
                        "data-url",
                        "data-href",
                        "data-download-url",
                        "data-pdf-url",
                    ):
                        try:
                            value = el.get_attribute(attr)
                        except Exception:
                            value = None
                        if value:
                            add(value, score, f"{selector}:{attr}:{text_value[:50]}")
        except Exception:
            pass

    if doi:
        doi_low = doi.lower()
        doi_compact = re.sub(r"[^a-z0-9]+", "", doi_low)
        boosted = []
        for score, url, why in candidates:
            low = unquote(url).lower()
            compact = re.sub(r"[^a-z0-9]+", "", low)
            if doi_low in low or (doi_compact and doi_compact in compact):
                score += 25
            boosted.append((score, url, why))
        candidates = boosted

    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[:6]


def try_click_pdf_control(context, page, dest: Path, label: str) -> bool:
    """Try visible PDF controls that have no useful href (JS-driven buttons)."""
    selectors = (
        'a:has-text("Download PDF")',
        'button:has-text("Download PDF")',
        '[role="button"]:has-text("Download PDF")',
        'a:has-text("View PDF")',
        'button:has-text("View PDF")',
        'a:has-text("Full Text PDF")',
        'a:has-text("PDF")',
        'button:has-text("PDF")',
    )

    checked = 0
    for fr in page.frames:
        for selector in selectors:
            try:
                loc = fr.locator(selector)
                for i in range(min(loc.count(), 12)):
                    if checked >= 8:
                        return False
                    el = loc.nth(i)
                    try:
                        if not el.is_visible():
                            continue
                        text_value = (el.inner_text(timeout=300) or "").strip()
                        href = el.get_attribute("href") or ""
                        if href and is_supplement_url(urljoin(page.url, href)):
                            continue
                        if href:
                            target = urljoin(page.url, href)
                            low = target.lower()
                            if "pdf" in low or "download" in low:
                                log_line(
                                    f"{label} click-fallback href: {text_value[:60]} -> {target}"
                                )
                                if capture_pdf_with_fetch(
                                    context, target, dest, 45,
                                    source_label=f"{label} clicked PDF",
                                ):
                                    return True

                        checked += 1
                        before_ids = {id(p) for p in context.pages}
                        start_url = page.url
                        log_line(
                            f"{label} JS PDF control click attempt {checked}: {text_value[:80]}"
                        )

                        try:
                            with page.expect_download(timeout=5000) as info:
                                el.click(force=True, timeout=8000)
                            dl = info.value
                            dl.save_as(dest)
                            if valid_pdf(dest):
                                log_line(f"{label} PDF saved from browser download event.")
                                return True
                        except PWTimeout:
                            pass
                        except Exception as exc:
                            log_line(
                                f"{label} PDF control click message: "
                                f"{type(exc).__name__}: {exc}"
                            )

                        try:
                            page.wait_for_timeout(1200)
                        except Exception:
                            pass

                        for pg in list(context.pages):
                            if id(pg) in before_ids:
                                continue
                            try:
                                target = pg.url
                                if target and target != "about:blank":
                                    log_line(f"{label} PDF click opened page: {target}")
                                    if capture_pdf_with_fetch(
                                        context, target, dest, 35,
                                        source_label=f"{label} clicked PDF",
                                    ):
                                        return True
                            except Exception:
                                pass

                        try:
                            current = page.url
                            if current and current != start_url:
                                low = current.lower()
                                if "pdf" in low or "download" in low:
                                    if capture_pdf_with_fetch(
                                        context, current, dest, 35,
                                        source_label=f"{label} navigated PDF",
                                    ):
                                        return True
                        except Exception:
                            pass

                    except Exception:
                        pass
            except Exception:
                pass

    return False


def save_pdf_direct_http(context, url: str, dest: Path) -> bool:
    """v5.19: fetch a resolved direct-PDF URL without opening a PDF viewer.

    First try a normal HTTP request for public PDFs, then a request made with
    the browser context's authorized cookie jar. Only %PDF files count.
    """
    parsed = urlparse(url)
    if parsed.scheme.lower() not in ("http", "https"):
        return False

    def accept_bytes(data: bytes, route: str) -> bool:
        if not data.startswith(b"%PDF-") or len(data) <= 1000:
            return False
        try:
            dest.write_bytes(data)
            if valid_pdf(dest):
                log_line(f"DIRECT_PDF_SAVED via {route}: {dest} ({len(data)} bytes)")
                return True
        except Exception as exc:
            log_line(f"Direct PDF write error: {exc}")
        try:
            dest.unlink(missing_ok=True)
        except Exception:
            pass
        return False

    def fetch(target: str, timeout: int = 12) -> bytes:
        req = urllib.request.Request(
            target, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130.0 Safari/537.36",
                "Accept": "application/pdf,*/*;q=0.8",
            }
        )
        with urllib.request.urlopen(req, timeout=timeout) as reply:
            return reply.read(80 * 1024 * 1024 + 1)

    # Set when the host cannot be reached at all (reset, TLS EOF, DNS), as
    # opposed to answering with a refusal.
    unreachable = False
    try:
        data = fetch(url)
        if accept_bytes(data, "urllib"):
            return True
        log_line(f"Direct PDF urllib returned non-PDF ({len(data)} bytes)")
    except urllib.error.HTTPError as exc:
        log_line(f"Direct PDF urllib failed: {type(exc).__name__}: {exc}")
    except Exception as exc:
        unreachable = True
        log_line(f"Direct PDF urllib failed: {type(exc).__name__}: {exc}")

    try:
        response = context.request.get(
            url,
            timeout=12000,
            fail_on_status_code=False,
            headers={"Accept": "application/pdf,*/*;q=0.8"},
        )
        data = response.body()
        if accept_bytes(data, "authenticated context.request"):
            return True
        unreachable = False
        log_line(
            "Direct PDF authenticated request did not return PDF: "
            f"status={response.status}; bytes={len(data)}; "
            f"content-type={response.headers.get('content-type', '')}"
        )
    except Exception as exc:
        log_line(f"Direct PDF authenticated request failed: {type(exc).__name__}: {exc}")

    # 23.10: several open journal sites (rombio.unibuc.ro, gup.ugal.ro) reset
    # every connection from both PCs, while the Internet Archive holds their
    # public PDFs. Only for hosts that are unreachable, never for refusals.
    if unreachable:
        try:
            data = fetch("https://web.archive.org/web/2id_/" + url, timeout=40)
            if accept_bytes(data, "Wayback Machine copy"):
                return True
            log_line(f"Wayback Machine has no PDF copy ({len(data)} bytes)")
        except Exception as exc:
            log_line(f"Wayback Machine copy failed: {type(exc).__name__}: {exc}")

    return False


def capture_direct_pdf_watchdog(target_url: str, dest: Path, timeout_sec: int = 30) -> bool:
    """Isolated PDF capture: successful bytes win even if Yandex CDP cleanup hangs.

    Yandex sometimes freezes after Fetch.getResponseBody writes a PDF, while
    Fetch.failRequest, page.goto or Fetch.disable is still waiting. Do NOT
    try to interrupt Playwright synchronously in the same process. Instead,
    use an isolated child attached to the same dedicated Yandex profile.
    The parent watches for the worker's explicit post-write log confirmation,
    verifies the file and ends the child immediately. Hard timeout ensures
    an unusable CDP connection cannot wedge the downloader indefinitely.
    """
    import queue as _queue
    import threading as _threading

    cmd = [sys.executable, "-u", str(Path(__file__).resolve()),
           "--internal-pdf-worker", target_url, str(dest)]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    proc = None
    messages = _queue.Queue()
    got_write_confirmation = False
    deadline = time.monotonic() + timeout_sec

    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            creationflags=flags,
        )

        def read_worker_lines():
            try:
                for line in proc.stdout:
                    messages.put(line.rstrip("\r\n"))
            except Exception:
                pass

        _threading.Thread(target=read_worker_lines, daemon=True).start()
        log_line(f"DIRECT_PDF_WORKER_STARTED pid={proc.pid} timeout={timeout_sec}s")

        while time.monotonic() < deadline:
            while True:
                try:
                    line = messages.get_nowait()
                except _queue.Empty:
                    break
                if "raw PDF captured:" in line or "authenticated request captured PDF:" in line:
                    got_write_confirmation = True
                    log_line(f"DIRECT_PDF_WORKER_CONFIRMED: {line}")
                elif "PDF_WORKER_SUCCESS" in line:
                    got_write_confirmation = True
                elif "PDF_WORKER_FAILED" in line:
                    log_line(line)
            if got_write_confirmation and valid_pdf(dest):
                log_line(f"DIRECT_PDF_WORKER_SAVED: {dest} ({dest.stat().st_size} bytes)")
                return True
            if proc.poll() is not None:
                # Drain any final buffered post-write marker.
                time.sleep(0.1)
                while not messages.empty():
                    line = messages.get_nowait()
                    if any(marker in line for marker in
                           ("raw PDF captured:", "authenticated request captured PDF:", "PDF_WORKER_SUCCESS")):
                        got_write_confirmation = True
                    elif "PDF_WORKER_FAILED" in line:
                        log_line(line)
                if valid_pdf(dest) and (got_write_confirmation or proc.returncode == 0):
                    log_line(f"DIRECT_PDF_WORKER_SAVED: {dest} ({dest.stat().st_size} bytes)")
                    return True
                log_line(f"DIRECT_PDF_WORKER_EXIT={proc.returncode}; PDF not confirmed")
                return False
            time.sleep(0.2)

        log_line(f"DIRECT_PDF_WORKER_TIMEOUT ({timeout_sec}s); no confirmed PDF")
        return False
    finally:
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        if proc is not None and proc.stdout is not None:
            try:
                proc.stdout.close()
            except Exception:
                pass


def internal_pdf_worker_main(target_url: str, dest: Path) -> int:
    """One disposable CDP session. Parent terminates it on post-write marker."""
    try:
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(BROWSER_CDP_URL, timeout=12000)
            if not browser.contexts:
                raise DownloadError("Yandex context missing in direct PDF worker")
            context = browser.contexts[0]
            if capture_pdf_with_fetch(context, target_url, dest, 12,
                                      source_label="Direct PDF worker"):
                safe_console_print("PDF_WORKER_SUCCESS")
                return 0
            safe_console_print("PDF_WORKER_FAILED: no valid PDF")
            return 1
    except Exception as exc:
        safe_console_print(f"PDF_WORKER_FAILED: {type(exc).__name__}: {exc}")
        return 1


def download_browser_pdf_platform(
    context,
    raw: str,
    doi: str | None,
    resolved_url: str | None,
    source: str,
    label: str,
    prefix: str,
) -> Path:
    if resolved_url and str(resolved_url).startswith("http"):
        article_url = resolved_url
    elif raw.startswith("http") and "doi.org/" not in raw.lower():
        article_url = raw
    elif doi:
        article_url = resolve_doi(context, doi)
    else:
        article_url = raw

    # v5.18: DOI resolvers sometimes return the PDF itself. Do not open that
    # URL as an article page and then inspect Chromium's PDF viewer (which only
    # exposes about:blank). Capture the resolved PDF directly and return.
    parsed_direct = urlparse(article_url)
    direct_path = unquote(parsed_direct.path or "").lower()
    if direct_path.endswith(".pdf"):
        identity = (
            doi
            or parsed_direct.path.strip("/").split("/")[-1]
            or source
        )
        dest = next_destination(prefix, identity)
        log_line(f"{label} resolved directly to PDF; capturing without viewer: {article_url}")
        if save_pdf_direct_http(context, article_url, dest):
            return dest
        log_line(f"{label}: direct HTTP methods missed; last authorized browser capture attempt")
        if capture_direct_pdf_watchdog(article_url, dest, timeout_sec=30):
            return dest
        try:
            dest.unlink()
        except Exception:
            pass
        # A URL whose path is already .pdf should NOT be treated as an article
        # webpage: Chromium's built-in viewer may present only about:blank.
        raise DownloadError("DIRECT_PDF_NOT_RETRIEVED: direct HTTP + authorized browser capture failed")

    page = context.new_page()

    try:
        log_line(f"{label} article open: {article_url}")
        safe_goto(page, article_url, 25000, attempts=2)
        wait_page_ready(page, label)
        page.wait_for_timeout(1800)

        wait_for_manual_verification(page, label, 900)

        def find_candidates():
            found = discover_pdf_candidates(
                page,
                page.url or article_url,
                source,
                doi,
            )
            if source == "generic":
                # A bare .pdf link in a bibliography scores around 90. Do not
                # mistake cited/reference PDFs for the article itself. Generic
                # fallback requires a stronger signal: PDF-labelled control,
                # citation metadata, embedded PDF, DOI-bearing link, or a known
                # publisher PDF pattern.
                found = [
                    item for item in found
                    if item[0] >= 110
                ][:8]
            return found

        candidates = find_candidates()

        # v5.23: after a Cloudflare check the publisher reloads the article.
        # In the 23.10 log (RSC, AIP, Oxford, ASME) the search ran 3-9 s after
        # the check, on a page that was not there yet, and the tab was closed.
        # Look again for up to 20 s unless the page already shows a paywall.
        retry_until = time.time() + job_bounded_wait(20)
        while not candidates and time.time() < retry_until:
            if access_denied_hint(page, label):
                break
            page.wait_for_timeout(2500)
            wait_for_manual_verification(page, label, 900)
            wait_page_ready(page, label, 10)
            candidates = find_candidates()

        if page.url:
            article_url = page.url

        for score, url, why in candidates:
            log_line(
                f"{label} PDF candidate score={score} "
                f"reason={why}: {url}"
            )

        identity = (
            doi
            or urlparse(article_url).path.strip("/").split("/")[-1]
            or source
        )

        for attempt, (score, pdf_url, why) in enumerate(candidates, 1):
            dest = next_destination(prefix, identity)

            log_line(
                f"{label} PDF attempt {attempt}/{len(candidates)}: "
                f"{pdf_url}"
            )

            if capture_pdf_with_fetch(
                context,
                pdf_url,
                dest,
                15,
                source_label=f"{label} PDF",
            ):
                return dest

            try:
                dest.unlink()
            except Exception:
                pass

        # v5.14: if URLs were absent or did not capture, try the real visible
        # PDF control. This handles JS-driven publisher buttons.
        click_dest = next_destination(prefix, identity)
        if try_click_pdf_control(context, page, click_dest, label):
            return click_dest
        try:
            click_dest.unlink()
        except Exception:
            pass

        denied = access_denied_hint(page, label)
        if denied:
            raise NoAccess(denied)

        if not candidates:
            raise DownloadError(
                f"{label.upper().replace(' ', '_')}_PDF_LINK_NOT_FOUND"
            )

        raise DownloadError(
            f"{label.upper().replace(' ', '_')}_PDF_NOT_CAPTURED"
        )

    finally:
        try:
            close_page_safely(page)
        except Exception:
            pass


def download_mdpi(context, raw: str, doi: str | None, resolved_url: str | None) -> Path:
    return download_browser_pdf_platform(
        context, raw, doi, resolved_url,
        "mdpi", "MDPI", "MDPI"
    )


def download_acs(context, raw: str, doi: str | None, resolved_url: str | None) -> Path:
    return download_browser_pdf_platform(
        context, raw, doi, resolved_url,
        "acs", "ACS", "ACS"
    )


def download_nature(context, raw: str, doi: str | None, resolved_url: str | None) -> Path:
    return download_browser_pdf_platform(
        context, raw, doi, resolved_url,
        "nature", "Nature", "Nature"
    )


def download_frontiers(context, raw: str, doi: str | None, resolved_url: str | None) -> Path:
    return download_browser_pdf_platform(
        context, raw, doi, resolved_url,
        "frontiers", "Frontiers", "Frontiers"
    )


def download_oxford(context, raw: str, doi: str | None, resolved_url: str | None) -> Path:
    return download_browser_pdf_platform(
        context, raw, doi, resolved_url,
        "oxford", "Oxford Academic", "Oxford"
    )


def download_generic(context, raw: str, doi: str | None, resolved_url: str | None) -> Path:
    return download_browser_pdf_platform(
        context, raw, doi, resolved_url,
        "generic", "Generic DOI publisher", "Generic"
    )


def download_taylor(context, raw: str, doi: str | None, resolved_url: str | None) -> Path:
    return download_browser_pdf_platform(
        context, raw, doi, resolved_url,
        "taylor", "Taylor & Francis", "TaylorFrancis"
    )



# ---------------------------------------------------------------------------
# eLIBRARY.RU
# ---------------------------------------------------------------------------

def elibrary_identity(raw_url: str) -> str:
    m = re.search(r"[?&]id=(\d+)", raw_url or "", flags=re.I)
    if m:
        return m.group(1)
    digest = hashlib.sha1((raw_url or "").encode("utf-8", errors="ignore")).hexdigest()[:16]
    return "direct_" + digest


def elibrary_page_text(page) -> str:
    try:
        return page.locator("body").inner_text(timeout=5000).lower()
    except Exception:
        return ""


def elibrary_is_ip_blocked(page) -> bool:
    body = elibrary_page_text(page)
    return (
        "доступ к сайту elibrary.ru" in body
        and "ip-адрес" in body
        and "заблокирован" in body
    )


def elibrary_needs_login(page) -> bool:
    body = elibrary_page_text(page)
    phrases = (
        "доступ к данной странице для незарегистрированных пользователей",
        "укажите имя пользователя и пароль",
        "вход в библиотеку",
    )
    return any(p in body for p in phrases)


def elibrary_wait_for_login(page, timeout_sec: int = 600):
    """Wait for the user to log in manually in the real Edge window."""
    if elibrary_is_ip_blocked(page):
        raise NoAccess("ELIBRARY_IP_BLOCKED")

    if not elibrary_needs_login(page):
        return

    try:
        page.bring_to_front()
    except Exception:
        pass

    log_line(
        "eLIBRARY: login/registered-user access required. "
        "Waiting for manual login in Edge."
    )
    safe_console_print("=" * 70)
    safe_console_print("eLIBRARY: ВОЙДИТЕ В АККАУНТ В ОКНЕ БРАУЗЕРА ЗАГРУЗЧИКА. Скрипт ждёт.")
    safe_console_print("=" * 70)

    deadline = time.time() + job_bounded_wait(timeout_sec)
    while time.time() < deadline:
        if elibrary_is_ip_blocked(page):
            raise NoAccess("ELIBRARY_IP_BLOCKED")
        if not elibrary_needs_login(page):
            page.wait_for_timeout(2500)
            return
        page.wait_for_timeout(2000)

    raise NoAccess("ELIBRARY_LOGIN_REQUIRED")


def _norm_title(value: str) -> str:
    value = (value or "").lower().replace("ё", "е")
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"[^0-9a-zа-я]+", " ", value, flags=re.I)
    return re.sub(r"\s+", " ", value).strip()


def _title_similarity(a: str, b: str) -> float:
    a1, b1 = _norm_title(a), _norm_title(b)
    if not a1 or not b1:
        return 0.0
    seq = difflib.SequenceMatcher(None, a1, b1).ratio()
    sa, sb = set(a1.split()), set(b1.split())
    jac = len(sa & sb) / max(1, len(sa | sb))
    return max(seq, jac)


def _http_json(url: str, timeout: int = 25):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "ArticleDownloader/5.12 (+academic metadata lookup)",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        # OpenAlex and Crossref answer 429 to bursts; one pause is enough.
        # A spent daily budget (OpenAlex without a key: Retry-After of hours)
        # is not worth waiting for.
        if exc.code != 429 or int(exc.headers.get("Retry-After") or 0) > 60:
            raise
        time.sleep(5)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))


def _year_from_crossref(item) -> int | None:
    for key in ("published-print", "published-online", "published", "issued", "created"):
        try:
            parts = item.get(key, {}).get("date-parts", [])
            if parts and parts[0] and parts[0][0]:
                return int(parts[0][0])
        except Exception:
            pass
    return None


def _elibrary_local_metadata(identity: str) -> dict:
    path = ROOT / "R1_elibrary_425_metadata.csv"
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                if str(row.get("elibrary_id", "")).strip() == str(identity):
                    return row
    except Exception as exc:
        log_line(f"eLIBRARY local metadata read failed: {exc}")
    return {}


def _page_meta_content(page, selectors) -> str:
    for selector in selectors:
        try:
            loc = page.locator(selector)
            if loc.count() > 0:
                value = loc.first.get_attribute("content") or ""
                if value.strip():
                    return value.strip()
        except Exception:
            pass
    return ""


def _elibrary_extract_metadata(page, identity: str) -> dict:
    local = _elibrary_local_metadata(identity)

    title = _page_meta_content(
        page,
        (
            'meta[name="citation_title"]',
            'meta[name="dc.title"]',
            'meta[property="og:title"]',
        ),
    )
    if not title:
        for selector in ("h1", ".title", "#title"):
            try:
                loc = page.locator(selector)
                if loc.count() and loc.first.is_visible():
                    title = (loc.first.inner_text(timeout=1000) or "").strip()
                    if title:
                        break
            except Exception:
                pass
    if not title:
        title = str(local.get("title") or "").strip()

    journal = _page_meta_content(
        page,
        (
            'meta[name="citation_journal_title"]',
            'meta[name="prism.publicationName"]',
        ),
    ) or str(local.get("journal") or "").strip()

    year = None
    date_value = _page_meta_content(
        page,
        (
            'meta[name="citation_publication_date"]',
            'meta[name="citation_date"]',
            'meta[name="dc.date"]',
        ),
    )
    m = re.search(r"\b(19|20)\d{2}\b", date_value or "")
    if m:
        year = int(m.group(0))
    if year is None:
        try:
            y = str(local.get("year") or "").strip()
            if re.fullmatch(r"\d{4}", y):
                year = int(y)
        except Exception:
            pass

    doi = _page_meta_content(
        page,
        (
            'meta[name="citation_doi"]',
            'meta[name="dc.identifier"]',
        ),
    )
    doi = extract_doi(doi) or None

    if not doi:
        try:
            body = page.locator("body").inner_text(timeout=3000)
        except Exception:
            body = ""
        # Do not take the first DOI from the whole page: references may contain
        # unrelated DOIs. Accept only a DOI explicitly labelled as the record DOI.
        m = re.search(
            r"(?i)\bDOI\s*[:：]?\s*(10\.\d{4,9}/[^\s<>'\"]+)",
            body,
        )
        if m:
            doi = extract_doi(m.group(1))

    return {
        "title": title,
        "year": year,
        "journal": journal,
        "doi": doi,
    }


def _add_open_candidate(items: list, seen: set, url: str | None, score: int, why: str):
    if not url:
        return
    url = str(url).strip()
    if not url.startswith(("http://", "https://")):
        return
    low = url.lower()
    if "elibrary.ru" in low:
        return
    key = re.sub(r"#.*$", "", url)
    if key in seen:
        return
    seen.add(key)
    items.append((score, url, why))


def _crossref_open_candidates(title: str, year: int | None, journal: str) -> list:
    result, seen = [], set()
    if not title:
        return result
    try:
        q = quote_plus(title)
        url = f"https://api.crossref.org/v1/works?query.title={q}&rows=6"
        data = _http_json(url)
        items = data.get("message", {}).get("items", [])
        for item in items:
            titles = item.get("title") or []
            cand_title = titles[0] if titles else ""
            sim = _title_similarity(title, cand_title)
            y = _year_from_crossref(item)
            year_ok = (year is None or y is None or abs(int(year) - int(y)) <= 2)
            if sim < 0.87 or not year_ok:
                continue

            doi = str(item.get("DOI") or "").strip()
            if doi:
                _add_open_candidate(result, seen, "https://doi.org/" + doi, 210, f"Crossref DOI sim={sim:.2f}")

            for link in item.get("link") or []:
                u = link.get("URL")
                ctype = str(link.get("content-type") or "").lower()
                score = 245 if "pdf" in ctype or (u and ".pdf" in u.lower()) else 190
                _add_open_candidate(result, seen, u, score, f"Crossref link {ctype} sim={sim:.2f}")

            _add_open_candidate(result, seen, item.get("URL"), 185, f"Crossref landing sim={sim:.2f}")
    except Exception as exc:
        log_line(f"eLIBRARY Crossref lookup failed: {exc}")
    return result


def _openalex_open_candidates(title: str, year: int | None) -> list:
    result, seen = [], set()
    if not title:
        return result
    try:
        q = quote_plus(title)
        data = _http_json(f"https://api.openalex.org/works?search={q}&per-page=6" + _openalex_key_param())
        for item in data.get("results", []) or []:
            cand_title = item.get("title") or item.get("display_name") or ""
            sim = _title_similarity(title, cand_title)
            y = item.get("publication_year")
            year_ok = (year is None or y is None or abs(int(year) - int(y)) <= 2)
            if sim < 0.87 or not year_ok:
                continue

            locations = []
            for key in ("best_oa_location", "primary_location"):
                loc = item.get(key)
                if isinstance(loc, dict):
                    locations.append(loc)
            for loc in item.get("locations") or []:
                if isinstance(loc, dict):
                    locations.append(loc)

            for loc in locations:
                pdf = loc.get("pdf_url")
                landing = loc.get("landing_page_url")
                if pdf:
                    _add_open_candidate(result, seen, pdf, 260, f"OpenAlex OA PDF sim={sim:.2f}")
                if landing:
                    _add_open_candidate(result, seen, landing, 205, f"OpenAlex landing sim={sim:.2f}")

            doi = str(item.get("doi") or "").strip()
            if doi:
                if doi.lower().startswith("https://doi.org/"):
                    _add_open_candidate(result, seen, doi, 215, f"OpenAlex DOI sim={sim:.2f}")
                else:
                    _add_open_candidate(result, seen, "https://doi.org/" + doi, 215, f"OpenAlex DOI sim={sim:.2f}")
    except Exception as exc:
        log_line(f"eLIBRARY OpenAlex lookup failed: {exc}")
    return result


def _word_cover(words: list, other: list) -> float:
    """
    Share of the meaningful words (4+ letters) of one title that the other
    contains, allowing for case endings: "пивной"/"пивная",
    "использования"/"использование" match, "пивоваренного"/"кровельного" do not.
    """
    def same(w1, w2):
        n = 0
        for c1, c2 in zip(w1, w2):
            if c1 != c2:
                break
            n += 1
        return n >= max(3, min(len(w1), len(w2)) - 3)

    words = [w for w in words if len(w) > 3]
    if not words:
        return 1.0
    return sum(1 for w in words if any(same(w, o) for o in other)) / len(words)


def _title_words_sim(a: str, b: str) -> float:
    """Character similarity capped by word coverage in both directions."""
    wa, wb = a.split(), b.split()
    return min(
        difflib.SequenceMatcher(None, a, b).ratio(),
        _word_cover(wa, wb),
        _word_cover(wb, wa),
    )


def _web_label_sim(title: str, label: str) -> float:
    """
    Similarity of a search-result heading to the title. Bing cuts long
    headings with "…"; compare those with the same-length start of the title.
    Character similarity alone is not enough: "Утилизация отходов
    кровельного производства" scored 0.89 against "...пивоваренного..."
    (23.10, item 109), and a paper by the same authors on the same topic
    shares nearly all title words (item 8).
    """
    label = (label or "").strip()
    truncated = label.endswith(("…", "..."))
    a, b = _norm_title(title), _norm_title(label.rstrip("…. "))
    if not a or not b:
        return 0.0
    if truncated and len(b) >= 25:
        # The last visible word may itself be cut.
        b = b.rsplit(" ", 1)[0] if " " in b else b
        a = " ".join(a.split()[:len(b.split())])
    return _title_words_sim(a, b)


def _bing_target(href: str) -> str:
    """
    Bing result links are bing.com/ck/a redirects; the real address is the
    u=a1<base64url> parameter. Before v5.24 the host filter below dropped
    every result because of this.
    """
    if "bing.com/ck/a" not in href:
        return href
    try:
        u = parse_qs(urlparse(href).query).get("u", [""])[0]
        if u.startswith("a1"):
            u = u[2:]
            return base64.urlsafe_b64decode(u + "=" * (-len(u) % 4)).decode("utf-8", "replace")
    except Exception:
        pass
    return href


def _bing_open_candidates(context, title: str, year: int | None, journal: str, exact: bool = True) -> list:
    result, seen = [], set()
    if not title:
        return result
    page = context.new_page()
    try:
        # A long title in quotes usually finds nothing on Bing.
        parts = [f'"{title}"' if exact else title]
        if journal:
            parts.append(f'"{journal}"')
        if year:
            parts.append(str(year))
        query = " ".join(parts)
        url = "https://www.bing.com/search?q=" + quote_plus(query)
        log_line(f"Web search: {url}")
        safe_goto(page, url, 60000)
        page.wait_for_timeout(2500)

        selectors = ("li.b_algo h2 a", "main a", "a")
        collected = 0
        for selector in selectors:
            try:
                links = page.locator(selector)
                for i in range(min(links.count(), 100)):
                    a = links.nth(i)
                    href = _bing_target(a.get_attribute("href") or "")
                    if not href.startswith(("http://", "https://")):
                        continue
                    host = (urlparse(href).netloc or "").lower()
                    if not host or any(x in host for x in ("bing.com", "microsoft.com", "elibrary.ru", "google.com", "yandex.")):
                        continue
                    try:
                        label = (a.inner_text(timeout=250) or "").strip()
                    except Exception:
                        label = ""
                    sim = _web_label_sim(title, label)
                    score = 175 + int(sim * 35)
                    if ".pdf" in href.lower():
                        score += 50
                    _add_open_candidate(result, seen, href, score, f"Bing result sim={sim:.2f}")
                    collected += 1
                    if collected >= 10:
                        break
                if collected >= 10:
                    break
            except Exception:
                pass
    except Exception as exc:
        log_line(f"eLIBRARY Bing lookup failed: {exc}")
    finally:
        try:
            close_page_safely(page)
        except Exception:
            pass
    return result


def _landing_title_matches(context, url: str, target_title: str, min_sim: float = 0.68, unknown: bool = True) -> bool:
    """unknown: the answer when the page shows no title or does not load."""
    if not target_title:
        return True
    page = context.new_page()
    try:
        safe_goto(page, url, 45000)
        page.wait_for_timeout(1800)
        # Cloudflare's "Just a moment..." usually passes by itself.
        for _ in range(6):
            try:
                if "just a moment" not in (page.title() or "").lower():
                    break
            except Exception:
                break
            page.wait_for_timeout(2500)
        got = _page_meta_content(
            page,
            (
                'meta[name="citation_title"]',
                'meta[name="dc.title"]',
                'meta[property="og:title"]',
                'meta[name="twitter:title"]',
            ),
        )
        if not got:
            try:
                got = page.title() or ""
            except Exception:
                got = ""
        if not got:
            return unknown
        # Page titles often carry " | Journal" after the article title.
        if _norm_title(target_title) in _norm_title(got):
            sim = 1.0
        elif min_sim > 0.68:
            sim = _web_label_sim(target_title, got)
        else:
            sim = max(_title_similarity(target_title, got), _web_label_sim(target_title, got))
        log_line(f"Candidate page title check sim={sim:.2f}: {got[:180]}")
        return sim >= min_sim
    except Exception:
        # Network/JS failures should not discard a strong metadata candidate.
        return unknown
    finally:
        try:
            close_page_safely(page)
        except Exception:
            pass


def _rename_external_pdf(path: Path, identity: str) -> Path:
    try:
        if not valid_pdf(path):
            return path
        dest = next_destination("eLIBRARY_WEB", identity)
        path.replace(dest)
        return dest
    except Exception:
        return path


def _try_elibrary_web_candidates(context, candidates: list, title: str, identity: str) -> Path | None:
    candidates = sorted(candidates, key=lambda x: x[0], reverse=True)[:14]

    for score, url, why in candidates:
        log_line(f"eLIBRARY open-web candidate score={score} reason={why}: {url}")

    for attempt, (score, url, why) in enumerate(candidates, 1):
        low = url.lower()

        # Direct PDFs are captured without extra navigation.
        if ".pdf" in low or "pdf=" in low or "/pdf/" in low or "/download/" in low:
            dest = next_destination("eLIBRARY_WEB", identity)
            if capture_pdf_with_fetch(
                context,
                url,
                dest,
                60,
                source_label="eLIBRARY open-web PDF",
            ):
                return dest
            try:
                dest.unlink()
            except Exception:
                pass

        # Landing-page candidates must at least resemble the target title.
        if not _landing_title_matches(context, url, title):
            log_line(f"eLIBRARY open-web skip title mismatch: {url}")
            continue

        try:
            path = download_generic(context, url, extract_doi(url), url)
            if path and valid_pdf(path):
                return _rename_external_pdf(path, identity)
        except NoAccess as exc:
            log_line(f"eLIBRARY open-web candidate not accessible: {exc}")
        except Exception as exc:
            log_line(f"eLIBRARY open-web candidate failed: {exc}")

    return None


def _elibrary_open_web_fallback(context, metadata: dict, identity: str) -> Path:
    title = str(metadata.get("title") or "").strip()
    journal = str(metadata.get("journal") or "").strip()
    year = metadata.get("year")
    doi = metadata.get("doi")

    if not title and not doi:
        raise DownloadError("ELIBRARY_OPEN_WEB_NO_METADATA")

    log_line(
        f"eLIBRARY open-web fallback: title={title[:160]!r}; year={year}; journal={journal[:100]!r}; doi={doi or '-'}"
    )

    # Stage 1: scholarly metadata services. These are much safer than a broad
    # web search because title/year matching can be checked before downloading.
    candidates, seen = [], set()

    if doi:
        _add_open_candidate(candidates, seen, "https://doi.org/" + doi, 280, "DOI found on eLIBRARY page")

    for group in (
        _openalex_open_candidates(title, year),
        _crossref_open_candidates(title, year, journal),
    ):
        for score, url, why in group:
            _add_open_candidate(candidates, seen, url, score, why)

    found = _try_elibrary_web_candidates(context, candidates, title, identity)
    if found:
        return found

    # Stage 2: ordinary web search. This is intentionally second because some
    # Russian articles are open on university/journal sites but absent from DOI
    # registries and OA metadata. No CAPTCHA bypass is attempted.
    web_candidates = _bing_open_candidates(context, title, year, journal)
    found = _try_elibrary_web_candidates(context, web_candidates, title, identity)
    if found:
        return found

    raise DownloadError("ELIBRARY_OPEN_WEB_NOT_FOUND")


def download_elibrary(context, raw: str) -> Path:
    article_url = raw.strip()
    identity = elibrary_identity(article_url)
    page = context.new_page()
    metadata = {}

    try:
        log_line(f"eLIBRARY article open: {article_url}")
        safe_goto(page, article_url, 25000, attempts=2)
        wait_page_ready(page, "eLIBRARY")
        page.wait_for_timeout(1800)

        elibrary_wait_for_login(page, 600)

        # After successful login the user may have been redirected. If so,
        # return to the requested record using the same authenticated session.
        if "item.asp" not in page.url.lower() or f"id={identity}" not in page.url.lower():
            safe_goto(page, article_url, 60000)
            page.wait_for_timeout(3000)

        if elibrary_is_ip_blocked(page):
            raise NoAccess("ELIBRARY_IP_BLOCKED")

        metadata = _elibrary_extract_metadata(page, identity)
        log_line(
            "eLIBRARY metadata: "
            f"title={str(metadata.get('title') or '')[:160]!r}; "
            f"year={metadata.get('year')}; doi={metadata.get('doi') or '-'}"
        )

        candidates = discover_pdf_candidates(
            page,
            page.url or article_url,
            "elibrary",
            metadata.get("doi"),
        )

        for score, url, why in candidates:
            log_line(
                f"eLIBRARY candidate score={score} reason={why}: {url}"
            )

        # First use a full text hosted by eLIBRARY itself when present.
        for attempt, (score, pdf_url, why) in enumerate(candidates[:8], 1):
            dest = next_destination("eLIBRARY", identity)
            log_line(
                f"eLIBRARY attempt {attempt}/{min(len(candidates),8)}: {pdf_url}"
            )
            if capture_pdf_with_fetch(
                context,
                pdf_url,
                dest,
                60,
                source_label="eLIBRARY PDF",
            ):
                return dest
            try:
                dest.unlink()
            except Exception:
                pass

        # Some records expose a JavaScript full-text control or publisher link.
        labels = (
            "Полный текст",
            "Текст статьи",
            "Скачать",
            "PDF",
            "Full text",
            "Download",
        )

        clicked = set()
        for label in labels:
            try:
                loc = page.get_by_text(label, exact=False)
                count = min(loc.count(), 20)
            except Exception:
                continue

            for i in range(count):
                el = loc.nth(i)
                try:
                    if not el.is_visible():
                        continue
                    key = f"{label}:{i}"
                    if key in clicked:
                        continue
                    clicked.add(key)

                    before_pages = list(context.pages)
                    dest = next_destination("eLIBRARY", identity)
                    log_line(f"eLIBRARY click control: {label}")

                    try:
                        with page.expect_download(timeout=15000) as info:
                            el.click(force=True)
                        dl = info.value
                        dl.save_as(dest)
                        if valid_pdf(dest):
                            return dest
                    except Exception:
                        try:
                            dest.unlink()
                        except Exception:
                            pass

                    page.wait_for_timeout(2500)
                    new_pages = [p for p in context.pages if p not in before_pages]
                    targets = new_pages + [page]

                    for target in targets:
                        try:
                            u = target.url or ""
                        except Exception:
                            u = ""

                        if not u.startswith("http"):
                            continue

                        low_u = u.lower()
                        if ".pdf" in low_u or "/download/" in low_u:
                            dest = next_destination("eLIBRARY", identity)
                            if capture_pdf_with_fetch(
                                context,
                                u,
                                dest,
                                60,
                                source_label="eLIBRARY clicked PDF",
                            ):
                                return dest
                            try:
                                dest.unlink()
                            except Exception:
                                pass

                        # eLIBRARY can send the user to the publisher's page.
                        host = (urlparse(u).netloc or "").lower()
                        if host and "elibrary.ru" not in host:
                            try:
                                path = download_generic(context, u, extract_doi(u), u)
                                if valid_pdf(path):
                                    return _rename_external_pdf(path, identity)
                            except Exception as exc:
                                log_line(f"eLIBRARY external full-text route failed: {exc}")
                except Exception as exc:
                    log_line(f"eLIBRARY control click error: {exc}")

        # Key v5.12 behavior: bibliographic record is not treated as failure.
        # Search trusted scholarly metadata, then the ordinary web, for an open
        # copy of the same article and download only a real PDF.
        return _elibrary_open_web_fallback(context, metadata, identity)

    finally:
        try:
            close_page_safely(page)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# TITLE SEARCH (open web)
# ---------------------------------------------------------------------------

TITLE_PREFIX = "title:"

# Web-search hits that never hold the article PDF itself: student essay
# mirrors, paid thesis shops, and sites that need a login or a CAPTCHA.
TITLE_WEB_SKIP_HOSTS = (
    "studopedia", "studfile", "studwood", "studref", "studbooks", "vuzlit",
    "bibliofond", "allbest", "twirpx", "dissercat", "researchgate.net",
    "academia.edu", "scribd.com", "studmed", "pandia", "infopedia",
)


def parse_title_query(raw: str) -> dict | None:
    """
    "title: <title> || <year> || <journal> || #<no>" -> dict, else None.
    Everything after the title is optional and may come in any order.
    """
    if not raw.lower().startswith(TITLE_PREFIX):
        return None
    parts = [x.strip() for x in raw[len(TITLE_PREFIX):].split("||")]
    query = {"title": parts[0], "year": None, "journal": "", "tag": ""}
    for extra in parts[1:]:
        if re.fullmatch(r"(19|20)\d{2}", extra):
            query["year"] = int(extra)
        elif re.fullmatch(r"#\s*\w+", extra):
            query["tag"] = extra.lstrip("#").strip()
        elif extra:
            query["journal"] = extra
    if not query["title"]:
        raise UnsupportedSource("Empty title query")
    return query


def _title_hit(query: dict, title: str, year, journal: str = "") -> float:
    """
    Similarity of a search hit to the wanted article, 0 when it must not be
    used. Short titles ("ПИВНАЯ ДРОБИНА") match many unrelated articles, so
    they need an exact title plus a matching year or journal.
    """
    sim = _title_words_sim(_norm_title(query["title"]), _norm_title(title))
    # A subtitle or a translated second title appended on one side only.
    a, b = sorted((_norm_title(query["title"]), _norm_title(title)), key=len)
    if len(a.split()) >= 6 and b.startswith(a):
        sim = max(sim, 0.9)
    try:
        if query["year"] and year and abs(int(query["year"]) - int(year)) > 1:
            return 0.0
    except (TypeError, ValueError):
        pass
    journal_sim = None
    if query["journal"] and journal:
        journal_sim = _title_similarity(query["journal"], journal)
    short = len(_norm_title(query["title"]).split()) <= 3
    if short:
        same_place = (journal_sim is not None and journal_sim >= 0.6) or (
            query["year"] and year and int(query["year"]) == int(year)
        )
        return sim if sim >= 0.97 and same_place else 0.0
    # Same title in another venue is usually a different text (thesis vs.
    # journal paper); accept it only for a near-exact title.
    if journal_sim is not None and journal_sim < 0.45 and sim < 0.97:
        return 0.0
    return sim if sim >= 0.87 else 0.0


def _http_post_json(url: str, payload: dict, timeout: int = 25):
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130.0 Safari/537.36",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def _title_cyberleninka(query: dict) -> list:
    result, seen = [], set()
    try:
        data = _http_post_json(
            "https://cyberleninka.ru/api/search",
            {"mode": "articles", "q": query["title"], "size": 10, "from": 0},
        )
        for item in data.get("articles") or []:
            name = re.sub(r"<[^>]+>", "", item.get("name") or "")
            sim = _title_hit(query, name, item.get("year"), item.get("journal") or "")
            link = item.get("link") or ""
            if not sim or not link.startswith("/article/"):
                continue
            url = "https://cyberleninka.ru" + link
            _add_open_candidate(result, seen, url + "/pdf", 270 + int(sim * 10), f"CyberLeninka PDF sim={sim:.2f}")
    except Exception as exc:
        log_line(f"Title search CyberLeninka failed: {type(exc).__name__}: {exc}")
    return result


def _openalex_key_param() -> str:
    """
    Without a key OpenAlex shares a tiny daily budget per IP address; on
    23.10 it ran out after about 150 searches. A free key goes into
    OPENALEX_API_KEY or openalex_key.txt next to the scripts folder.
    """
    key = os.environ.get("OPENALEX_API_KEY", "").strip()
    if not key:
        try:
            key = (ROOT / "openalex_key.txt").read_text(encoding="utf-8").strip()
        except OSError:
            key = ""
    return "&api_key=" + quote_plus(key) if key else ""


def _title_openalex(query: dict) -> list:
    result, seen = [], set()
    try:
        data = _http_json(
            "https://api.openalex.org/works?per-page=8&search=" + quote_plus(query["title"])
            + _openalex_key_param()
        )
        for item in data.get("results") or []:
            venue = ((item.get("primary_location") or {}).get("source") or {}).get("display_name") or ""
            sim = _title_hit(query, item.get("title") or "", item.get("publication_year"), venue)
            if not sim:
                continue
            locations = [item.get("best_oa_location")] + list(item.get("locations") or [])
            for loc in locations:
                if not isinstance(loc, dict):
                    continue
                _add_open_candidate(result, seen, loc.get("pdf_url"), 255 + int(sim * 10), f"OpenAlex OA PDF sim={sim:.2f}")
                if loc.get("is_oa"):
                    _add_open_candidate(result, seen, loc.get("landing_page_url"), 200, f"OpenAlex OA landing sim={sim:.2f}")
            doi = extract_doi(str(item.get("doi") or ""))
            if doi:
                _add_open_candidate(result, seen, "https://doi.org/" + doi, 230, f"OpenAlex DOI sim={sim:.2f}")
    except Exception as exc:
        log_line(f"Title search OpenAlex failed: {type(exc).__name__}: {exc}")
    return result


def _title_europepmc(query: dict) -> list:
    """Europe PMC: open full texts of food, feed and biotech papers, no key needed."""
    result, seen = [], set()
    try:
        data = _http_json(
            "https://www.ebi.ac.uk/europepmc/webservices/rest/search?format=json&resultType=core"
            "&pageSize=5&query=" + quote_plus('TITLE:"' + query["title"].replace('"', " ") + '"')
        )
        for item in (data.get("resultList") or {}).get("result") or []:
            journal = ((item.get("journalInfo") or {}).get("journal") or {}).get("title") or ""
            sim = _title_hit(query, item.get("title") or "", item.get("pubYear"), journal)
            if not sim:
                continue
            for link in (item.get("fullTextUrlList") or {}).get("fullTextUrl") or []:
                if link.get("documentStyle") == "pdf" and link.get("availabilityCode") in ("OA", "F"):
                    _add_open_candidate(result, seen, link.get("url"), 255 + int(sim * 10), f"Europe PMC PDF sim={sim:.2f}")
            if item.get("pmcid") and item.get("isOpenAccess") == "Y":
                _add_open_candidate(
                    result, seen, f"https://europepmc.org/articles/{item['pmcid']}?pdf=render",
                    254 + int(sim * 10), f"Europe PMC OA sim={sim:.2f}",
                )
            if item.get("doi"):
                _add_open_candidate(result, seen, "https://doi.org/" + item["doi"], 227, f"Europe PMC DOI sim={sim:.2f}")
    except Exception as exc:
        log_line(f"Title search Europe PMC failed: {type(exc).__name__}: {exc}")
    return result


def _title_crossref(query: dict) -> list:
    result, seen = [], set()
    try:
        data = _http_json(
            "https://api.crossref.org/works?rows=8&query.bibliographic=" + quote_plus(query["title"])
        )
        for item in data.get("message", {}).get("items") or []:
            journal = (item.get("container-title") or [""])[0]
            sim = _title_hit(query, (item.get("title") or [""])[0], _year_from_crossref(item), journal)
            doi = str(item.get("DOI") or "").strip()
            if sim and doi:
                _add_open_candidate(result, seen, "https://doi.org/" + doi, 228, f"Crossref DOI sim={sim:.2f}")
    except Exception as exc:
        log_line(f"Title search Crossref failed: {type(exc).__name__}: {exc}")
    return result


def _pdf_mentions_title(path: Path, title: str) -> bool | None:
    """
    True/False when the first pages could be read, None without pypdf or for
    a scan without a text layer. The title must occur as a phrase (allowing
    for inflection and hyphenation), not as scattered words.
    """
    try:
        from pypdf import PdfReader
    except ImportError:
        return None
    import logging
    # Font-encoding warnings (fontTools missing) flood the job log otherwise.
    logging.getLogger("pypdf").setLevel(logging.ERROR)
    try:
        reader = PdfReader(str(path))
        text = " ".join((page.extract_text() or "") for page in reader.pages[:3])
    except Exception as exc:
        log_line(f"Title check: PDF text not readable ({type(exc).__name__}): {path.name}")
        return None
    # Rejoin words hyphenated across lines ("ПРО- МЫШЛЕННОСТИ").
    text = _norm_title(re.sub(r"(\w)[-­]\s+(\w)", r"\1\2", text))
    if len(text) < 200:
        return None
    target = _norm_title(title)
    n = len(target.split())
    if n < 2:
        return None
    tokens = text.split()
    # Titles in PDFs come with hyphenation, letter spacing and line breaks
    # ("пше- ничного", "К В А С"), so also compare with spaces removed.
    target_ns = target.replace(" ", "")
    target_words = target.split()
    best = 0.0
    for size in sorted({max(2, n - 1), n, n + 1, n + 2, n + 4}):
        for i in range(max(1, len(tokens) - size + 1)):
            window = tokens[i:i + size]
            for a, b in ((target, " ".join(window)), (target_ns, "".join(window))):
                sm = difflib.SequenceMatcher(None, a, b)
                if sm.quick_ratio() <= best or sm.ratio() <= best:
                    continue
                # Same sentence frame, other subject ("кровельного" instead of
                # "пивоваренного") must not count.
                if _word_cover(target_words, window) >= 0.85:
                    best = sm.ratio()
    log_line(f"Title check: best title phrase match in PDF text {best:.2f}")
    # 23.10: correct PDFs scored 0.83-0.84 against 0.85; unrelated ones
    # stay near 0.4-0.6.
    return best >= 0.8


def _title_accept_pdf(path: Path | None, query: dict, identity: str) -> Path | None:
    if not path or not valid_pdf(path):
        return None
    check = _pdf_mentions_title(path, query["title"])
    if check is False:
        # Kept for a human look instead of deleted: the check can be wrong.
        rejected = PDF_DIR / "_rejected"
        try:
            rejected.mkdir(exist_ok=True)
            path.replace(rejected / path.name.replace("TITLE_TMP_", "TITLE_REJECTED_"))
        except Exception:
            pass
        log_line(f"Title search: PDF text does not contain the title; moved to _rejected: {path.name}")
        return None
    if check is None:
        log_line("Title search: PDF text not checked (no pypdf or no text layer)")
    dest = next_destination("TITLE", identity)
    try:
        path.replace(dest)
        return dest
    except Exception:
        return path


def _title_try_candidate(context, url: str, why: str, query: dict, identity: str) -> Path | None:
    doi = extract_doi(url) if "doi.org/" in url.lower() else None
    if doi:
        source, resolved = identify_source(context, url, doi)
        log_line(f"Title search DOI {doi} -> source {source}")
        return _title_accept_pdf(
            _download_routed(context, source, url, doi, resolved), query, identity
        )

    low = url.lower()
    if low.endswith("/pdf") or ".pdf" in low or "/download/" in low or "pdf=" in low:
        dest = next_destination("TITLE_TMP", identity)
        if save_pdf_direct_http(context, url, dest) or capture_pdf_with_fetch(
            context, url, dest, 45, source_label="Title search PDF"
        ):
            return _title_accept_pdf(dest, query, identity)
        try:
            dest.unlink()
        except Exception:
            pass
        if "cyberleninka.ru" in low:
            return None

    # A landing page found by a web search must show the wanted title;
    # metadata hits were already matched by title.
    if why.startswith("Bing") and not _landing_title_matches(context, url, query["title"], min_sim=0.85, unknown=False):
        log_line(f"Title search: landing title mismatch, skipped: {url}")
        return None
    return _title_accept_pdf(download_generic(context, url, None, url), query, identity)


def download_by_title(context, query: dict) -> Path:
    title = query["title"]
    identity = f"{query['tag']}_{title[:70]}" if query["tag"] else title[:80]
    log_line(
        f"Title search: {title[:160]!r}; year={query['year'] or '-'}; "
        f"journal={query['journal'][:80]!r}"
    )

    candidates, seen = [], set()
    # Semantic Scholar answered 403 to every request on 23.10, so it is not used.
    for collect in (_title_cyberleninka, _title_openalex, _title_europepmc, _title_crossref):
        for score, url, why in collect(query):
            _add_open_candidate(candidates, seen, url, score, why)

    job_deadline = os.environ["ARTICLE_JOB_DEADLINE"]
    closed = []  # pages that show the article but sell it or have no file

    def run(items: list, limit: int = 12) -> Path | None:
        for score, url, why in sorted(items, key=lambda x: x[0], reverse=True)[:limit]:
            if job_bounded_wait(9999, margin_sec=60) <= 0:
                log_line("Title search: job deadline reached")
                return None
            log_line(f"Title search candidate score={score} reason={why}: {url}")
            # Nobody watches an unattended title search: a CAPTCHA or a slow
            # site may cost one candidate two minutes, not the whole job.
            os.environ["ARTICLE_JOB_DEADLINE"] = f"{min(float(job_deadline), time.time() + 90):.0f}"
            try:
                found = _title_try_candidate(context, url, why, query, identity)
                if found:
                    return found
            except NoAccess as exc:
                log_line(f"Title search candidate closed: {exc}")
                closed.append(f"{str(exc).split(': ')[-1]} {url}")
            except Exception as exc:
                log_line(f"Title search candidate failed: {type(exc).__name__}: {exc}")
            finally:
                os.environ["ARTICLE_JOB_DEADLINE"] = job_deadline
        return None

    found = run(candidates)
    if found:
        return found

    # Ordinary web search last: journal and university sites that are absent
    # from the scholarly indexes. Only results whose link text resembles the
    # title are opened. A two-word title would match any article on the topic.
    if len(_norm_title(title).split()) > 3:
        web = [
            item for item in _bing_open_candidates(context, title, None, "", exact=False)
            if float(re.search(r"sim=([\d.]+)", item[2]).group(1)) >= 0.85
            and not any(h in (urlparse(item[1]).netloc or "").lower() for h in TITLE_WEB_SKIP_HOSTS)
        ]
        # English titles bring many mirrors and indexes; the best few decide.
        found = run(web, limit=6)
        if found:
            return found

    dois = sorted({extract_doi(u) for _, u, _ in candidates if "doi.org/" in u.lower()} - {None})
    if dois or closed:
        raise NoAccess(
            "TITLE_FOUND_NO_FREE_PDF: "
            + "; ".join(["DOI " + d for d in dois] + closed[:3])
        )
    raise DownloadError("TITLE_NOT_FOUND_OPEN_WEB")


# ---------------------------------------------------------------------------
# CNKI
# ---------------------------------------------------------------------------

def exact_visible_text(page, text_value: str):
    hits = []

    for fr in page.frames:
        try:
            loc = fr.get_by_text(text_value, exact=True)

            for i in range(min(loc.count(), 60)):
                el = loc.nth(i)
                try:
                    if not el.is_visible():
                        continue
                    box = el.bounding_box()
                    if not box:
                        continue

                    meta = el.evaluate(
                        """e => ({
                            tag:e.tagName,
                            id:e.id || '',
                            cls:typeof e.className === 'string' ? e.className : '',
                            href:e.getAttribute ? (e.getAttribute('href') || '') : ''
                        })"""
                    )
                    hits.append((fr, el, box, meta))
                except Exception:
                    pass
        except Exception:
            pass

    return hits


def cnki_extract_title_from_resolver(page) -> str | None:
    """
    CHN DOI pages often expose the Chinese title even when they do not expose
    an Overseas CNKI link. Extract it for a title-search fallback.
    """
    try:
        body = page.locator("body").inner_text(timeout=5000)
    except Exception:
        return None

    patterns = (
        r"题名\s*[:：]\s*([^\r\n]+)",
        r"标题\s*[:：]\s*([^\r\n]+)",
        r"Title\s*[:：]\s*([^\r\n]+)",
    )

    for pattern in patterns:
        m = re.search(pattern, body, flags=re.I)
        if m:
            title = re.sub(r"\s+", " ", m.group(1)).strip()
            if len(title) >= 4:
                return title

    return None


def cnki_try_direct_doi_link(context, doi: str):
    """
    Some records are resolvable by CNKI's Overseas DOI gateway even when the
    CHN DOI resolver page does not render the upper link.
    """
    url = "https://link.oversea.cnki.net/doi/" + doi
    log_line(f"CNKI direct Overseas DOI fallback: {url}")

    page = context.new_page()

    try:
        page.bring_to_front()
    except Exception:
        pass

    safe_goto(page, url, 60000)

    deadline = time.time() + 45

    while time.time() < deadline:
        current = page.url.lower()

        if (
            "oversea.cnki.net" in current
            and "/article/" in current
        ):
            log_line(f"CNKI direct DOI resolved to article: {page.url}")
            return page

        try:
            body = page.locator("body").inner_text(timeout=3000)
        except Exception:
            body = ""

        if doi.lower() in body.lower() and "oversea.cnki.net" in current:
            log_line(f"CNKI direct DOI page contains DOI: {page.url}")
            return page

        page.wait_for_timeout(1000)

    try:
        close_page_safely(page)
    except Exception:
        pass

    return None


def cnki_find_result_article(context, page, doi: str, title: str | None, timeout_sec: int = 35):
    """
    Detect a CNKI result using DOI, title text, or known article-link shapes.
    """
    deadline = time.time() + timeout_sec

    while time.time() < deadline:
        # 1. Strongest match: visible title link.
        if title:
            for fr in page.frames:
                try:
                    loc = fr.locator("a")

                    for i in range(min(loc.count(), 500)):
                        el = loc.nth(i)

                        try:
                            if not el.is_visible():
                                continue

                            text = re.sub(
                                r"\s+",
                                " ",
                                (el.inner_text() or "")
                            ).strip()

                            if not text:
                                continue

                            # Case, quotes (' vs ’) and spacing differ between
                            # the list and CNKI's English titles.
                            nt, nx = _norm_title(title), _norm_title(text)
                            if nt and nx and (nt in nx or (len(nx) > 20 and nx in nt)
                                              or _title_words_sim(nt, nx) >= 0.85):
                                href = el.get_attribute("href") or ""

                                if href and (
                                    "article" in href.lower()
                                    or "kcms" in href.lower()
                                    or "detail" in href.lower()
                                ):
                                    article_url = urljoin(page.url, href)
                                    log_line(
                                        f"CNKI title result: {article_url}"
                                    )

                                    article = context.new_page()
                                    try:
                                        article.bring_to_front()
                                    except Exception:
                                        pass

                                    safe_goto(article, article_url, 60000)
                                    return article

                        except Exception:
                            pass
                except Exception:
                    pass

        # 2. Look through known article links and surrounding result rows.
        candidates = []

        for fr in page.frames:
            try:
                links = fr.locator("a")

                for i in range(min(links.count(), 800)):
                    el = links.nth(i)

                    try:
                        if not el.is_visible():
                            continue

                        href = el.get_attribute("href") or ""
                        if not href:
                            continue

                        low_href = href.lower()

                        if not (
                            "kcms" in low_href
                            or "/article/" in low_href
                            or "detail" in low_href
                            or "abstract" in low_href
                        ):
                            continue

                        text = ""
                        surrounding = ""

                        try:
                            text = re.sub(
                                r"\s+",
                                " ",
                                (el.inner_text() or "")
                            ).strip()
                        except Exception:
                            pass

                        try:
                            surrounding = el.evaluate(
                                """e => {
                                    const row = e.closest(
                                      'tr,li,.result-item,.result-table-list,.result,.list-item,.search-result-item'
                                    );
                                    return row ? (row.innerText || '') : (e.innerText || '');
                                }"""
                            )
                        except Exception:
                            pass

                        strong = False

                        if doi and doi.lower() in (surrounding or "").lower():
                            strong = True

                        if title and _norm_title(title) in _norm_title(surrounding or ""):
                            strong = True

                        candidates.append(
                            (strong, text, href)
                        )

                    except Exception:
                        pass

            except Exception:
                pass

        strong_hits = [x for x in candidates if x[0]]

        chosen = None

        if strong_hits:
            chosen = strong_hits[0]
        elif len(candidates) == 1:
            chosen = candidates[0]

        if chosen:
            _, text, href = chosen
            article_url = urljoin(page.url, href)

            log_line(
                f"CNKI result article: {article_url} text={text!r}"
            )

            article = context.new_page()
            try:
                article.bring_to_front()
            except Exception:
                pass

            safe_goto(article, article_url, 60000)
            return article

        page.wait_for_timeout(1000)

    return None


def cnki_submit_professional_query(context, page, query: str, doi: str, title: str | None):
    """
    Open Professional Search fresh, fill one query, submit, and try to open
    a result article.
    """
    safe_goto(
        page,
        "https://oversea.cnki.net/kns8s/advsearch?language=en",
        60000,
    )
    page.wait_for_timeout(3000)

    try:
        page.bring_to_front()
    except Exception:
        pass

    # Switch to Professional Search.
    clicked_tab = False

    for selector in (
        'a:has-text("Professional Search")',
        'button:has-text("Professional Search")',
        'li:has-text("Professional Search")',
        'a:has-text("专业检索")',
        'button:has-text("专业检索")',
        'li:has-text("专业检索")',
    ):
        try:
            loc = page.locator(selector)

            for i in range(min(loc.count(), 20)):
                el = loc.nth(i)

                try:
                    if el.is_visible():
                        el.click(force=True)
                        clicked_tab = True
                        break
                except Exception:
                    pass

            if clicked_tab:
                break

        except Exception:
            pass

    page.wait_for_timeout(2200)

    # The Professional Search expression editor is normally the large field.
    field = None
    best_area = 0

    for fr in page.frames:
        for selector in (
            "textarea",
            '[contenteditable="true"]',
            'input[type="text"]',
            'input[type="search"]',
            'input:not([type])',
        ):
            try:
                loc = fr.locator(selector)

                for i in range(min(loc.count(), 80)):
                    el = loc.nth(i)

                    try:
                        if not el.is_visible():
                            continue

                        box = el.bounding_box()
                        if not box or box["width"] < 300:
                            continue

                        area = box["width"] * box["height"]

                        # Prefer the tall Professional Search expression editor.
                        if box["height"] >= 70:
                            area *= 5

                        if area > best_area:
                            best_area = area
                            field = el

                    except Exception:
                        pass

            except Exception:
                pass

    if field is None:
        raise DownloadError("CNKI_PRO_SEARCH_FIELD_NOT_FOUND")

    log_line(f"CNKI Professional query: {query}")

    try:
        field.click(force=True)
    except Exception:
        pass

    try:
        field.fill(query)
    except Exception:
        field.evaluate(
            """(e, value) => {
                e.focus();
                if ('value' in e) {
                    e.value = value;
                    e.dispatchEvent(new Event('input', {bubbles:true}));
                    e.dispatchEvent(new Event('change', {bubbles:true}));
                } else {
                    e.innerText = value;
                    e.dispatchEvent(
                      new InputEvent('input', {bubbles:true, data:value})
                    );
                }
            }""",
            query,
        )

    # Submit. Prefer the orange Search button close to the editor.
    search_button = None

    for fr in page.frames:
        for selector in (
            'button:has-text("Search")',
            'a:has-text("Search")',
            'input[type="submit"]',
            'button:has-text("检索")',
            'a:has-text("检索")',
        ):
            try:
                loc = fr.locator(selector)

                for i in range(min(loc.count(), 40)):
                    el = loc.nth(i)

                    try:
                        if el.is_visible():
                            search_button = el
                            break
                    except Exception:
                        pass

                if search_button is not None:
                    break

            except Exception:
                pass

        if search_button is not None:
            break

    if search_button is None:
        try:
            field.press("Enter")
        except Exception as exc:
            raise DownloadError(
                f"CNKI_PRO_SEARCH_BUTTON_NOT_FOUND: {exc}"
            ) from exc
    else:
        search_button.click(force=True)

    log_line("CNKI Professional Search submitted.")

    return cnki_find_result_article(
        context,
        page,
        doi,
        title,
        timeout_sec=35,
    )


def cnki_professional_search(context, page, doi: str, title: str | None = None):
    """
    Robust fallback for CNKI records not exposed by CHN DOI.

    Tries:
      1) several DOI expression variants;
      2) title search if CHN DOI exposed a title.
    """
    log_line(
        "CNKI CHN DOI has no Overseas link; "
        "falling back to Professional Search."
    )

    queries = []
    if title:
        safe_title = title.replace("'", "’")
        queries.append(f"TI='{safe_title}'")
    if doi:
        queries += [f"DOI='{doi}'", f'DOI="{doi}"', f"DOI={doi}"]
    if title:
        queries.append(f"TKA='{safe_title}'")

    for idx, query in enumerate(queries, 1):
        log_line(
            f"CNKI Professional attempt {idx}/{len(queries)}"
        )

        article = cnki_submit_professional_query(
            context,
            page,
            query,
            doi,
            title,
        )

        if article is not None:
            return article

    raise DownloadError("CNKI_PRO_SEARCH_RESULT_NOT_FOUND")




def cnki_direct_identity(raw_url: str | None) -> str:
    """Create a stable, filesystem-safe identity for CNKI records without DOI."""
    raw_url = (raw_url or "").strip()
    if raw_url:
        try:
            parsed = urlparse(raw_url)
            path = unquote(parsed.path or "")
            m = re.search(r"/urlid/([^/?#]+)", path, flags=re.I)
            if m:
                return "urlid_" + m.group(1)
        except Exception:
            pass

    digest = hashlib.sha1(raw_url.encode("utf-8", errors="ignore")).hexdigest()[:16]
    return "direct_" + digest


def download_cnki(context, doi: str | None, direct_url: str | None = None, title: str | None = None) -> Path:
    """title: search CNKI by title first (e.g. a Wanfang DOI that CNKI does not know)."""
    if not doi and not direct_url and not title:
        raise DownloadError("CNKI handler requires DOI, direct CNKI URL or title")

    start = context.new_page()
    try:
        start.bring_to_front()
    except Exception:
        pass

    resolver = context.new_page()
    article = None

    try:
        cnki_start = "https://oversea.cnki.net/kns8s/advsearch?language=en"
        log_line(f"CNKI session start: {cnki_start}")
        try:
            start.bring_to_front()
        except Exception:
            pass
        start_ok = True
        try:
            safe_goto(start, cnki_start, 60000, attempts=1)
        except Exception as exc:
            # 23-24.10: the work network resets kns8s/advsearch now and then
            # while article and DOI pages on the same host load. The session
            # page only warms up cookies; the later steps open their own pages
            # (the professional search navigates to advsearch itself).
            start_ok = False
            log_line(
                f"CNKI session start failed ({type(exc).__name__}); continuing without it"
            )

        deadline = time.time() + 120
        while start_ok and time.time() < deadline:
            if "oversea.cnki.net" in start.url.lower():
                try:
                    if start.locator("body").count() > 0:
                        start.wait_for_timeout(3000)
                        break
                except Exception:
                    pass
            start.wait_for_timeout(1500)
        else:
            if start_ok:
                raise DownloadError("CNKI_START_TIMEOUT")

        if title:
            log_line(f"CNKI search by title: {title[:160]}")
            article = cnki_professional_search(context, start, doi, title)
        elif direct_url and not doi:
            log_line(f"CNKI direct article URL: {direct_url}")
            try:
                resolver.bring_to_front()
            except Exception:
                pass
            safe_goto(resolver, direct_url, 60000)
            resolver.wait_for_timeout(3000)
            article = resolver
        else:
            resolver_url = "https://www.chndoi.org/Resolution/Handler?doi=" + doi
            log_line(f"CNKI CHN DOI: {resolver_url}")
            try:
                resolver.bring_to_front()
            except Exception:
                pass
            safe_goto(resolver, resolver_url, 60000)
            resolver.wait_for_timeout(2500)

            deadline = time.time() + 90
            link = None
            while time.time() < deadline:
                links = resolver.locator('a[href*="link.oversea.cnki.net/doi/"]')
                if links.count() > 0:
                    link = links.first
                    break
                resolver.wait_for_timeout(1000)

            if link is None:
                resolver_title = cnki_extract_title_from_resolver(
                    resolver
                )

                if resolver_title:
                    log_line(
                        f"CNKI resolver title: {resolver_title}"
                    )

                # First try the same Overseas DOI gateway directly.
                article = cnki_try_direct_doi_link(
                    context,
                    doi,
                )

                # If the gateway still cannot resolve the record, search CNKI.
                if article is None:
                    article = cnki_professional_search(
                        context,
                        start,
                        doi,
                        resolver_title,
                    )
            else:
                href = link.get_attribute("href") or ""
                log_line(f"CNKI upper Overseas link: {href}")

                target = (link.get_attribute("target") or "").lower()

                if target == "_blank":
                    with context.expect_page(timeout=60000) as info:
                        link.click(force=True)
                    article = info.value
                else:
                    link.click(force=True)
                    article = resolver

        try:
            article.bring_to_front()
        except Exception:
            pass

        article.wait_for_timeout(2500)

        # Important: no generic Chinese CAPTCHA keyword scan here.
        # It caused a false positive in testing.
        deadline = time.time() + 180
        stable = 0

        while time.time() < deadline:
            try:
                body = article.locator("body").inner_text(timeout=5000)
            except Exception:
                body = ""

            doi_seen = True if not doi else doi.lower() in body.lower()
            current_url = article.url.lower()
            article_url = (
                "cnki.net" in current_url
                and (
                    "/article/" in current_url
                    or "/kcms2/article/" in current_url
                    or "/urlid/" in current_url
                )
            )
            download_hits = exact_visible_text(article, "下载")
            if not download_hits:
                download_hits = exact_visible_text(article, "Download")

            ready = article_url and doi_seen and bool(download_hits)
            stable = stable + 1 if ready else 0

            if stable >= 2:
                article.wait_for_timeout(2000)
                break

            article.wait_for_timeout(1500)
        else:
            raise DownloadError("CNKI_ARTICLE_TIMEOUT")

        hits = exact_visible_text(article, "下载")
        if not hits:
            hits = exact_visible_text(article, "Download")
        if not hits:
            raise DownloadError("CNKI_DOWNLOAD_TEXT_NOT_FOUND")

        # Click the text itself, not the arrow icon.
        hits.sort(
            key=lambda item: (
                item[2]["width"] < 120,
                item[2]["height"] < 80,
                item[2]["y"],
            ),
            reverse=True,
        )

        _, download_text, box, _ = hits[0]
        log_line(f"CNKI click exact Download label at {box}")
        download_text.click(force=True)

        # Wait for exact visible PDF item.
        deadline = time.time() + 45
        pdf_item = None

        while time.time() < deadline:
            pdf_hits = exact_visible_text(article, "PDF")
            compact = [
                item
                for item in pdf_hits
                if item[2]["width"] < 180 and item[2]["height"] < 100
            ]

            if compact:
                compact.sort(key=lambda item: item[2]["y"], reverse=True)
                pdf_item = compact[0][1]
                break

            article.wait_for_timeout(500)

        if pdf_item is None:
            raise DownloadError("CNKI_PDF_MENU_NOT_FOUND")

        identity = doi or cnki_direct_identity(direct_url or article.url)
        dest = next_destination("CNKI", identity)

        try:
            pdf_href = pdf_item.evaluate(
                "el => (el.closest('a') && el.closest('a').href) || ''"
            )
        except Exception:
            pdf_href = ""
        log_line(f"CNKI click exact PDF{' -> ' + pdf_href if pdf_href else ''}")

        # 24.10: the PDF link opens bar.cnki.net in a NEW tab and the file
        # downloads there; waiting for a download on the article tab only ran
        # out after 3 minutes every time. Watch every tab instead.
        downloads = []
        new_pages = []

        def watch(pg):
            new_pages.append(pg)
            pg.on("download", lambda d: downloads.append(d))

        article.on("download", lambda d: downloads.append(d))
        context.on("page", watch)
        try:
            pdf_item.click(force=True)
            deadline = time.time() + job_bounded_wait(90)
            while time.time() < deadline and not downloads:
                article.wait_for_timeout(1000)
        finally:
            try:
                context.remove_listener("page", watch)
            except Exception:
                pass

        try:
            if downloads:
                dl = downloads[0]
                dl.save_as(dest)
                if valid_pdf(dest):
                    return dest
                name = dl.suggested_filename or ""
                data = dest.read_bytes() if dest.exists() else b""
                log_line(
                    f"CNKI download is not a clean PDF: {name!r}, {len(data)} bytes, "
                    f"head={data[:16]!r}, tail={data[-24:]!r}"
                )
                # 24.10: CNKI files arrived but failed the strict trailer check.
                # A real PDF body with an unusual ending is still the article.
                if data.startswith(b"%PDF-") and len(data) > 20000 and b"%%EOF" in data:
                    log_line("CNKI PDF with a non-standard trailer accepted")
                    return dest
                rejected = PDF_DIR / "_rejected"
                try:
                    rejected.mkdir(exist_ok=True)
                    dest.replace(rejected / sanitize_filename(f"CNKI_{identity}_{name}"))
                except Exception:
                    pass
                kind = "CAJ" if data[:3] in (b"CAJ", b"HN\x00") or name.lower().endswith((".caj", ".kdh", ".nh")) else "not PDF"
                raise DownloadError(f"CNKI file is {kind}: {name or '?'} head={data[:8]!r}")

            # No download event: report what the new tab shows and try its
            # URL with the authorized browser session.
            seen = []
            for pg in new_pages:
                try:
                    url, title = pg.url, pg.title()
                    body = page_text_lower(pg)
                except Exception:
                    continue
                seen.append(f"{title[:60]} <{url[:120]}>")
                log_line(f"CNKI PDF click opened: {title[:80]} <{url}>")
                # e.g. "The product is not subscribed by your institute or the
                # full text download is not enabled" (conference papers, 24.10)
                if "errormsg" in url.lower():
                    msg = unquote(parse_qs(urlparse(url).query).get("ErrMsg", [""])[0])
                    raise NoAccess(f"CNKI_NOT_SUBSCRIBED: {msg[:200] or url}")
                for hint in ("登录", "sign in", "log in", "login", "充值", "余额", "purchase", "购买"):
                    if hint in body:
                        raise NoAccess(f"CNKI_DOWNLOAD_NEEDS_LOGIN_OR_PAYMENT ({hint}): {url}")
                if url.startswith("http") and capture_pdf_with_fetch(
                    context, url, dest, 60, source_label="CNKI download tab"
                ):
                    return dest
            if pdf_href.startswith("http") and capture_pdf_with_fetch(
                context, pdf_href, dest, 60, source_label="CNKI PDF link"
            ):
                return dest
            raise DownloadError(
                "CNKI PDF click did not start download; tabs: " + ("; ".join(seen) or "none")
            )
        finally:
            for pg in new_pages:
                close_page_safely(pg)

    finally:
        for pg in (article, resolver, start):
            if pg is not None:
                try:
                    # resolver may be the same object as article
                    if not pg.is_closed():
                        pg.close()
                except Exception:
                    pass


# ---------------------------------------------------------------------------
# DEDICATED BROWSERS SELF-HEAL
# ---------------------------------------------------------------------------

def cdp_alive(base_url: str, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(
            base_url.rstrip("/") + "/json/version",
            timeout=timeout,
        ) as response:
            return response.status == 200
    except Exception:
        return False


def edge_cdp_alive(timeout: float = 2.0) -> bool:
    return cdp_alive(EDGE_CDP_URL, timeout)


def browser_cdp_alive(timeout: float = 2.0) -> bool:
    return cdp_alive(BROWSER_CDP_URL, timeout)


# v5.21: Playwright connect_over_cdp attaches to EVERY open tab before it
# returns. Jobs killed by the watcher timeout (or STOP) never run their
# finally/page.close(), so tabs pile up in the dedicated profile; one tab stuck
# on a dialog/PDF viewer is enough for the attach to hang after
# "<ws connected>". The plain HTTP endpoints below still work in that state.
CDP_MAX_IDLE_TABS = 4


def cdp_page_targets(base_url: str, timeout: float = 3.0) -> list | None:
    try:
        with urllib.request.urlopen(
            base_url.rstrip("/") + "/json/list",
            timeout=timeout,
        ) as response:
            data = json.loads(response.read().decode("utf-8", errors="replace"))
        return [t for t in data if t.get("type") == "page"]
    except Exception:
        return None


def close_cdp_tabs(base_url: str, label: str) -> int:
    """Open one fresh blank tab and close every other tab over HTTP."""
    pages = cdp_page_targets(base_url)
    if not pages:
        return 0

    base = base_url.rstrip("/")
    fresh_id = None
    for method in ("PUT", "GET"):
        try:
            req = urllib.request.Request(base + "/json/new?about:blank", method=method)
            with urllib.request.urlopen(req, timeout=5) as response:
                fresh_id = json.loads(response.read().decode("utf-8")).get("id")
            break
        except Exception:
            continue

    closed = 0
    for target in pages:
        target_id = target.get("id")
        if not target_id or target_id == fresh_id:
            continue
        try:
            with urllib.request.urlopen(base + "/json/close/" + target_id, timeout=5):
                closed += 1
        except Exception:
            pass

    log_line(f"{label}: closed {closed} of {len(pages)} leftover tab(s) via CDP HTTP")
    time.sleep(1)
    return closed


# v6.0 portable: the dedicated browser is chosen by ARTICLE_BROWSER
# (auto | yandex | edge | chrome, or the path to a browser .exe). "auto"
# prefers Yandex Browser: its dedicated profile keeps the Elsevier check
# passed far longer than Edge did. ARTICLE_BROWSER_ARGS adds extra switches.
#
# The profile stays in %LOCALAPPDATA%, not in the portable folder: Windows
# encrypts cookies per user and computer, so a copied profile loses its
# logins anyway, and the folder that is copied around or synced through
# OneDrive carries no sign-ins. The Yandex profile is the one v5 used.
BROWSER_KINDS = ("yandex", "edge", "chrome")
BROWSER_TITLES = {
    "yandex": "Яндекс.Браузер",
    "edge": "Microsoft Edge",
    "chrome": "Google Chrome",
    "custom": "браузер",
}
BROWSER_PROFILE_NAMES = {
    "yandex": "YandexProfile",
    "edge": "EdgeProfile",
    "chrome": "ChromeProfile",
    "custom": "BrowserProfile",
}
BROWSER_EXE_NAMES = {
    "browser.exe": "yandex",
    "msedge.exe": "edge",
    "chrome.exe": "chrome",
}


def find_browser_exe(kind: str) -> str | None:
    local = os.environ.get("LOCALAPPDATA")
    pf = os.environ.get("ProgramFiles")
    pf86 = os.environ.get("ProgramFiles(x86)")

    if kind == "yandex":
        bases = (local, pf, pf86)
        rel = ("Yandex", "YandexBrowser", "Application", "browser.exe")
    elif kind == "edge":
        bases = (pf86, pf, local)
        rel = ("Microsoft", "Edge", "Application", "msedge.exe")
    elif kind == "chrome":
        bases = (pf, pf86, local)
        rel = ("Google", "Chrome", "Application", "chrome.exe")
    else:
        return None

    for base in bases:
        if base:
            candidate = Path(base, *rel)
            if candidate.exists():
                return str(candidate)

    return None


def find_edge_exe() -> str | None:
    return find_browser_exe("edge")


def find_yandex_exe() -> str | None:
    return find_browser_exe("yandex")


def dedicated_browser() -> tuple[str, str]:
    """(kind, executable) of the dedicated browser for ARTICLE_BROWSER."""
    choice = (os.environ.get("ARTICLE_BROWSER") or "auto").strip().strip('"')

    if choice.lower() not in ("auto",) + BROWSER_KINDS:
        exe = Path(choice)
        if not exe.is_file():
            raise DownloadError(f"Browser executable not found: {choice}")
        return BROWSER_EXE_NAMES.get(exe.name.lower(), "custom"), str(exe)

    kinds = BROWSER_KINDS if choice.lower() == "auto" else (choice.lower(),)
    for kind in kinds:
        exe = find_browser_exe(kind)
        if exe:
            return kind, exe

    wanted = ", ".join(BROWSER_TITLES[k] for k in kinds)
    raise DownloadError(f"Browser not found: {wanted}")


def browser_profile_dir(kind: str | None = None) -> Path:
    override = os.environ.get("ARTICLE_PROFILE_DIR")
    if override:
        return Path(override)

    if kind is None:
        kind = dedicated_browser()[0]

    local = os.environ.get("LOCALAPPDATA")
    base = Path(local) / "ArticleDownloader" if local else ROOT / "profile"
    return base / BROWSER_PROFILE_NAMES.get(kind, "BrowserProfile")


def clear_session_restore(profile: Path):
    """
    Remove only the 'reopen previous tabs' files of a STOPPED profile.
    Cookies, logins and site settings are kept.
    """
    default = profile / "Default"
    removed = 0
    for name in ("Current Session", "Current Tabs", "Last Session", "Last Tabs"):
        try:
            (default / name).unlink()
            removed += 1
        except FileNotFoundError:
            pass
        except Exception as exc:
            log_line(f"Session file not removed ({name}): {exc}")
    sessions = default / "Sessions"
    if sessions.is_dir():
        for item in sessions.iterdir():
            try:
                item.unlink()
                removed += 1
            except Exception as exc:
                log_line(f"Session file not removed ({item.name}): {exc}")
    if removed:
        log_line(f"Cleared {removed} session-restore file(s) in {profile}")


def ensure_edge_running(timeout_sec: int = 40):
    # v5.13 compatibility route: legacy Edge requests go to the browser/9223.
    return ensure_browser_running(timeout_sec)


def ensure_browser_running(timeout_sec: int = 40):
    """
    All routes use the dedicated persistent browser profile on port 9223.
    If it is not running, restart the same profile so its cookies/session
    survive normal browser restarts.
    """
    if browser_cdp_alive():
        return

    kind, exe = dedicated_browser()
    title = BROWSER_TITLES.get(kind, kind)

    profile = browser_profile_dir(kind)
    profile.mkdir(parents=True, exist_ok=True)

    log_line(
        f"Dedicated browser ({title}) is not reachable on port 9223; starting it."
    )

    args = [
        exe,
        "--remote-debugging-port=9223",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-mode",
    ]
    extra = os.environ.get("ARTICLE_BROWSER_ARGS", "").strip()
    if extra:
        args.extend(extra.split())
    args.append("about:blank")

    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

    try:
        subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )
    except Exception as exc:
        raise DownloadError(
            f"Cannot start dedicated browser ({title}): {exc}"
        ) from exc

    deadline = time.time() + timeout_sec

    while time.time() < deadline:
        if browser_cdp_alive():
            log_line(f"Dedicated browser ({title}) is ready on port 9223.")
            time.sleep(2)
            return
        time.sleep(1)

    raise DownloadError(
        f"Dedicated browser ({title}) was started but CDP port 9223 "
        "did not become ready"
    )


def _is_dedicated_browser_cmdline(cmdline: list[str], profile: Path) -> bool:
    joined = " ".join(cmdline).lower()
    return (
        "--remote-debugging-port=9223" in joined
        or str(profile).lower() in joined
    )


def stop_dedicated_browser():
    """
    Stop ONLY the ArticleDownloader browser instance (port 9223 / its
    profile). Normal browser windows and profiles are not targeted.
    """
    try:
        profile = browser_profile_dir()
    except Exception:
        profile = Path("ArticleDownloader") / "BrowserProfile"

    try:
        import psutil
    except ImportError:
        psutil = None

    if psutil is None:
        if os.name != "nt":
            return
        ps = r"""
$items = Get-CimInstance Win32_Process |
    Where-Object {
        @('browser.exe','msedge.exe','chrome.exe') -contains $_.Name -and (
            $_.CommandLine -like '*--remote-debugging-port=9223*' -or
            $_.CommandLine -like '*ArticleDownloader*Profile*')
    }
foreach($p in $items) {
    try {
        Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop
        Write-Output ("Stopped dedicated browser PID " + $p.ProcessId)
    } catch {}
}
"""
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
                text=True,
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=20,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            for line in result.stdout.splitlines():
                if line.strip():
                    log_line(line)
        except Exception as exc:
            log_line(f"Could not stop dedicated browser cleanly: {exc}")
        return

    names = {"browser.exe", "msedge.exe", "chrome.exe", "browser", "msedge",
             "chrome", "chromium", "chrome-wrapper"}
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            name = (proc.info.get("name") or "").lower()
            cmdline = proc.info.get("cmdline") or []
            if name not in names and not any("chrom" in c.lower() for c in cmdline[:1]):
                continue
            if not _is_dedicated_browser_cmdline(cmdline, profile):
                continue
            proc.kill()
            log_line(f"Stopped dedicated browser PID {proc.pid}")
        except (psutil.Error, OSError):
            pass


def restart_browser_running(timeout_sec: int = 45):
    """
    Recover from a stale browser CDP context.
    """
    log_line(
        "Restarting dedicated browser because its CDP context is stale."
    )

    stop_dedicated_browser()

    deadline = time.time() + 12
    while time.time() < deadline:
        if not browser_cdp_alive():
            break
        time.sleep(0.5)

    if browser_cdp_alive():
        raise DownloadError(
            "Dedicated browser could not be stopped; "
            "port 9223 is still occupied"
        )

    # Otherwise the browser reopens the same stale tabs and the next
    # connect_over_cdp hangs exactly like before the restart.
    time.sleep(1)
    try:
        clear_session_restore(browser_profile_dir())
    except Exception as exc:
        log_line(f"Session restore cleanup skipped: {exc}")

    ensure_browser_running(timeout_sec)


def connect_cdp_browser(playwright, cdp_url: str, ensure_func, label: str):
    """
    Connect to the dedicated browser with bounded timeouts and a REAL restart
    when the CDP HTTP endpoint is alive but the websocket/browser context is
    stale. This fixes the case where /json/version responds on 9223 while
    Playwright connect_over_cdp hangs for minutes.
    """
    last_error = None
    # Attempt 2 follows a cheap tab cleanup, attempt 3 a full restart.
    max_attempts = 2 if os.environ.get("ARTICLE_SAFE_MODE") == "1" else 3

    for attempt in range(1, max_attempts + 1):
        try:
            ensure_func()

            if attempt == 1:
                pages = cdp_page_targets(cdp_url)
                if pages is not None and len(pages) > CDP_MAX_IDLE_TABS:
                    log_line(f"{label}: {len(pages)} tabs open before attach")
                    close_cdp_tabs(cdp_url, label)

            timeout_ms = 15000 if attempt == 1 else 20000
            log_line(
                f"{label}: CDP connect attempt {attempt}/{max_attempts} "
                f"(timeout {timeout_ms // 1000}s)"
            )

            browser = playwright.chromium.connect_over_cdp(
                cdp_url,
                timeout=timeout_ms,
            )

            if not browser.contexts:
                raise RuntimeError(f"{label} has no browser contexts")

            context = browser.contexts[0]

            # A stale CDP endpoint can answer the handshake but still have an
            # unusable context. Opening a disposable tab validates the context.
            probe = context.new_page()
            probe.close()

            if attempt > 1:
                log_line(f"{label}: CDP connection recovered successfully.")

            return browser

        except Exception as exc:
            last_error = exc
            log_line(
                f"{label}: CDP connect/validation failed "
                f"(attempt {attempt}/{max_attempts}): {type(exc).__name__}: {exc}"
            )

            if attempt >= max_attempts:
                break

            if attempt == 1:
                # Usually a leftover tab blocks the attach; closing tabs keeps
                # the browser (and its in-memory session) alive.
                close_cdp_tabs(cdp_url, label)
                continue

            # In the current single-browser configuration EDGE_CDP_URL and
            # BROWSER_CDP_URL both point to 9223. A simple ensure_* call is not
            # sufficient because browser_cdp_alive() may still return True for
            # a wedged websocket. Force-restart ONLY the dedicated profile.
            try:
                log_line(
                    f"{label}: forcing dedicated browser restart before retry."
                )
                restart_browser_running(45)
            except Exception as restart_exc:
                log_line(
                    f"{label}: restart failed: "
                    f"{type(restart_exc).__name__}: {restart_exc}"
                )

            time.sleep(2)

    raise DownloadError(
        f"{label}: CDP unusable after forced restart(s): {last_error}"
    )



# ---------------------------------------------------------------------------
# WARM-UP: open every database before the queue starts (v6.0)
# ---------------------------------------------------------------------------

# Wider than looks_like_captcha(): a false hit here only keeps the queue
# waiting, while there it would stop a download.
WARMUP_CHECK_WORDS = (
    "verify you are human",
    "verifying you are human",
    "checking your browser",
    "security verification",
    "cloudflare",
    "are you a robot",
    "just a moment",
    "один момент",
    "подтвердите, что вы не робот",
    "安全验证",
    "拖动下方拼图",
)
WARMUP_ACCESS_RE = re.compile(
    r"(?:access provided by|доступ предоставлен)\s*:?\s*([^\n]{3,80})", re.I
)
WARMUP_LOAD_LIMIT_SEC = 90


def _warmup_state(page) -> tuple[str, str]:
    try:
        url, text = page.evaluate(
            "() => [location.href, (document.title || '') + '\\n' + "
            "(document.body ? document.body.innerText.slice(0, 8000) : '')]"
        )
    except Exception as exc:
        return ("closed", "") if _target_closed_error(exc) else ("loading", "")

    low = (text or "").lower()
    if url in ("", "about:blank"):
        return "loading", ""
    # The browser's own "site can't be reached" page.
    if url.startswith("chrome-error:") or re.search(r"\bERR_[A-Z_]{4,}\b", text or ""):
        return "error", ""
    if any(word in low for word in WARMUP_CHECK_WORDS):
        return "captcha", ""
    if len(low.strip()) < 20:
        return "loading", ""
    m = WARMUP_ACCESS_RE.search(text)
    return "ok", (m.group(1).strip() if m else "")


def run_warmup(bases_file: Path) -> int:
    """
    Open one tab per database ([{"key", "url"}, ...] in bases_file) and wait
    until none of them shows a human-verification page. Progress goes to
    stdout as WARMUP|<key>|<state>|<detail>; states: loading, captcha, ok,
    error (did not load), closed (tab closed by the user). The tab with a
    CAPTCHA is brought to the front, one after another.
    """
    bases = json.loads(bases_file.read_text(encoding="utf-8"))
    wait_sec = job_bounded_wait(1800, margin_sec=5)

    with sync_playwright() as p:
        browser = connect_cdp_browser(
            p, BROWSER_CDP_URL, ensure_browser_running, "Dedicated browser"
        )
        context = browser.contexts[0]
        tabs = []

        for base in bases:
            page = context.new_page()
            safe_console_print(f"WARMUP|{base['key']}|loading|")
            try:
                page.goto(base["url"], wait_until="commit", timeout=30000)
            except Exception as exc:
                log_line(f"Warm-up {base['key']}: {type(exc).__name__}: {exc}")
            tabs.append({"key": base["key"], "page": page,
                         "state": "loading", "opened": time.time()})

        log_line(f"Warm-up: {len(tabs)} database tab(s) opened")
        deadline = time.time() + wait_sec
        front = None
        clear_rounds = 0

        while time.time() < deadline:
            first_captcha = None
            blocking = 0

            for tab in tabs:
                if tab["state"] == "closed":
                    continue
                state, detail = _warmup_state(tab["page"])
                if (state == "loading"
                        and time.time() - tab["opened"] > WARMUP_LOAD_LIMIT_SEC):
                    state = "error"
                if state != tab["state"] or (state == "ok" and detail):
                    if state != tab["state"]:
                        log_line(f"Warm-up {tab['key']}: {state} {detail}".rstrip())
                    safe_console_print(f"WARMUP|{tab['key']}|{state}|{detail}")
                    tab["state"] = state
                if state in ("loading", "captcha"):
                    blocking += 1
                if state == "captcha" and first_captcha is None:
                    first_captcha = tab["page"]

            if first_captcha is not None and first_captcha is not front:
                try:
                    first_captcha.bring_to_front()
                except Exception:
                    pass
                front = first_captcha

            # Two clean rounds: a check page reloads itself right after it
            # is passed and may briefly look finished before that.
            clear_rounds = clear_rounds + 1 if blocking == 0 else 0
            if clear_rounds >= 2:
                break
            time.sleep(2)

        cleared = clear_rounds >= 2

        # The cookies stay in the profile; the open tabs would only slow down
        # every later CDP attach. Keep one tab so the window stays open.
        try:
            context.new_page()
        except Exception:
            pass
        for tab in tabs:
            close_page_safely(tab["page"])

    if cleared:
        log_line("Warm-up: all databases are ready")
        emit("SUCCESS", "warm-up complete")
        return EXIT_SUCCESS

    left = [t["key"] for t in tabs if t["state"] in ("loading", "captcha")]
    log_line(f"Warm-up: time is up, still waiting: {', '.join(left)}")
    emit("CAPTCHA_REQUIRED", ", ".join(left))
    return EXIT_CAPTCHA


def open_in_dedicated_browser(urls: list[str]) -> int:
    """Open URLs as new tabs of the dedicated browser and focus the last one."""
    ensure_browser_running()
    base = BROWSER_CDP_URL.rstrip("/")

    for url in urls:
        target_id = None
        for method in ("PUT", "GET"):
            try:
                req = urllib.request.Request(
                    base + "/json/new?" + quote(url, safe=""), method=method
                )
                with urllib.request.urlopen(req, timeout=10) as response:
                    target_id = json.loads(response.read().decode("utf-8")).get("id")
                break
            except Exception:
                continue
        if target_id:
            try:
                with urllib.request.urlopen(base + "/json/activate/" + target_id, timeout=5):
                    pass
            except Exception:
                pass
        log_line(f"Opened in dedicated browser: {url}")

    emit("SUCCESS", "opened")
    return EXIT_SUCCESS


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def _download_routed(context, source: str, raw: str, doi: str | None, resolved: str | None) -> Path:
    """Run the platform downloader for an identified source."""
    if source == "elsevier":
        log_line("Browser route: Elsevier -> dedicated browser (port 9223)")
        return download_elsevier(context, raw, doi, resolved)

    if source == "elibrary":
        log_line("Browser route: eLIBRARY -> dedicated browser (port 9223)")
        return download_elibrary(context, raw)

    if source == "generic":
        log_line(
            "Browser route: Generic DOI publisher -> dedicated browser (port 9223)"
        )
        return download_generic(context, raw, doi, resolved)

    if source == "mdpi":
        log_line("Browser route: MDPI -> dedicated browser (port 9223)")
        return download_mdpi(context, raw, doi, resolved)

    if source == "acs":
        log_line("Browser route: ACS -> dedicated browser (port 9223)")
        return download_acs(context, raw, doi, resolved)

    if source == "nature":
        log_line("Browser route: Nature/Springer Nature -> dedicated browser (port 9223)")
        return download_nature(context, raw, doi, resolved)

    if source == "frontiers":
        log_line("Browser route: Frontiers -> dedicated browser (port 9223)")
        return download_frontiers(context, raw, doi, resolved)

    if source == "oxford":
        log_line("Browser route: Oxford Academic -> dedicated browser (port 9223)")
        return download_oxford(context, raw, doi, resolved)

    if source == "taylor":
        log_line("Browser route: Taylor & Francis -> dedicated browser (port 9223)")
        return download_taylor(context, raw, doi, resolved)

    if source == "springer":
        log_line("Browser route: Springer -> dedicated browser (port 9223)")
        return download_springer(context, raw, doi)

    if source == "wiley":
        log_line("Browser route: Wiley -> dedicated browser (port 9223)")
        return download_wiley(context, raw, doi, resolved)

    if source == "cnki":
        log_line("Browser route: CNKI -> dedicated browser (port 9223)")
        return download_cnki(context, doi, resolved or raw)

    raise UnsupportedSource(f"Unsupported source: {source}")


def _process_one_once(raw: str) -> Path:
    raw = raw.strip()

    if not raw:
        raise UnsupportedSource("Empty input")

    title_query = parse_title_query(raw)
    if title_query:
        log_line(f"Input: {raw}")
        # Without a watcher deadline a CAPTCHA on one candidate site would
        # hold the whole search for 15 minutes.
        os.environ.setdefault("ARTICLE_JOB_DEADLINE", f"{time.time() + 600:.0f}")
        with sync_playwright() as p:
            browser = connect_cdp_browser(
                p, BROWSER_CDP_URL, ensure_browser_running, "Dedicated browser"
            )
            return download_by_title(browser.contexts[0], title_query)

    # "cnki: <title> || <DOI>" searches CNKI by title, e.g. for Chinese papers
    # whose DOI is registered with Wanfang and unknown to CNKI.
    if raw.lower().startswith("cnki:"):
        parts = [x.strip() for x in raw[5:].split("||") if x.strip()]
        cnki_doi = next((extract_doi(x) for x in parts if extract_doi(x)), None)
        cnki_title = next((x for x in parts if not extract_doi(x)), None)
        log_line(f"Input: {raw}")
        with sync_playwright() as p:
            browser = connect_cdp_browser(
                p, BROWSER_CDP_URL, ensure_browser_running, "Dedicated browser"
            )
            log_line("Browser route: CNKI by title -> dedicated browser (port 9223)")
            return download_cnki(browser.contexts[0], cnki_doi, None, cnki_title)

    doi = extract_doi(raw)
    log_line(f"Input: {raw}")
    log_line(f"DOI: {doi or '-'}")

    # Fast classification first. This lets known Elsevier inputs go directly
    # to the dedicated browser without an extra resolution step.
    source = classify_direct(raw, doi)
    resolved = raw if raw.startswith("http") else None

    with sync_playwright() as p:
        edge_browser = None
        edge_context = None

        # Unknown DOI/source: use the dedicated browser for neutral resolution / CNKI probe.
        if source is None:
            edge_browser = connect_cdp_browser(
                p,
                EDGE_CDP_URL,
                ensure_edge_running,
                "Dedicated browser",
            )
            edge_context = edge_browser.contexts[0]

            source, resolved = identify_source(
                edge_context,
                raw,
                doi,
            )

        log_line(f"Source: {source}")
        if resolved:
            log_line(f"Resolved: {resolved}")

        # All routes share the dedicated browser on port 9223; a
        # second attach only doubles the work for every open tab.
        if edge_context is None:
            edge_browser = connect_cdp_browser(
                p,
                EDGE_CDP_URL,
                ensure_edge_running,
                "Dedicated browser",
            )
            edge_context = edge_browser.contexts[0]

        return _download_routed(edge_context, source, raw, doi, resolved)


def process_one(raw: str) -> Path:
    """Run one job with self-healing retries for browser/network failures."""
    last_exc = None

    max_attempts = 1 if os.environ.get("ARTICLE_SAFE_MODE") == "1" else 3
    for attempt in range(1, max_attempts + 1):
        try:
            return _process_one_once(raw)
        except (CaptchaRequired, NoAccess) as exc:
            raise
        except Exception as exc:
            last_exc = exc
            msg = type(exc).__name__ + ": " + str(exc)

            if _target_closed_error(exc) and attempt < max_attempts:
                log_line(
                    f"Target/browser closed; self-heal retry {attempt + 1}/3: {msg}"
                )
                try:
                    restart_browser_running()
                except Exception as restart_exc:
                    log_line(
                        f"Browser restart message: {type(restart_exc).__name__}: {restart_exc}"
                    )
                time.sleep(2)
                continue

            if _transient_navigation_error(exc) and attempt < max_attempts:
                delay = 3 * attempt
                log_line(
                    f"Transient job failure; retry {attempt + 1}/3 after {delay}s: {msg}"
                )
                time.sleep(delay)
                continue

            # One extra resolution attempt for a DOI that temporarily looked unsupported.
            if (isinstance(exc, UnsupportedSource) and extract_doi(raw)
                    and attempt < min(2, max_attempts)):
                log_line(
                    f"DOI source unresolved; retrying full resolution once: {msg}"
                )
                time.sleep(3)
                continue

            raise

    if last_exc:
        raise last_exc
    raise DownloadError("Unknown retry failure")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "query",
        nargs="?",
        help='DOI, article URL or "title: <title> || <year> || <journal> || #<no>"',
    )
    parser.add_argument(
        "--warmup",
        metavar="BASES_JSON",
        help="open the databases listed in this JSON file and wait for CAPTCHAs",
    )
    parser.add_argument(
        "--open",
        metavar="URL",
        nargs="+",
        help="open URLs in the dedicated browser",
    )
    parser.add_argument(
        "--restart-browser",
        action="store_true",
        help="restart the dedicated browser",
    )
    args = parser.parse_args()

    try:
        if args.warmup:
            return run_warmup(Path(args.warmup))
        if args.open:
            return open_in_dedicated_browser(args.open)
        if args.restart_browser:
            stop_dedicated_browser()
            time.sleep(2)
            ensure_browser_running()
            emit("SUCCESS", "browser restarted")
            return EXIT_SUCCESS
        if not args.query:
            parser.error("query is required")

        path = process_one(args.query)
        log_line(f"SUCCESS: {path}")
        emit("SUCCESS", str(path))
        return EXIT_SUCCESS

    except CaptchaRequired as exc:
        log_line(f"CAPTCHA_REQUIRED: {exc}")
        emit("CAPTCHA_REQUIRED", str(exc))
        return EXIT_CAPTCHA

    except NoAccess as exc:
        log_line(f"NO_ACCESS: {exc}")
        emit("NO_ACCESS", str(exc))
        return EXIT_NO_ACCESS

    except UnsupportedSource as exc:
        log_line(f"UNSUPPORTED: {exc}")
        emit("UNSUPPORTED", str(exc))
        return EXIT_UNSUPPORTED

    except Exception as exc:
        log_line(f"FAILED: {type(exc).__name__}: {exc}")
        emit("FAILED", f"{type(exc).__name__}: {exc}")
        return EXIT_FAILURE


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--internal-pdf-worker":
        sys.exit(internal_pdf_worker_main(sys.argv[2], Path(sys.argv[3])))
    sys.exit(main())
