# -*- coding: utf-8 -*-
"""
Literature review helper, stage 1 (v7.0): screening by abstracts, the
reference list by GOST R 7.0.100-2018, RIS / BibTeX export, PRISMA numbers.

Metadata is free: OpenAlex gives the abstract, year, journal and pages,
Crossref the authors split into family and given names, journal, volume,
issue and pages; both are asked by DOI in batches. Elsevier keeps its
abstracts out of OpenAlex and Crossref, so a missing abstract is also looked
for in Europe PMC (articles indexed in PubMed, Food Chemistry among them). A review is kept per list
file in inbox\\_отбор\\<list>.json, the decisions are "include", "maybe" and
"exclude". ARTICLE_OPENALEX_URL / ARTICLE_CROSSREF_URL point the lookups at
test servers.
"""

from __future__ import annotations

import html
import json
import os
import re
from collections import Counter
from pathlib import Path
from urllib.parse import quote

import doi_search as ds

CROSSREF_URL = "https://api.crossref.org/works"
EUROPEPMC_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
DECISIONS = ("include", "maybe", "exclude")
CYRILLIC = re.compile(r"[А-Яа-яЁё]")
# Publishers whose single "page" is an article number (MDPI, Frontiers, PLOS).
ARTICLE_NUMBER_PREFIXES = ("10.3390/", "10.3389/", "10.1371/")
PARTICLES = {"van", "von", "de", "da", "del", "der", "den", "di", "du", "la", "le", "dos", "das", "ten", "ter"}


# ---------------------------------------------------------------------------
# review files
# ---------------------------------------------------------------------------

def review_path(inbox: Path, list_name: str) -> Path:
    return inbox / "_отбор" / (Path(list_name).stem + ".json")


def meta_path(inbox: Path, list_name: str) -> Path:
    return inbox / "_подробно" / (Path(list_name).stem + ".meta.json")


