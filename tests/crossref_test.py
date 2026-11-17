"""Crossref instead of OpenAlex past its daily limit (v9.2): python crossref_test.py <portable/scripts>

Without a key OpenAlex gives an IP address a small daily budget, and a whole
university goes out through one address. When OpenAlex answers 429 the search
asks Crossref instead (query.bibliographic, date and type filters, cursor pages,
the polite pool when a contact address is set) and says so plainly; the records
merge with Scopus and WoS by DOI, and the saved list and PRISMA name Crossref.
A fake API (fake_api.py).
"""
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
TMP = Path(tempfile.mkdtemp(prefix="ad_crossref_"))
BASE = "http://127.0.0.1:8013"
os.environ.update(
    ARTICLE_SCOPUS_URL=BASE + "/scopus",
    ARTICLE_WOS_URL=BASE + "/wos",
    ARTICLE_OPENALEX_URL=BASE + "/openalex",
    ARTICLE_CROSSREF_URL=BASE + "/crossref",
    ARTICLE_WOS_DELAY="0",
    ARTICLE_CROSSREF_DELAY="0",
    ARTICLE_KEYS_FILE=str(TMP / "keys.json"),
    ARTICLE_UI_NO_WINDOW="1",
    NO_PROXY="127.0.0.1,localhost",
)
os.environ.pop("ARTICLE_CROSSREF_MAILTO", None)
sys.path.insert(0, sys.argv[1])
sys.path.insert(0, str(HERE))
import doi_search as ds  # noqa: E402
import fake_api  # noqa: E402
import i18n  # noqa: E402

fake_api.start(8013)
GOOD = {"scopus": "good-scopus-key-1234", "wos": "good-wos-key-5678", "openalex": ""}
PARAMS = {"keywords": "grape pomace; grape marc\npolyphenols", "year_from": "2015", "year_to": "2024",
          "doc_type": "both", "max_results": 200}
NOTICE = "Лимит OpenAlex на сегодня исчерпан, результаты взяты из Crossref (поиск менее точный)."

# --- the Crossref query ------------------------------------------------------------------
q = ds.build_queries(PARAMS)["crossref"]
assert q == {"query": "grape pomace grape marc polyphenols",
             "filter": "from-pub-date:2015-01-01,until-pub-date:2024-12-31,type:journal-article"}, q
# Crossref has no "review" type: reviews are journal articles there too.
assert ds.build_queries({"keywords": "x", "doc_type": "review"})["crossref"]["filter"] == "type:journal-article"
assert ds.build_queries({"keywords": "x", "doc_type": "all"})["crossref"]["filter"] == ""

# --- search_crossref: cursor pages, markup, no contact address by default -----------------
fake_api.CROSSREF_SEEN.clear()
res = ds.search_crossref(q, 450)
assert len(res["records"]) == 450 and res["total"] == 500 and res["no_doi"] == 0, (len(res["records"]), res["total"])
pages = fake_api.CROSSREF_SEEN
assert [p["cursor"] for p in pages] == ["*", "c200", "c400"], [p["cursor"] for p in pages]
first = pages[0]
assert first["query.bibliographic"] == "grape pomace grape marc polyphenols", first
assert first["filter"] == "from-pub-date:2015-01-01,until-pub-date:2024-12-31,type:journal-article", first
assert first["rows"] == "200" and first["select"] == "DOI,title,container-title,issued", first
# With a cursor Crossref does not rank by relevance unless asked to.
assert all(p["sort"] == "score" and p["order"] == "desc" for p in pages), pages
assert "mailto" not in first, first          # nothing about the user goes to Crossref
rec = res["records"][7]
assert rec == {"doi": "10.8888/crossref.7", "title": "Crossref work 7 on Vitis vinifera pomace",
               "year": "2019", "journal": "Food & Function", "sources": ["crossref"]}, rec

# The program's contact address (the polite pool), when one is set.
os.environ["ARTICLE_CROSSREF_MAILTO"] = "tool@example.org"
fake_api.CROSSREF_SEEN.clear()
assert len(ds.search_crossref(q, 5)["records"]) == 5
assert fake_api.CROSSREF_SEEN[0]["mailto"] == "tool@example.org" and fake_api.CROSSREF_SEEN[0]["rows"] == "5"
del os.environ["ARTICLE_CROSSREF_MAILTO"]

