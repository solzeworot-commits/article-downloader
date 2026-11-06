# -*- coding: utf-8 -*-
# © 2026 Bucaneiro. Бесплатная программа; условия — в файле ЛИЦЕНЗИЯ.txt.
"""
DOI search for the "Получить список DOI" window (v6.1).

Builds Scopus / Web of Science / OpenAlex queries from keywords, runs them
through the official APIs (Scopus Search API, Web of Science Starter API,
OpenAlex), merges the hits by DOI and saves a list into inbox\\. Exports the
user makes on the Scopus / WoS sites (CSV, RIS, plain text, tab-delimited)
are read into the same records.

API keys live in %LOCALAPPDATA%\\ArticleDownloader\\keys.json, outside the
portable folder that people copy around, and never go into logs or to the
window in full. For tests ARTICLE_KEYS_FILE, ARTICLE_SCOPUS_URL,
ARTICLE_WOS_URL, ARTICLE_OPENALEX_URL and ARTICLE_WOS_DELAY override the
defaults.

The request and response shapes follow the public documentation as far as
it could be checked while writing this; HANDOFF.md lists what still has to
be confirmed against the real services.
"""

from __future__ import annotations

import csv
import html
import io
import json
import os
import re
import time
import urllib.error
import urllib.request
import zipfile
from collections import Counter
from datetime import date
from pathlib import Path
from urllib.parse import quote

from i18n import tr

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent

SCOPUS_URL = "https://api.elsevier.com/content/search/scopus"
WOS_URL = "https://api.clarivate.com/apis/wos-starter/v1/documents"
OPENALEX_URL = "https://api.openalex.org/works"

KEY_FIELDS = ("scopus", "scopus_insttoken", "wos", "openalex",
              # AI helper (ai_helper.KEY_FIELDS)
              "perplexity", "openai", "anthropic", "gigachat", "yandex", "yandex_folder")
SOURCE_TITLES = {"scopus": "Scopus", "wos": "Web of Science", "openalex": "OpenAlex"}
# Services the review module also asks (names for error messages only).
SERVICE_TITLES = {**SOURCE_TITLES, "crossref": "Crossref", "europepmc": "Europe PMC"}
MAX_RESULTS_CAP = 2000

DOI_RE = re.compile(r"10\.\d{4,9}/[^\s\"<>{}|\\^`;,]+", re.I)