def load_meta(inbox: Path, list_name: str) -> dict:
    try:
        return json.loads(meta_path(inbox, list_name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_meta(inbox: Path, list_name: str, meta: dict):
    path = meta_path(inbox, list_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")


def load_review(inbox: Path, list_name: str, dois: list[str]) -> dict:
    """The saved review of a list, with a blank record for every new DOI."""
    try:
        data = json.loads(review_path(inbox, list_name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    records = data.setdefault("records", {})
    for doi in dois:
        records.setdefault(doi, {"doi": doi, "decision": "", "reason": "", "found": None})
    data["list"] = list_name
    data["order"] = list(dois)
    return data


def save_review(inbox: Path, data: dict):
    path = review_path(inbox, data["list"])
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# metadata by DOI
# ---------------------------------------------------------------------------

def _chunks(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _clean(text) -> str:
    text = re.sub(r"<[^>]+>", " ", str(text or ""))
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def split_name(display: str) -> dict:
    """"John A. Smith", "Smith, John A.", "Ludwig van Beethoven" -> family / given."""
    display = re.sub(r"\s+", " ", str(display or "")).strip()
    if not display:
        return {"family": "", "given": ""}
    if "," in display:
        family, given = display.split(",", 1)
        return {"family": family.strip(), "given": given.strip()}
    parts = display.split(" ")
    if len(parts) == 1:
        return {"family": parts[0], "given": ""}
    i = len(parts) - 1
    while i > 1 and parts[i - 1].lower() in PARTICLES:
        i -= 1
    return {"family": " ".join(parts[i:]), "given": " ".join(parts[:i])}


def _openalex_abstract(inverted: dict | None) -> str:
    if not inverted:
        return ""
    words = [(pos, word) for word, positions in inverted.items() for pos in positions]
    return " ".join(word for _, word in sorted(words))


def fetch_openalex(dois: list[str], keys: dict | None = None) -> dict:
    base = os.environ.get("ARTICLE_OPENALEX_URL") or ds.OPENALEX_URL
    key = (keys or {}).get("openalex")
    out = {}
    for chunk in _chunks([d for d in dois if "," not in d and "|" not in d], 50):
        url = (f"{base}?per-page=50&filter=doi:{quote('|'.join(chunk), safe='|/:')}"
               "&select=doi,display_name,publication_year,authorships,biblio,"
               "primary_location,abstract_inverted_index,language,type")
        if key:
            url += "&api_key=" + quote(key)
        data, _ = ds._get_json("openalex", url, {})
        for work in data.get("results") or []:
            doi = ds.normalize_doi(work.get("doi"))
            if not doi:
                continue
            biblio = work.get("biblio") or {}
            source = (work.get("primary_location") or {}).get("source") or {}
            first, last = biblio.get("first_page") or "", biblio.get("last_page") or ""
            out[doi] = {
                "title": _clean(work.get("display_name")),
                "year": str(work.get("publication_year") or ""),
                "journal": _clean(source.get("display_name")),
                "volume": str(biblio.get("volume") or ""),
                "issue": str(biblio.get("issue") or ""),
                "pages": f"{first}-{last}" if first and last and first != last else str(first or ""),
                "abstract": _clean(_openalex_abstract(work.get("abstract_inverted_index"))),
                "language": work.get("language") or "",
                "authors": [split_name((a.get("author") or {}).get("display_name") or a.get("raw_author_name"))
                            for a in work.get("authorships") or []],
            }
    return out


def fetch_crossref(dois: list[str]) -> dict:
    base = os.environ.get("ARTICLE_CROSSREF_URL") or CROSSREF_URL
    out = {}
    for chunk in _chunks([d for d in dois if "," not in d], 20):
        url = (f"{base}?rows={len(chunk)}&filter="
               + quote(",".join("doi:" + d for d in chunk), safe=":,/")
               + "&select=DOI,author,title,container-title,volume,issue,page,issued,abstract")
        data, _ = ds._get_json("crossref", url, {})
        for item in (data.get("message") or {}).get("items") or []:
            doi = ds.normalize_doi(item.get("DOI"))
            if not doi:
                continue
            parts = ((item.get("issued") or {}).get("date-parts") or [[None]])[0] or [None]
            out[doi] = {
                "title": _clean((item.get("title") or [""])[0]),
                "year": str(parts[0] or ""),
                "journal": _clean((item.get("container-title") or [""])[0]),
                "volume": str(item.get("volume") or ""),
                "issue": str(item.get("issue") or ""),
                "pages": str(item.get("page") or ""),
                "abstract": _clean(item.get("abstract")),
                "authors": [{"family": a.get("family") or a.get("name") or "", "given": a.get("given") or ""}
                            for a in item.get("author") or [] if a.get("family") or a.get("name")],
            }
    return out


def fetch_europepmc_abstracts(dois: list[str]) -> dict:
    """Abstracts from Europe PMC (PubMed-indexed articles), by DOI in batches."""
    base = os.environ.get("ARTICLE_EUROPEPMC_URL") or EUROPEPMC_URL
    out = {}
    for chunk in _chunks([d for d in dois if '"' not in d], 20):
        query = " OR ".join(f'DOI:"{d}"' for d in chunk)
        url = f"{base}?query={quote(query)}&resultType=core&format=json&pageSize=50"
        data, _ = ds._get_json("europepmc", url, {})
        for item in (data.get("resultList") or {}).get("result") or []:
            doi = ds.normalize_doi(item.get("doi"))
            text = _clean(item.get("abstractText"))
            if doi and text:
                out[doi] = text
    return out


def merge_meta(openalex: dict | None, crossref: dict | None) -> dict:
    """Crossref for the bibliographic fields, OpenAlex for the abstract."""
    oa, cr = openalex or {}, crossref or {}
    pick = lambda field: cr.get(field) or oa.get(field) or ""  # noqa: E731
    return {
        "title": pick("title"), "year": pick("year"), "journal": pick("journal"),
        "volume": pick("volume"), "issue": pick("issue"), "pages": pick("pages"),
        "abstract": oa.get("abstract") or cr.get("abstract") or "",
        "language": oa.get("language") or cr.get("language") or "",
        "authors": cr.get("authors") or oa.get("authors") or [],
        "found": bool(oa or cr),
    }


def enrich(dois: list[str], keys: dict | None = None) -> tuple[dict, str]:
    """Metadata for the DOIs; a failing service leaves its fields empty."""
    errors = []
    try:
        oa = fetch_openalex(dois, keys)
    except ds.SearchError as exc:
        oa, errors = {}, errors + [exc.message]
    try:
        cr = fetch_crossref(dois)
    except ds.SearchError as exc:
        cr, errors = {}, errors + [exc.message]
    merged = {d: merge_meta(oa.get(d), cr.get(d)) for d in dois}
    missing = [d for d, m in merged.items() if not m["abstract"]]
    if missing:
        try:
            for doi, text in fetch_europepmc_abstracts(missing).items():
                if doi in merged:
                    merged[doi]["abstract"] = text
                    merged[doi]["found"] = True
        except ds.SearchError:
            pass  # only abstracts are missing then; the rest is there
    return merged, " ".join(errors)


# ---------------------------------------------------------------------------
# GOST R 7.0.100-2018
# ---------------------------------------------------------------------------

def initials(given: str) -> str:
    out = []
    for part in str(given or "").replace(".", ". ").split():
        if "-" in part:
            out.append("-".join(p[0].upper() + "." for p in part.split("-") if p))
        elif part[0].isalpha():
            out.append(part[0].upper() + ".")
    return " ".join(out)


def _is_russian(rec: dict) -> bool:
    return bool(CYRILLIC.search(rec.get("title", "") + rec.get("journal", ""))) or rec.get("language") == "ru"


def gost(rec: dict) -> str:
    """
    One entry by GOST R 7.0.100-2018: up to three authors — the first one in
    the heading and all after the slash; four — all after the slash; five and
    more — the first three and [и др.] / [et al.].
    """
    ru = _is_russian(rec)
    lab = ({"vol": "Т.", "no": "№", "p": "С.", "art": "Ст.", "etal": "и др."} if ru else
           {"vol": "Vol.", "no": "No.", "p": "P.", "art": "Art.", "etal": "et al."})
    authors = [a for a in rec.get("authors") or [] if a.get("family")]
    fam_init = lambda a: f"{a['family']}, {initials(a['given'])}".rstrip(", ")  # noqa: E731
    init_fam = lambda a: f"{initials(a['given'])} {a['family']}".strip()  # noqa: E731

    head, resp = "", ""
    if 1 <= len(authors) <= 3:
        head = fam_init(authors[0]) + " "
        resp = ", ".join(init_fam(a) for a in authors)
    elif len(authors) == 4:
        resp = ", ".join(init_fam(a) for a in authors)
    elif len(authors) >= 5:
        resp = ", ".join(init_fam(a) for a in authors[:3]) + f" [{lab['etal']}]"

    title = (rec.get("title") or "").rstrip(" .") or "[Без названия]"
    text = head + title + (f" / {resp}" if resp else "")
    if rec.get("journal"):
        text += " // " + rec["journal"].rstrip(" .")
    text += "."
    if rec.get("year"):
        text += f" – {rec['year']}."
    parts = []
    if rec.get("volume"):
        parts.append(f"{lab['vol']} {rec['volume']}")
    if rec.get("issue"):
        parts.append(f"{lab['no']} {rec['issue']}")
    if parts:
        text += " – " + ", ".join(parts) + "."
    pages = str(rec.get("pages") or "").replace("--", "-").strip()
    if pages:
        if "-" not in pages and (len(re.sub(r"\D", "", pages)) >= 5
                                 or str(rec.get("doi", "")).startswith(ARTICLE_NUMBER_PREFIXES)):
            text += f" – {lab['art']} {pages}."          # article number, not pages
        else:
            text += f" – {lab['p']} {pages.replace('-', '–')}."
    if rec.get("doi"):
        text += f" – DOI: {rec['doi']}."
    return text


def sort_key(text: str):
    """Russian sources first, then the others, each alphabetically."""
    return (0 if CYRILLIC.match(text) else 1, text.lower())


def gost_list(records: list[dict], alphabetical: bool) -> list[str]:
    entries = [gost(r) for r in records]
    return sorted(entries, key=sort_key) if alphabetical else entries


# ---------------------------------------------------------------------------
# RIS and BibTeX
# ---------------------------------------------------------------------------

def _pages(rec: dict) -> tuple[str, str]:
    pages = str(rec.get("pages") or "")
    first, _, last = pages.partition("-")
    return first.strip(), last.strip()


def to_ris(records: list[dict]) -> str:
    lines = []
    for r in records:
        lines.append("TY  - JOUR")
        for a in r.get("authors") or []:
            if a.get("family"):
                lines.append(f"AU  - {a['family']}, {a.get('given', '')}".rstrip(", "))
        for tag, field in (("TI", "title"), ("JO", "journal"), ("PY", "year"),
                           ("VL", "volume"), ("IS", "issue")):
            if r.get(field):
                lines.append(f"{tag}  - {r[field]}")
        first, last = _pages(r)
        if first:
            lines.append(f"SP  - {first}")
        if last:
            lines.append(f"EP  - {last}")
        if r.get("doi"):
            lines.append(f"DO  - {r['doi']}")
            lines.append(f"UR  - https://doi.org/{r['doi']}")
        if r.get("abstract"):
            lines.append(f"AB  - {r['abstract']}")
        lines.append("ER  - ")
        lines.append("")
    return "\r\n".join(lines)


TRANSLIT = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
                    ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o",
                     "p", "r", "s", "t", "u", "f", "kh", "ts", "ch", "sh", "shch", "", "y", "", "e",
                     "yu", "ya"]))


def _key_part(text: str) -> str:
    text = "".join(TRANSLIT.get(c, c) for c in str(text or "").lower())
    return re.sub(r"[^a-z0-9]", "", text)


def _tex(text: str) -> str:
    return re.sub(r"([&%$#_])", r"\\\1", str(text or ""))


def to_bibtex(records: list[dict]) -> str:
    out, used = [], Counter()
    for r in records:
        authors = [a for a in r.get("authors") or [] if a.get("family")]
        first_word = next((w for w in re.findall(r"\w+", r.get("title", "")) if len(w) > 3), "article")
        key = (_key_part(authors[0]["family"]) if authors else "anon") + (r.get("year") or "") + _key_part(first_word)
        used[key] += 1
        if used[key] > 1:
            key += "abcdefghijklmnopqrstuvwxyz"[min(used[key] - 2, 25)]
        fields = []
        if authors:
            fields.append(("author", " and ".join(f"{a['family']}, {a.get('given', '')}".rstrip(", ") for a in authors)))
        fields.append(("title", "{" + _tex(r.get("title", "")) + "}"))
        for name, field in (("journal", "journal"), ("year", "year"), ("volume", "volume"), ("number", "issue")):
            if r.get(field):
                fields.append((name, _tex(r[field])))
        first, last = _pages(r)
        if first:
            fields.append(("pages", f"{first}--{last}" if last else first))
        if r.get("doi"):
            fields.append(("doi", r["doi"]))
        body = ",\n".join(f"  {name} = {{{value}}}" for name, value in fields)
        out.append(f"@article{{{key},\n{body}\n}}\n")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# PRISMA 2020 numbers
# ---------------------------------------------------------------------------

def prisma(review: dict, meta: dict, downloaded: set[str]) -> dict:
    records = [review["records"][d] for d in review["order"] if d in review["records"]]
    decided = Counter(r.get("decision") or "undecided" for r in records)
    reasons = Counter(r.get("reason") or "без причины" for r in records if r.get("decision") == "exclude")
    per_source = meta.get("per_source") or {}
    identified = sum(per_source.values()) if per_source else len(records)
    included = [r["doi"] for r in records if r.get("decision") == "include"]
    got = sum(d in downloaded for d in included)
    return {
        "per_source": per_source,
        "identified": identified,
        "duplicates": int(meta.get("duplicates") or 0),
        "no_doi": int(meta.get("no_doi") or 0),
        "removed_by_hand": int(meta.get("deselected") or 0),
        "screened": len(records),
        "screened_done": len(records) - decided["undecided"],
        "excluded": decided["exclude"],
        "reasons": dict(reasons.most_common()),
        "maybe": decided["maybe"],
        "included": decided["include"],
        "undecided": decided["undecided"],
        "retrieved": got,
        "not_retrieved": len(included) - got,
    }
