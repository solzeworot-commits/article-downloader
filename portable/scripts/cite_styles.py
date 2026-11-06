# -*- coding: utf-8 -*-
# © 2026 Bucaneiro. Бесплатная программа; условия — в файле ЛИЦЕНЗИЯ.txt.
"""
Reference list styles for journals (v7.8).

Two layers:
  * built-in styles, offline and instant: GOST R 7.0.100-2018 (review.gost),
    APA 7, Elsevier Harvard, Vancouver (NLM), ACS, IEEE, Chicago author-date
    (18th) and Nature. The rules (et al. thresholds, name forms, punctuation,
    italic and bold parts) follow the official CSL files of these styles
    (Citation Style Language repository, May 2026);
  * the style of any journal: an index of the ~10 800 CSL styles
    (csl_styles.json.gz, made by tools/make_csl_index.py) to find a journal by
    name, and the reference itself formatted by doi.org content negotiation
    (Accept: text/x-bibliography; style=...), which runs the full CSL
    processor of Crossref / DataCite. A journal's own (dependent) style is
    asked for by its independent parent, which carries all the formatting.

A formatted entry is a list of segments (text, "" | "i" | "b"), so it can be
shown and copied with italics and bold, saved as plain text or as a Word file.
ARTICLE_DOI_URL points the content negotiation at a fake server in tests.
"""
from __future__ import annotations

import gzip
import json
import os
import re
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from urllib.parse import quote
from xml.sax.saxutils import escape

import review as rv
from i18n import tr

SCRIPT_DIR = Path(__file__).resolve().parent
CSL_INDEX = SCRIPT_DIR / "csl_styles.json.gz"
DOI_URL = "https://doi.org/"
USER_AGENT = "ArticleDownloader (+https://github.com/solzeworot-commits/article-downloader)"

Seg = tuple[str, str]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _authors(rec: dict) -> list[dict]:
    return [a for a in rec.get("authors") or [] if a.get("family")]


def _ini(given: str, dots: bool = True, spaces: bool = True) -> str:
    text = rv.initials(given)                      # "J. P." / "J.-P."
    if not spaces:
        text = text.replace(" ", "")
    if not dots:
        text = text.replace(".", "")
    return text


def _join(names: list[str], last_sep: str, sep: str = ", ") -> str:
    if len(names) <= 1:
        return "".join(names)
    return sep.join(names[:-1]) + last_sep + names[-1]


def _end(text: str, mark: str = ".") -> str:
    """The text with a final mark unless it already ends with one."""
    text = text.rstrip()
    return text if not text or text[-1] in ".?!" else text + mark


def _pages(rec: dict, dash: str = "–") -> tuple[str, bool]:
    """(pages, is an article number)."""
    pages = str(rec.get("pages") or "").replace("--", "-").strip()
    if not pages:
        return "", False
    if "-" not in pages and (len(re.sub(r"\D", "", pages)) >= 5
                             or str(rec.get("doi", "")).startswith(rv.ARTICLE_NUMBER_PREFIXES)):
        return pages, True
    return pages.replace("-", dash), False


def _journal(rec: dict, short: bool = False) -> str:
    name = (rec.get("journal_abbr") if short else "") or rec.get("journal") or ""
    return name.strip().rstrip(".") if not short else name.strip()


def _title(rec: dict) -> str:
    return (rec.get("title") or "").strip() or "[No title]"


def _doi_url(rec: dict) -> str:
    return f"https://doi.org/{rec['doi']}" if rec.get("doi") else ""


def plain(segs: list[Seg]) -> str:
    return "".join(t for t, _ in segs)


def html(segs: list[Seg]) -> str:
    out = []
    for text, fmt in segs:
        part = escape(text)
        if "i" in fmt:
            part = f"<i>{part}</i>"
        if "b" in fmt:
            part = f"<b>{part}</b>"
        out.append(part)
    return "".join(out)


# ---------------------------------------------------------------------------
# built-in styles: one journal article each
# ---------------------------------------------------------------------------

def apa(rec: dict) -> list[Seg]:
    """APA 7: Family, J. P., & Family, J. (2020). Title. Journal, 12(3), 45–67. https://doi.org/..."""
    names = [f"{a['family']}, {_ini(a.get('given', ''))}".rstrip(", ") for a in _authors(rec)]
    if len(names) >= 21:
        who = ", ".join(names[:19]) + ", . . . " + names[-1]
    elif len(names) == 2:
        who = names[0] + ", & " + names[1]
    else:
        who = _join(names, ", & ")
    year = rec.get("year") or "n.d."
    segs: list[Seg] = [(f"{who} ({year}). " if who else "", ""), (_end(_title(rec)) + " ", "")]
    if not who:
        segs = [(_end(_title(rec)) + f" ({year}). ", "")]
    journal = _journal(rec)
    if journal:
        segs.append((journal, "i"))
        if rec.get("volume"):
            segs += [(", ", ""), (str(rec["volume"]), "i")]
            if rec.get("issue"):
                segs.append((f"({rec['issue']})", ""))
        pages, art = _pages(rec)
        if pages:
            segs.append((f", Article {pages}" if art else f", {pages}", ""))
        segs.append((". ", ""))
    if rec.get("doi"):
        segs.append((_doi_url(rec), ""))
    return _trim(segs)