class SearchError(Exception):
    """kind: no_key, bad_key, no_access, quota, pending, bad_query, bad_file,
    network, other. The message is for the user, in Russian."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind
        self.message = message


# ---------------------------------------------------------------------------
# keys
# ---------------------------------------------------------------------------

def keys_path() -> Path:
    override = os.environ.get("ARTICLE_KEYS_FILE")
    if override:
        return Path(override)
    local = os.environ.get("LOCALAPPDATA")
    base = Path(local) if local else Path.home() / ".config"
    return base / "ArticleDownloader" / "keys.json"


def load_keys() -> dict:
    try:
        data = json.loads(keys_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    keys = {k: str(data.get(k) or "").strip() for k in KEY_FIELDS}
    if not keys["openalex"]:
        # The downloader's own places for the OpenAlex key (v5.24).
        keys["openalex"] = os.environ.get("OPENALEX_API_KEY", "").strip()
        if not keys["openalex"]:
            try:
                keys["openalex"] = (ROOT / "openalex_key.txt").read_text(encoding="utf-8").strip()
            except OSError:
                pass
    return keys


def save_keys(updates: dict) -> dict:
    """Set the given fields; None or "" removes a key."""
    path = keys_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    for field, value in updates.items():
        if field not in KEY_FIELDS:
            continue
        value = str(value or "").strip()
        if value:
            data[field] = value
        else:
            data.pop(field, None)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    os.replace(tmp, path)
    return load_keys()


def mask(value: str) -> str:
    if not value:
        return ""
    return "••••" + value[-4:] if len(value) >= 8 else "••••"


def keys_status(keys: dict) -> dict:
    return {field: mask(keys.get(field, "")) for field in KEY_FIELDS}


# ---------------------------------------------------------------------------
# queries
# ---------------------------------------------------------------------------

def _groups(keywords) -> list[list[str]]:
    """One concept per line; synonyms on the same line are separated by ";" (v7.6)."""
    if isinstance(keywords, str):
        keywords = keywords.splitlines()
    groups, seen = [], set()
    for raw in keywords:
        group = []
        for part in str(raw).split(";"):
            term = re.sub(r"\s+", " ", part.replace('"', " ")).strip()
            if term and term.lower() not in seen:
                seen.add(term.lower())
                group.append(term)
        if group:
            groups.append(group)
    return groups


def _terms(keywords) -> list[str]:
    return [term for group in _groups(keywords) for term in group]


def _quoted(term: str) -> str:
    return f'"{term}"' if re.search(r"[\s\-/]", term) else term


def _year(value) -> int | None:
    try:
        year = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return year if 1800 <= year <= 2100 else None


def build_queries(params: dict) -> dict:
    """
    params: keywords (lines), match "all"|"any", field "tak"|"title",
    year_from, year_to, doc_type "both"|"article"|"review"|"all".
    Returns {"scopus": str, "wos": str, "openalex": {"search", "filter"}}.
    """
    groups = _groups(params.get("keywords") or "")
    terms = [term for group in groups for term in group]
    if not terms:
        raise SearchError("bad_query", tr("Введите хотя бы одно ключевое слово."))
    if params.get("match") == "any":
        inner = " OR ".join(_quoted(t) for t in terms)
    else:
        # Synonyms of a line go together with OR, the lines with AND.
        inner = " AND ".join(_quoted(g[0]) if len(g) == 1 else "(" + " OR ".join(_quoted(t) for t in g) + ")"
                             for g in groups)
        if len(groups) == 1 and len(groups[0]) > 1:
            inner = inner[1:-1]
    title_only = params.get("field") == "title"
    year_from, year_to = _year(params.get("year_from")), _year(params.get("year_to"))
    if year_from and year_to and year_from > year_to:
        year_from, year_to = year_to, year_from
    doc_type = params.get("doc_type") or "both"

    scopus = f"{'TITLE' if title_only else 'TITLE-ABS-KEY'}({inner})"
    if year_from:
        scopus += f" AND PUBYEAR > {year_from - 1}"
    if year_to:
        scopus += f" AND PUBYEAR < {year_to + 1}"
    scopus += {
        "article": " AND DOCTYPE(ar)",
        "review": " AND DOCTYPE(re)",
        "both": " AND (DOCTYPE(ar) OR DOCTYPE(re))",
    }.get(doc_type, "")

    wos = f"{'TI' if title_only else 'TS'}=({inner})"
    if year_from or year_to:
        wos += f" AND PY=({year_from or 1900}-{year_to or date.today().year})"
    wos += {
        "article": " AND DT=(Article)",
        "review": " AND DT=(Review)",
        "both": " AND DT=(Article OR Review)",
    }.get(doc_type, "")

    filters = []
    if title_only:
        if params.get("match") == "any" or all(len(g) == 1 for g in groups):
            sep = "|" if params.get("match") == "any" else " "
            filters.append("title.search:" + sep.join(t.replace(",", " ") for t in terms))
        else:
            filters.append("title.search:" + inner.replace(",", " "))
    if year_from:
        filters.append(f"from_publication_date:{year_from}-01-01")
    if year_to:
        filters.append(f"to_publication_date:{year_to}-12-31")
    oa_type = {"article": "article", "review": "review", "both": "article|review"}.get(doc_type)
    if oa_type:
        filters.append("type:" + oa_type)
    openalex = {"search": "" if title_only else inner, "filter": ",".join(filters)}

    return {"scopus": scopus, "wos": wos, "openalex": openalex}


def query_slug(params: dict) -> str:
    words = " ".join(_terms(params.get("keywords") or ""))
    slug = re.sub(r"[^\w\-]+", "_", words).strip("_")
    return slug[:40].rstrip("_") or tr("список")


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def _quota_left(headers) -> str:
    for name, value in (headers or {}).items():
        if "remaining" in name.lower():
            return str(value)
    return ""


def _get_json(source: str, url: str, headers: dict, timeout: int = 40):
    req = urllib.request.Request(url, headers={
        "Accept": "application/json",
        "User-Agent": "ArticleDownloader/7.1 (+https://github.com/solzeworot-commits/article-downloader)",
        **headers,
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8")), dict(response.headers)
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read(4000).decode("utf-8", "replace")
        except Exception:
            body = ""
        raise _http_error(source, exc.code, body) from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise SearchError(
            "network",
            tr("Нет связи с {service} ({reason}). Проверьте интернет.", service=SERVICE_TITLES[source], reason=reason),
        ) from None
    except ValueError:
        raise SearchError("other", tr("{service} прислал непонятный ответ.", service=SERVICE_TITLES[source])) from None


def _http_error(source: str, code: int, body: str) -> SearchError:
    low = body.lower()
    name = SERVICE_TITLES[source]
    if code == 429 or "quota" in low or "too many" in low:
        if source == "openalex":
            # Without a key OpenAlex shares a small daily budget per IP address.
            return SearchError("quota", tr("Исчерпан дневной лимит OpenAlex для этого компьютера. "
                                           "Возьмите бесплатный ключ OpenAlex (окно «🔑 Ключи API» → "
                                           "вкладка OpenAlex) или попробуйте завтра."))
        hint = tr(" (бесплатный план: 50 запросов в день)") if source == "wos" else ""
        return SearchError("quota", tr("Исчерпан лимит запросов {service}{hint}. Попробуйте позже.",
                                       service=name, hint=hint))
    if code == 400:
        detail = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body))[:200]
        return SearchError("bad_query", tr("{service} не понял запрос. {detail}", service=name, detail=detail).strip())
    if source == "scopus" and code in (401, 403):
        if "invalid" in low and "key" in low:
            return SearchError("bad_key", tr("Ключ Scopus неверный — скопируйте его ещё раз, целиком."))
        return SearchError(
            "no_access",
            tr("Ключ Scopus принят, но Scopus не видит доступа университета с этого компьютера. "
               "Подключитесь к сети или VPN университета либо попросите insttoken (письмо — в "
               "пошаговой инструкции). Можно искать через браузер."),
        )
    if source == "wos" and code == 401:
        return SearchError("bad_key", tr("Ключ Web of Science неверный — скопируйте его ещё раз, целиком."))
    if source == "wos" and code == 403:
        return SearchError(
            "pending",
            tr("Ключ не открывает Web of Science Starter API: подписка на него ещё не одобрена "
               "или не оформлена (шаг W4 инструкции)."),
        )
    if code >= 500:
        return SearchError("other", tr("{service} временно не отвечает (код {code}). Попробуйте позже.", service=name, code=code))
    return SearchError("other", tr("{service} ответил ошибкой {code}.", service=name, code=code))


def normalize_doi(value) -> str:
    if not value:
        return ""
    value = str(value).strip()
    value = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", value, flags=re.I)
    value = value.strip().rstrip(".,;").lower()
    return value if value.startswith("10.") and "/" in value else ""


def _record(doi, title, year, journal, source) -> dict:
    return {
        "doi": doi,
        "title": re.sub(r"\s+", " ", html.unescape(str(title or ""))).strip(),
        "year": str(year or "")[:4],
        "journal": re.sub(r"\s+", " ", str(journal or "")).strip(),
        "sources": [source],
    }


# ---------------------------------------------------------------------------
# the three APIs
# ---------------------------------------------------------------------------

def _scopus_headers(keys: dict) -> dict:
    if not keys.get("scopus"):
        raise SearchError("no_key", tr("Нет ключа Scopus — введите его в окне «🔑 Ключи API»."))
    headers = {"X-ELS-APIKey": keys["scopus"]}
    if keys.get("scopus_insttoken"):
        headers["X-ELS-Insttoken"] = keys["scopus_insttoken"]
    return headers


def search_scopus(query: str, keys: dict, max_results: int, progress=None) -> dict:
    headers = _scopus_headers(keys)
    base = os.environ.get("ARTICLE_SCOPUS_URL") or SCOPUS_URL
    records, total, no_doi, quota, cursor, seen = [], 0, 0, "", "*", 0
    while seen < max_results:
        count = min(25, max_results - seen)
        url = (f"{base}?query={quote(query)}&count={count}&cursor={quote(cursor)}"
               "&field=dc:title,prism:doi,prism:coverDate,prism:publicationName")
        data, response_headers = _get_json("scopus", url, headers)
        quota = _quota_left(response_headers) or quota
        result = data.get("search-results") or {}
        total = int(result.get("opensearch:totalResults") or 0)
        entries = [e for e in (result.get("entry") or []) if not e.get("error")]
        for entry in entries:
            doi = normalize_doi(entry.get("prism:doi"))
            if not doi:
                no_doi += 1
                continue
            records.append(_record(doi, entry.get("dc:title"),
                                   (entry.get("prism:coverDate") or "")[:4],
                                   entry.get("prism:publicationName"), "scopus"))
        seen += len(entries)
        if progress:
            progress(seen, min(total, max_results))
        next_cursor = (result.get("cursor") or {}).get("@next")
        if not entries or not next_cursor or next_cursor == cursor or seen >= total:
            break
        cursor = next_cursor
    return {"records": records, "total": total, "no_doi": no_doi, "quota": quota}


def search_wos(query: str, keys: dict, max_results: int, progress=None) -> dict:
    if not keys.get("wos"):
        raise SearchError("no_key", tr("Нет ключа Web of Science — введите его в окне «🔑 Ключи API»."))
    headers = {"X-ApiKey": keys["wos"]}
    base = os.environ.get("ARTICLE_WOS_URL") or WOS_URL
    # The free plan allows one request per second.
    delay = float(os.environ.get("ARTICLE_WOS_DELAY") or 1.1)
    records, total, no_doi, quota, page, seen = [], 0, 0, "", 1, 0
    limit = min(50, max_results)
    while seen < max_results:
        url = f"{base}?db=WOS&q={quote(query)}&limit={limit}&page={page}"
        data, response_headers = _get_json("wos", url, headers)
        quota = _quota_left(response_headers) or quota
        total = int((data.get("metadata") or {}).get("total") or 0)
        hits = data.get("hits") or []
        for hit in hits[: max_results - seen]:
            doi = normalize_doi((hit.get("identifiers") or {}).get("doi"))
            if not doi:
                no_doi += 1
                continue
            source = hit.get("source") or {}
            records.append(_record(doi, hit.get("title"), source.get("publishYear"),
                                   source.get("sourceTitle"), "wos"))
        seen += len(hits)
        if progress:
            progress(min(seen, max_results), min(total, max_results))
        if not hits or seen >= total:
            break
        page += 1
        time.sleep(delay)
    return {"records": records, "total": total, "no_doi": no_doi, "quota": quota}


def search_openalex(params: dict, keys: dict, max_results: int, progress=None) -> dict:
    base = os.environ.get("ARTICLE_OPENALEX_URL") or OPENALEX_URL
    records, total, no_doi, cursor, seen = [], 0, 0, "*", 0
    per_page = min(200, max_results)
    while seen < max_results:
        url = (f"{base}?per-page={per_page}&cursor={quote(cursor)}"
               "&select=doi,display_name,publication_year,primary_location")
        if params.get("search"):
            url += "&search=" + quote(params["search"])
        if params.get("filter"):
            url += "&filter=" + quote(params["filter"], safe=":,|-")
        if keys.get("openalex"):
            url += "&api_key=" + quote(keys["openalex"])
        data, _ = _get_json("openalex", url, {})
        meta = data.get("meta") or {}
        total = int(meta.get("count") or 0)
        results = data.get("results") or []
        for work in results[: max_results - seen]:
            doi = normalize_doi(work.get("doi"))
            if not doi:
                no_doi += 1
                continue
            journal = ((work.get("primary_location") or {}).get("source") or {}).get("display_name")
            records.append(_record(doi, work.get("display_name"), work.get("publication_year"),
                                   journal, "openalex"))
        seen += len(results)
        if progress:
            progress(min(seen, max_results), min(total, max_results))
        cursor = meta.get("next_cursor")
        if not results or not cursor or seen >= total:
            break
    return {"records": records, "total": total, "no_doi": no_doi, "quota": ""}


def check_key(source: str, keys: dict) -> dict:
    """One tiny request; the answer for the "Проверить ключ" button."""
    try:
        if source == "scopus":
            result = search_scopus("TITLE(chromatography)", keys, 1)
        elif source == "wos":
            result = search_wos("TS=(chromatography)", keys, 1)
        elif source == "openalex":
            result = search_openalex({"search": "chromatography"}, keys, 1)
        else:
            return {"ok": False, "kind": "other", "message": tr("Неизвестный источник.")}
    except SearchError as exc:
        return {"ok": False, "kind": exc.kind, "message": exc.message}
    if result["quota"]:
        message = tr("Ключ работает ✓ ({service} отвечает, осталось запросов: {quota})",
                     service=SOURCE_TITLES[source], quota=result["quota"])
    else:
        message = tr("Ключ работает ✓ ({service} отвечает)", service=SOURCE_TITLES[source])
    return {"ok": True, "kind": "ok", "message": message}


def suggest_keywords(keywords, keys: dict | None = None, papers: int = 50, limit: int = 30) -> dict:
    """
    Free keyword hints: the keywords and topics OpenAlex assigned to the most
    relevant papers for the words typed so far, most frequent first, without
    the words already there. {"terms": [{"term", "count", "kind"}], "papers", "total"}.
    """
    terms = _terms(keywords)
    if not terms:
        raise SearchError("bad_query", tr("Введите хотя бы одно слово — подсказки строятся по нему."))
    base = os.environ.get("ARTICLE_OPENALEX_URL") or OPENALEX_URL
    url = f"{base}?per-page={papers}&search={quote(' '.join(terms))}&select=keywords,topics"
    key = (keys or load_keys()).get("openalex")
    if key:
        url += "&api_key=" + quote(key)
    data, _ = _get_json("openalex", url, {})
    works = data.get("results") or []

    have = {t.lower() for t in terms}
    counts, shown, kinds = Counter(), {}, {}
    for work in works:
        seen = set()
        for kind, items in (("keyword", work.get("keywords")), ("topic", work.get("topics"))):
            for item in items or []:
                name = re.sub(r"\s+", " ", str(item.get("display_name") or "")).strip()
                low = name.lower()
                if not name or low in have or low in seen:
                    continue
                seen.add(low)
                counts[low] += 1
                shown.setdefault(low, name)
                kinds.setdefault(low, kind)

    ranked = [k for k, n in counts.most_common() if n >= 2]
    if len(ranked) < 10:
        ranked += [k for k, n in counts.most_common() if n < 2][: 10 - len(ranked)]
    return {
        "terms": [{"term": shown[k], "count": counts[k], "kind": kinds[k]} for k in ranked[:limit]],
        "papers": len(works),
        "total": int((data.get("meta") or {}).get("count") or 0),
    }


def merge(record_lists: list[list[dict]]) -> list[dict]:
    """One record per DOI (case-insensitive); sources are combined."""
    merged: dict[str, dict] = {}
    for records in record_lists:
        for rec in records:
            have = merged.get(rec["doi"])
            if have is None:
                merged[rec["doi"]] = {**rec, "sources": list(rec["sources"])}
                continue
            for field in ("title", "year", "journal"):
                if not have[field] and rec[field]:
                    have[field] = rec[field]
            for src in rec["sources"]:
                if src not in have["sources"]:
                    have["sources"].append(src)
    return sorted(merged.values(), key=lambda r: (-(int(r["year"]) if r["year"].isdigit() else 0),
                                                  r["title"].lower()))


def run_search(params: dict, sources: list[str], keys: dict, progress=None) -> dict:
    """
    Search every chosen source; a failing source does not stop the others.
    Returns {"queries", "per_source": {src: {...}}, "records", "no_doi"}.
    """
    queries = build_queries(params)
    if params.get("scopus_query"):
        queries["scopus"] = params["scopus_query"].strip()
    if params.get("wos_query"):
        queries["wos"] = params["wos_query"].strip()
    try:
        max_results = int(params.get("max_results") or 200)
    except ValueError:
        max_results = 200
    max_results = max(1, min(MAX_RESULTS_CAP, max_results))

    per_source, lists, no_doi = {}, [], 0
    for source in sources:
        def report(done, of, source=source):
            if progress:
                progress(source, done, of)
        try:
            if source == "scopus":
                result = search_scopus(queries["scopus"], keys, max_results, report)
            elif source == "wos":
                result = search_wos(queries["wos"], keys, max_results, report)
            elif source == "openalex":
                result = search_openalex(queries["openalex"], keys, max_results, report)
            else:
                continue
        except SearchError as exc:
            per_source[source] = {"ok": False, "kind": exc.kind, "message": exc.message}
            continue
        lists.append(result["records"])
        no_doi += result["no_doi"]
        per_source[source] = {"ok": True, "total": result["total"],
                              "taken": len(result["records"]) + result["no_doi"],
                              "with_doi": len(result["records"]),
                              "no_doi": result["no_doi"], "quota": result["quota"]}
    return {"queries": queries, "per_source": per_source, "records": merge(lists),
            "no_doi": no_doi}


# ---------------------------------------------------------------------------
# exports made on the Scopus / WoS sites
# ---------------------------------------------------------------------------

def _decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-16", "cp1251"):
        try:
            text = data.decode(encoding)
            if encoding != "utf-16" or text.count("\x00") == 0:
                return text
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


def _tagged_records(lines: list[str], ris: bool) -> list[dict]:
    """WoS plain text ("TI title", continuation lines indented) and RIS ("TI  - title")."""
    out, cur, last = [], {}, None
    for line in lines:
        if ris:
            m = re.match(r"^([A-Z][A-Z0-9])  -\s?(.*)$", line)
        else:
            m = re.match(r"^([A-Z][A-Z0-9]) (.*)$", line) or re.match(r"^(ER|EF)\s*$", line)
        if m:
            tag = m.group(1)
            value = m.group(2).strip() if m.lastindex and m.lastindex >= 2 else ""
            if tag == "ER":
                out.append(cur)
                cur, last = {}, None
                continue
            if tag == "EF":  # end of a WoS file
                continue
            if tag not in cur:
                cur[tag] = value
            last = tag
        elif last and line.startswith("   ") and not ris:
            cur[last] += " " + line.strip()
    if cur:
        out.append(cur)
    return out


def parse_export(name: str, data: bytes) -> tuple[list[dict], int]:
    """Records (with source "export") and the number of entries without a DOI."""
    if data[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        raise SearchError(
            "bad_file",
            tr("Это файл старого формата Excel (.xls). Выгрузите из Web of Science в формате "
               "«Plain text file» или «Tab delimited file», а из Scopus — CSV или RIS."),
        )
    if data[:2] == b"PK":
        texts = []
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for item in z.namelist():
                if item.endswith(".xml"):
                    texts.append(re.sub(r"<[^>]+>", " ", z.read(item).decode("utf-8", "replace")))
        text = html.unescape("\n".join(texts))
    else:
        text = _decode(data)

    lines = text.splitlines()
    records, no_doi = [], 0

    def add(doi_value, title, year, journal):
        nonlocal no_doi
        doi = normalize_doi(doi_value)
        if doi:
            records.append(_record(doi, title, year, journal, "export"))
        else:
            no_doi += 1

    head = "\n".join(lines[:5])
    if re.search(r"^(FN |PT [A-Z])", head, re.M):
        for rec in _tagged_records(lines, ris=False):
            if rec:
                add(rec.get("DI"), rec.get("TI"), rec.get("PY"), rec.get("SO"))
    elif re.search(r"^[A-Z][A-Z0-9]  - ", head, re.M):
        for rec in _tagged_records(lines, ris=True):
            if rec:
                add(rec.get("DO"), rec.get("TI") or rec.get("T1"),
                    (rec.get("PY") or rec.get("Y1") or rec.get("DA") or "")[:4],
                    rec.get("JO") or rec.get("JF") or rec.get("T2") or rec.get("JA"))
    elif lines and re.search(r"(^|[\t,;\"])(DOI|DI)([\t,;\"]|$)", lines[0]):
        delimiter = max(("\t", ",", ";"), key=lines[0].count)
        reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
        cols = {c.strip().strip("﻿").lower(): c for c in (reader.fieldnames or [])}

        def col(row, *names):
            for n in names:
                if n in cols and row.get(cols[n]):
                    return row[cols[n]]
            return ""

        for row in reader:
            add(col(row, "doi", "di"), col(row, "title", "ti", "article title"),
                col(row, "year", "py", "publication year"),
                col(row, "source title", "so", "source"))
    else:
        for doi in dict.fromkeys(d.lower() for d in DOI_RE.findall(text)):
            add(doi, "", "", "")

    return merge([records]), no_doi


# ---------------------------------------------------------------------------
# saving the list
# ---------------------------------------------------------------------------

def save_list(inbox: Path, slug: str, records: list[dict], dated: bool = True) -> Path:
    """inbox\\<date>_<slug>.txt with the DOIs and inbox\\_подробно\\<same>.csv."""
    inbox.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^\w\-]+", "_", slug).strip("_")[:60 if not dated else 40] or tr("список")
    stem = f"{date.today().isoformat()}_{slug}" if dated else slug
    path, n = inbox / f"{stem}.txt", 2
    while path.exists():
        path, n = inbox / f"{stem}_{n}.txt", n + 1
    path.write_text("".join(r["doi"] + "\n" for r in records), encoding="utf-8")

    detail = inbox / "_подробно" / (path.stem + ".csv")
    detail.parent.mkdir(parents=True, exist_ok=True)
    with detail.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(["DOI"] + [tr(x) for x in ("Название", "Год", "Журнал", "Где найдено")])
        for r in records:
            writer.writerow([r["doi"], r["title"], r["year"], r["journal"],
                             ", ".join(SOURCE_TITLES.get(s, tr("экспорт")) for s in r["sources"])])
    return path