# --- OpenAlex 429 → Crossref ----------------------------------------------------------------
fake_api.OPENALEX_QUOTA = True
assert ds.check_key("openalex", {})["kind"] == "quota"        # a keyless IP past its daily limit
progress = []
res = ds.run_search(PARAMS, ["scopus", "wos", "openalex"], GOOD, lambda s, d, o: progress.append((s, d, o)))
ps = res["per_source"]
oa, cr = ps["openalex"], ps["crossref"]
assert not oa["ok"] and oa["kind"] == "quota" and oa["fallback"] == "crossref" and oa["message"] == NOTICE, oa
assert cr["ok"] and cr["instead_of"] == "openalex" and cr["taken"] == 200 and cr["with_doi"] == 200, cr
assert ps["scopus"]["ok"] and ps["wos"]["ok"], ps
assert list(ps) == ["scopus", "wos", "openalex", "crossref"], list(ps)
assert ("crossref", 200, 200) in progress, progress[-3:]
common = next(r for r in res["records"] if r["doi"] == "10.9999/common.3")
assert common["sources"] == ["scopus", "wos", "crossref"], common      # one record per DOI
dois = [r["doi"] for r in res["records"]]
# Scopus 54 + WoS 35 − 8 in both; Crossref 200, of which common.0–4 are already there.
assert len(dois) == len(set(dois)) == 54 + 35 - 8 + 200 - 5, len(dois)

# Only reviews asked for: Crossref cannot tell them apart, and the message says so.
res = ds.run_search({**PARAMS, "doc_type": "review"}, ["openalex"], {})
assert res["per_source"]["openalex"]["message"] == NOTICE + " Обзоры от статей Crossref не отличает.", res["per_source"]

# The message in the English window.
i18n.set_lang("en")
res = ds.run_search(PARAMS, ["openalex"], {})
assert res["per_source"]["openalex"]["message"].startswith("The OpenAlex limit for today is used up"), res["per_source"]
i18n.set_lang("ru")

# Crossref does not answer either: the usual OpenAlex advice stays, both are reported.
os.environ["ARTICLE_CROSSREF_URL"] = "http://127.0.0.1:9/crossref"     # nothing listens there
res = ds.run_search(PARAMS, ["openalex"], {})
oa, cr = res["per_source"]["openalex"], res["per_source"]["crossref"]
assert oa["kind"] == "quota" and "fallback" not in oa and "ключ OpenAlex" in oa["message"], oa
assert not cr["ok"] and cr["kind"] == "network" and res["records"] == [], (cr, len(res["records"]))
os.environ["ARTICLE_CROSSREF_URL"] = BASE + "/crossref"

# Any other OpenAlex trouble (no network) is not a reason to switch.
os.environ["ARTICLE_OPENALEX_URL"] = "http://127.0.0.1:9/openalex"
res = ds.run_search(PARAMS, ["openalex"], {})
assert list(res["per_source"]) == ["openalex"] and res["per_source"]["openalex"]["kind"] == "network", res["per_source"]
os.environ["ARTICLE_OPENALEX_URL"] = BASE + "/openalex"

# --- the saved list and PRISMA name Crossref --------------------------------------------------
res = ds.run_search(PARAMS, ["openalex"], {})
saved = ds.save_list(TMP / "inbox", "crossref", res["records"][:3])
detail = (TMP / "inbox" / "_подробно" / (saved.stem + ".csv")).read_text(encoding="utf-8-sig")
assert [line.rsplit(";", 1)[1] for line in detail.splitlines()[1:]] == ["Crossref"] * 3, detail

import app  # noqa: E402

app._set_search_result(res["records"], {"from": "api", "per_source": res["per_source"],
                                        "no_doi": res["no_doi"], "count": len(res["records"])})
meta = app.search_meta({"keywords": PARAMS["keywords"]}, res["records"][:3])
assert meta["per_source"] == {"Crossref": 200} and meta["duplicates"] == 0, meta

fake_api.OPENALEX_QUOTA = False
res = ds.run_search(PARAMS, ["openalex"], {})
assert list(res["per_source"]) == ["openalex"] and res["per_source"]["openalex"]["ok"], res["per_source"]
print("crossref tests: OK")