def harvard(rec: dict) -> list[Seg]:
    """Elsevier Harvard (with titles): Family, J.P., Family, J., 2020. Title. J. Abbr. 12, 45–67. https://doi.org/..."""
    names = [f"{a['family']}, {_ini(a.get('given', ''), spaces=False)}".rstrip(", ") for a in _authors(rec)]
    who = ", ".join(names)
    year = rec.get("year") or "n.d."
    text = (f"{who}, {year}. " if who else f"{year}. ") + _end(_title(rec)) + " "
    journal = _journal(rec, short=True)
    if journal:
        text += journal
        if rec.get("volume"):
            text += f" {rec['volume']}"
        pages, art = _pages(rec)
        if pages:
            text += f", {pages}"
        text = _end(text) + " "
    if rec.get("doi"):
        text += _doi_url(rec)
    return _trim([(text, "")])


def vancouver(rec: dict) -> list[Seg]:
    """Vancouver (NLM): Family JP, Family J, et al. Title. J Abbr. 2020;12(3):45-67. doi:..."""
    names = [f"{a['family']} {_ini(a.get('given', ''), dots=False, spaces=False).replace('-', '')}".strip()
             for a in _authors(rec)]
    who = ", ".join(names[:6]) + (", et al" if len(names) >= 7 else "")
    text = (f"{who}. " if who else "") + _end(_title(rec)) + " "
    journal = _journal(rec, short=True).replace(".", "")
    if journal:
        text += f"{journal}. "
    if rec.get("year"):
        text += str(rec["year"])
    if rec.get("volume"):
        text += f";{rec['volume']}"
        if rec.get("issue"):
            text += f"({rec['issue']})"
    pages, _ = _pages(rec, dash="-")
    if pages:
        text += f":{pages}"
    text = _end(text) + " "
    if rec.get("doi"):
        text += f"doi:{rec['doi']}"
    return _trim([(text, "")])


def acs(rec: dict) -> list[Seg]:
    """ACS: Family, J. P.; Family, J. Title. J. Abbr. 2020, 12 (3), 45–67. https://doi.org/..."""
    names = [f"{a['family']}, {_ini(a.get('given', ''))}".rstrip(", ") for a in _authors(rec)]
    who = _end("; ".join(names))
    segs: list[Seg] = [(f"{who} " if who else "", ""), (_end(_title(rec)) + " ", "")]
    journal = _journal(rec, short=True)
    if journal:
        segs += [(journal, "i"), (" ", "")]
    if rec.get("year"):
        segs += [(str(rec["year"]), "b")]
    if rec.get("volume"):
        segs += [(", ", ""), (str(rec["volume"]), "i")]
        if rec.get("issue"):
            segs.append((f" ({rec['issue']})", ""))
    pages, _ = _pages(rec)
    if pages:
        segs.append((f", {pages}", ""))
    segs.append((". ", ""))
    if rec.get("doi"):
        segs.append((_doi_url(rec) + ".", ""))
    return _trim(segs)


def ieee(rec: dict) -> list[Seg]:
    """IEEE: J. P. Family, J. Family, and A. Family, “Title,” J. Abbr., vol. 12, no. 3, pp. 45–67, 2020, doi: ..."""
    names = [f"{_ini(a.get('given', ''))} {a['family']}".strip() for a in _authors(rec)]
    if len(names) >= 7:
        who = names[0] + " et al."
    elif len(names) == 2:
        who = names[0] + " and " + names[1]
    else:
        who = _join(names, ", and ")
    title = _title(rec).rstrip(".")
    segs: list[Seg] = [(f"{who}, " if who else "", ""),
                       (f"“{title}” " if title.endswith(("?", "!")) else f"“{title},” ", "")]
    journal = _journal(rec, short=True)
    parts: list[Seg] = []
    if journal:
        parts.append((journal, "i"))
    if rec.get("volume"):
        parts.append((f"vol. {rec['volume']}", ""))
    if rec.get("issue"):
        parts.append((f"no. {rec['issue']}", ""))
    pages, art = _pages(rec)
    if pages:
        parts.append((f"Art. no. {pages}" if art else (f"pp. {pages}" if "–" in pages else f"p. {pages}"), ""))
    if rec.get("year"):
        parts.append((str(rec["year"]), ""))
    if rec.get("doi"):
        parts.append((f"doi: {rec['doi']}", ""))
    for i, part in enumerate(parts):
        segs.append(part)
        segs.append((", " if i < len(parts) - 1 else ".", ""))
    return _trim(segs)


