# -*- coding: utf-8 -*-
# © 2026 Bucaneiro. Бесплатная программа; условия — в файле ЛИЦЕНЗИЯ.txt.
"""
«📄 Привести в порядок свой список» (v8.7): a reference list from a Word file
(or pasted text), in any shape, put into the chosen style.

split_entries() cuts the text into references (by the numbers when most lines
are numbered, else one per paragraph). Each reference is then found:
- its DOI, when it has one: the exact metadata (status "doi");
- else Crossref's search made for this (query.bibliographic), accepted only
  when the found title, year and authors agree with the reference
  (status "search": shown for checking);
- else it stays as written (status "none"); the AI helper can format those.
"""
from __future__ import annotations

import os
import re
from urllib.parse import quote

import doi_search as ds

CROSSREF_URL = "https://api.crossref.org/works"
NUMBER_RE = re.compile(r"^\s*(?:\[\d{1,4}\]|\(\d{1,4}\)|\d{1,4}\s*[.)]|\d{1,4}\s+(?=[^\d\s]))\s*")
HEADING_RE = re.compile(r"^\s*(список (использованн\w+ )?(литературы|источников)|литература|библиографи\w*( список)?|"
                        r"references|bibliography|reference list|literature cited)\s*:?\s*$", re.I)
WORD_RE = re.compile(r"[^\W_]{3,}", re.U)
YEAR_RE = re.compile(r"\b(19[5-9]\d|20[0-4]\d)\b")


def split_entries(text: str) -> list[str]:
    """The references of a list: numbered ones by their numbers, else one per paragraph."""
    lines = [ln.strip().strip("﻿") for ln in str(text or "").replace("\r", "\n").split("\n")]
    lines = [ln for ln in lines if ln and not HEADING_RE.match(ln)]
    if not lines:
        return []
    numbered = sum(1 for ln in lines if NUMBER_RE.match(ln))
    entries: list[str] = []
    if numbered >= max(2, len(lines) // 2):
        for ln in lines:
            if NUMBER_RE.match(ln) or not entries:
                entries.append(NUMBER_RE.sub("", ln, count=1))
            else:                                   # a wrapped line of the reference above
                entries[-1] += (" " if not entries[-1].endswith("-") else "") + ln
    else:
        entries = [NUMBER_RE.sub("", ln, count=1) for ln in lines]
    return [re.sub(r"\s+", " ", e).strip() for e in entries if len(WORD_RE.findall(e)) >= 2]


def find_doi(entry: str) -> str:
    for raw in ds.DOI_RE.findall(entry):
        doi = ds.normalize_doi(raw.rstrip(".,;:]"))
        while doi.endswith(")") and doi.count("(") < doi.count(")"):
            doi = doi[:-1]
        if doi:
            return doi
    return ""


def _words(text: str) -> set[str]:
    return {w.lower() for w in WORD_RE.findall(str(text or ""))}


def matches(entry: str, cand: dict) -> bool:
    """Does the Crossref record cand describe this reference? Title words, year and authors must agree."""
    title = _words(cand.get("title"))
    if len(title) < 2:
        return False
    have = _words(entry)
    overlap = len(title & have) / len(title)
    years = {int(y) for y in YEAR_RE.findall(entry)}
    year = int(cand["year"]) if str(cand.get("year") or "").isdigit() else 0
    if years and year and min(abs(year - y) for y in years) > 1:
        return False
    families = [a.lower() for a in cand.get("families") or [] if a][:3]
    author_ok = not families or any(f in entry.lower() for f in families)
    return overlap >= 0.9 or (overlap >= 0.75 and author_ok and (not years or year in years or not year))


def search_crossref(entry: str, timeout: int = 30) -> dict | None:
    """The best Crossref match for a reference without a DOI, or None."""
    base = os.environ.get("ARTICLE_CROSSREF_URL") or CROSSREF_URL
    url = (f"{base}?rows=3&query.bibliographic={quote(entry[:600])}"
           "&select=DOI,title,author,issued,container-title,type")
    data, _ = ds._get_json("crossref", url, {}, timeout=timeout)
    for item in (data.get("message") or {}).get("items") or []:
        parts = ((item.get("issued") or {}).get("date-parts") or [[None]])[0] or [None]
        cand = {"doi": ds.normalize_doi(item.get("DOI")), "title": (item.get("title") or [""])[0],
                "year": str(parts[0] or ""),
                "families": [a.get("family") or "" for a in item.get("author") or []]}
        if cand["doi"] and matches(entry, cand):
            return cand
    return None