def chicago(rec: dict) -> list[Seg]:
    """Chicago 18th author-date: Family, Given, Given Family, and Given Family. 2020. “Title.” Journal 12 (3): 45–67. https://doi.org/..."""
    authors = _authors(rec)
    names = []
    for i, a in enumerate(authors):
        given = (a.get("given") or "").strip()
        names.append(f"{a['family']}, {given}".rstrip(", ") if i == 0 else f"{given} {a['family']}".strip())
    if len(names) >= 7:
        who = ", ".join(names[:3]) + ", et al."
    elif len(names) == 2:
        who = names[0] + ", and " + names[1]
    else:
        who = _join(names, ", and ")
    year = rec.get("year") or "n.d."
    segs: list[Seg] = [(f"{_end(who)} {year}. " if who else f"{year}. ", ""),
                       (f"“{_end(_title(rec))}” ", "")]
    journal = _journal(rec)
    if journal:
        segs.append((journal, "i"))
        if rec.get("volume"):
            segs.append((f" {rec['volume']}", ""))
            if rec.get("issue"):
                segs.append((f" ({rec['issue']})", ""))
        pages, _ = _pages(rec)
        if pages:
            segs.append((f": {pages}", ""))
        segs.append((". ", ""))
    if rec.get("doi"):
        segs.append((_doi_url(rec) + ".", ""))
    return _trim(segs)


def nature(rec: dict) -> list[Seg]:
    """Nature: Family, J. P., Family, J. & Family, A. Title. J. Abbr. 12, 45–67 (2020)."""
    names = [f"{a['family']}, {_ini(a.get('given', ''))}".rstrip(", ") for a in _authors(rec)]
    if len(names) >= 6:
        who = names[0] + " et al."
    else:
        who = _join(names, " & ")
    segs: list[Seg] = [(f"{_end(who)} " if who else "", ""), (_end(_title(rec)) + " ", "")]
    journal = _journal(rec, short=True)
    if journal:
        segs.append((journal, "i"))
    if rec.get("volume"):
        segs += [(" ", ""), (str(rec["volume"]), "b")]
    pages, _ = _pages(rec)
    if pages:
        segs.append((f", {pages}", ""))
    if rec.get("year"):
        segs.append((f" ({rec['year']})", ""))
    segs.append((".", ""))
    return _trim(segs)


def gost(rec: dict) -> list[Seg]:
    return [(rv.gost(rec), "")]


def _trim(segs: list[Seg]) -> list[Seg]:
    """No empty pieces and no space at the end."""
    segs = [(t, f) for t, f in segs if t]
    while segs and segs[-1][0] != segs[-1][0].rstrip():
        text, fmt = segs[-1]
        segs[-1] = (text.rstrip(), fmt)
        if not segs[-1][0]:
            segs.pop()
    return segs


# id -> (title, citation format, formatter, label of the list)
STYLES = {
    "gost": ("ГОСТ Р 7.0.100-2018", "numeric", gost, "{n}. "),
    "apa": ("APA 7", "author-date", apa, ""),
    "harvard": ("Elsevier Harvard", "author-date", harvard, ""),
    "vancouver": ("Vancouver (NLM)", "numeric", vancouver, "{n}. "),
    "acs": ("ACS", "numeric", acs, "({n}) "),
    "ieee": ("IEEE", "numeric", ieee, "[{n}] "),
    "chicago": ("Chicago (author-date)", "author-date", chicago, ""),
    "nature": ("Nature", "numeric", nature, "{n}. "),
}


def first_author_key(rec: dict) -> tuple:
    authors = _authors(rec)
    family = authors[0]["family"].lower() if authors else _title(rec).lower()
    return (family, str(rec.get("year") or ""), _title(rec).lower())


def entries(records: list[dict], style: str, alphabetical: bool) -> list[dict]:
    """[{"label", "text", "html"}] in the order of the list."""
    if style not in STYLES:
        style = "gost"
    title, _fmt, fn, label = STYLES[style]
    if style == "gost":
        texts = rv.gost_list(records, alphabetical)
        segs_list = [[(t, "")] for t in texts]
    else:
        recs = sorted(records, key=first_author_key) if alphabetical else list(records)
        segs_list = [fn(r) for r in recs]
    return [{"label": label.format(n=n), "text": plain(s), "html": html(s)} for n, s in enumerate(segs_list, 1)]


# ---------------------------------------------------------------------------
# the style of any journal: CSL index + doi.org content negotiation
# ---------------------------------------------------------------------------

_index: list | None = None


def csl_index() -> list:
    """[[id, title, format, parent id or ""], ...]"""
    global _index
    if _index is None:
        try:
            with gzip.open(CSL_INDEX, "rt", encoding="utf-8") as f:
                _index = json.load(f)["styles"]
        except (OSError, ValueError, KeyError):
            _index = []
    return _index


def csl_find(style_id: str) -> dict | None:
    for sid, title, fmt, parent in csl_index():
        if sid == style_id:
            return {"id": sid, "title": title, "format": fmt, "parent": parent}
    return None


def csl_search(query: str, limit: int = 25) -> list[dict]:
    """Styles whose title has all the words of the query; titles that start with it first."""
    words = [w for w in re.split(r"\s+", (query or "").lower().strip()) if w]
    if not words:
        return []
    found = []
    for sid, title, fmt, parent in csl_index():
        low = title.lower()
        if all(w in low or w in sid for w in words):
            rank = (0 if low.startswith(" ".join(words)) else 1, len(title))
            found.append((rank, {"id": sid, "title": title, "format": fmt}))
    found.sort(key=lambda x: x[0])
    return [x[1] for x in found[:limit]]


class StyleError(Exception):
    pass


NUMBER_RE = re.compile(r"^\s*(?:\[\d+\]|\(\d+\)|\d+[.)])\s*")


def csl_format(doi: str, style_id: str, numeric: bool, locale: str = "en-US", timeout: float = 30) -> str:
    """One reference in the style, formatted by doi.org (Crossref / DataCite)."""
    base = os.environ.get("ARTICLE_DOI_URL") or DOI_URL
    request = urllib.request.Request(base + quote(doi, safe="/:;()[]<>"), headers={
        "Accept": f"text/x-bibliography; style={style_id}; locale={locale}", "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            text = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        raise StyleError(tr("doi.org не смог оформить статью (код {code}).", code=exc.code)) from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise StyleError(tr("Нет связи с doi.org ({reason}).", reason=getattr(exc, "reason", exc))) from None
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", text)).strip()
    if not text or text.lower().startswith(("<!doctype", "<html")):
        raise StyleError(tr("doi.org прислал пустой ответ."))
    return NUMBER_RE.sub("", text) if numeric else text


def csl_entries(records: list[dict], style: dict, alphabetical: bool) -> list[dict]:
    """Entries from the texts cached in the records (rec["csl"][style id]); a fallback for the rest."""
    numeric = style["format"] in ("numeric", "label")
    recs = sorted(records, key=first_author_key) if alphabetical else list(records)
    out = []
    for n, rec in enumerate(recs, 1):
        text = ((rec.get("csl") or {}).get(style["id"]) or "").strip()
        fallback = not text
        if fallback:      # not formatted (yet): the closest built-in style
            text = plain(vancouver(rec) if numeric else apa(rec))
        out.append({"label": f"{n}. " if numeric else "", "text": text, "html": escape(text), "fallback": fallback})
    return out


# ---------------------------------------------------------------------------
# a Word file
# ---------------------------------------------------------------------------

def _run(text: str, fmt: str) -> str:
    props = ("<w:i/>" if "i" in fmt else "") + ("<w:b/>" if "b" in fmt else "")
    props = f"<w:rPr>{props}</w:rPr>" if props else ""
    return f'<w:r>{props}<w:t xml:space="preserve">{escape(text)}</w:t></w:r>'


def _segments_from_html(text: str) -> list[Seg]:
    segs, fmt = [], ""
    for part in re.split(r"(</?[ib]>)", text):
        if part in ("<i>", "<b>"):
            fmt += part[1]
        elif part in ("</i>", "</b>"):
            fmt = fmt.replace(part[2], "")
        elif part:
            segs.append((part.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&"), fmt))
    return segs


def to_docx(path: Path, heading: str, items: list[dict]):
    """A minimal .docx: a heading and one paragraph per entry, italics and bold kept."""
    paras = [f'<w:p><w:pPr><w:spacing w:after="200"/></w:pPr>{_run(heading, "b")}</w:p>']
    for item in items:
        runs = _run(item["label"], "") if item["label"] else ""
        runs += "".join(_run(t, f) for t, f in _segments_from_html(item["html"]))
        paras.append('<w:p><w:pPr><w:spacing w:after="120"/><w:ind w:left="360" w:hanging="360"/></w:pPr>'
                     f"{runs}</w:p>")
    document = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
                + "".join(paras) +
                '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1134" w:right="850" w:bottom="1134" '
                'w:left="1701" w:header="709" w:footer="709" w:gutter="0"/></w:sectPr></w:body></w:document>')
    types = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
             '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
             '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
             '<Default Extension="xml" ContentType="application/xml"/>'
             '<Override PartName="/word/document.xml" '
             'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
            'officeDocument" Target="word/document.xml"/></Relationships>')
    tmp = path.with_suffix(".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", types)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", document)
    os.replace(tmp, path)
