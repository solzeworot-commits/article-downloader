"""Checks of the DOI search (v6.1): python search_test.py <portable/scripts>"""
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
TMP = Path(tempfile.mkdtemp(prefix="ad_search_"))
os.environ.update(
    ARTICLE_SCOPUS_URL="http://127.0.0.1:8011/scopus",
    ARTICLE_WOS_URL="http://127.0.0.1:8011/wos",
    ARTICLE_OPENALEX_URL="http://127.0.0.1:8011/openalex",
    ARTICLE_WOS_DELAY="0",
    ARTICLE_KEYS_FILE=str(TMP / "keys.json"),
    NO_PROXY="127.0.0.1,localhost",
)
sys.path.insert(0, sys.argv[1])
sys.path.insert(0, str(HERE))
import doi_search as ds  # noqa: E402
import fake_api  # noqa: E402

fake_api.start(8011)
GOOD = {"scopus": "good-scopus-key-1234", "wos": "good-wos-key-5678", "openalex": ""}

# --- queries ---------------------------------------------------------------
q = ds.build_queries({"keywords": "grape pomace\npolyphenols", "year_from": "2015",
                      "year_to": "2024", "doc_type": "both"})
assert q["scopus"] == ('TITLE-ABS-KEY("grape pomace" AND polyphenols) AND PUBYEAR > 2014 '
                       "AND PUBYEAR < 2025 AND (DOCTYPE(ar) OR DOCTYPE(re))"), q["scopus"]
assert q["wos"] == 'TS=("grape pomace" AND polyphenols) AND PY=(2015-2024) AND DT=(Article OR Review)', q["wos"]
q = ds.build_queries({"keywords": "spent grain\nbrewery", "match": "any", "field": "title", "doc_type": "all"})
assert q["scopus"] == 'TITLE("spent grain" OR brewery)' and q["wos"] == 'TI=("spent grain" OR brewery)', q
assert q["openalex"]["filter"] == "title.search:spent grain|brewery", q
try:
    ds.build_queries({"keywords": "  \n "})
    raise AssertionError("empty keywords accepted")
except ds.SearchError as exc:
    assert exc.kind == "bad_query"

# --- all three sources, merged ----------------------------------------------
res = ds.run_search({"keywords": "grape", "max_results": 200}, ["scopus", "wos", "openalex"], GOOD)
ps = res["per_source"]
assert ps["scopus"] == {"ok": True, "total": 60, "taken": 60, "with_doi": 54, "no_doi": 6, "quota": "19876"}, ps["scopus"]
assert ps["wos"]["with_doi"] == 35 and ps["wos"]["no_doi"] == 5 and ps["wos"]["quota"] == "48", ps["wos"]
assert ps["openalex"]["with_doi"] == 25 and ps["openalex"]["no_doi"] == 5, ps["openalex"]
dois = [r["doi"] for r in res["records"]]
assert len(dois) == len(set(dois)), "duplicates in merged list"
common = next(r for r in res["records"] if r["doi"] == "10.9999/common.3")
assert common["sources"] == ["scopus", "wos"], common          # same DOI, different case
# common.0-8 have a DOI in Scopus, common.0-6,8,9 in WoS: eight are in both.
assert len(dois) == 54 + 35 + 25 - 8, len(dois)

res = ds.run_search({"keywords": "grape", "max_results": 25}, ["scopus", "wos"], GOOD)
assert res["per_source"]["scopus"]["taken"] == 25 and res["per_source"]["wos"]["taken"] == 25, res["per_source"]

# --- errors: one failing source does not stop the others ---------------------
cases = [
    ({"scopus": "wrong-key-0000"}, "scopus", "bad_key"),
    ({"scopus": "noaccess-key-0000"}, "scopus", "no_access"),
    ({"scopus": "quota-key-0000"}, "scopus", "quota"),
    ({"scopus": ""}, "scopus", "no_key"),
    ({"wos": "pending-key-0000"}, "wos", "pending"),
    ({"wos": "wrong-key-0000"}, "wos", "bad_key"),
    ({"wos": "quota-key-0000"}, "wos", "quota"),
]
for override, source, kind in cases:
    res = ds.run_search({"keywords": "grape", "max_results": 10}, ["scopus", "wos"], {**GOOD, **override})
    got = res["per_source"][source]
    assert not got["ok"] and got["kind"] == kind, (override, got)
    other = "wos" if source == "scopus" else "scopus"
    assert res["per_source"][other]["ok"], res["per_source"][other]
    assert got["message"] and "Traceback" not in got["message"]

assert ds.check_key("scopus", GOOD)["ok"] and "19876" in ds.check_key("scopus", GOOD)["message"]
assert ds.check_key("wos", {**GOOD, "wos": "pending-x"})["kind"] == "pending"
assert ds.check_key("openalex", {})["ok"]

os.environ["ARTICLE_SCOPUS_URL"] = "http://127.0.0.1:9/scopus"     # nothing listens there
assert ds.check_key("scopus", GOOD)["kind"] == "network"
os.environ["ARTICLE_SCOPUS_URL"] = "http://127.0.0.1:8011/scopus"

# --- keyword hints (OpenAlex) ---------------------------------------------------
hints = ds.suggest_keywords("grape pomace\n", {})
terms = [h["term"] for h in hints["terms"]]
assert hints["papers"] == 50 and hints["total"] == 1234, hints
assert "Grape pomace" not in terms, terms                      # already typed
counts = [h["count"] for h in hints["terms"]]
assert counts == sorted(counts, reverse=True) and min(counts) >= 2, counts
topic = next(h for h in hints["terms"] if h["term"] == "Phenolic compounds in plants")
assert topic["kind"] == "topic" and topic["count"] == 17, topic
try:
    ds.suggest_keywords("  \n ")
    raise AssertionError("empty hint request accepted")
except ds.SearchError as exc:
    assert exc.kind == "bad_query"

# --- keys file -----------------------------------------------------------------
keys = ds.save_keys({"scopus": " abcdefgh12345678 ", "wos": "wos-key-ABCD"})
assert keys["scopus"] == "abcdefgh12345678" and ds.keys_status(keys)["scopus"] == "••••5678"
assert Path(os.environ["ARTICLE_KEYS_FILE"]).is_file()
keys = ds.save_keys({"scopus": None})
assert keys["scopus"] == "" and keys["wos"] == "wos-key-ABCD"

# --- exports from the sites ------------------------------------------------------
wos_plain = (b"FN Clarivate Analytics Web of Science\nVR 1.0\nPT J\nTI Grape pomace as a source of\n"
             b"   polyphenols\nSO FOOD CHEMISTRY\nPY 2020\nDI 10.1016/J.FOODCHEM.2020.127316\nER\n\n"
             b"PT J\nTI No doi here\nPY 2019\nER\n\nEF\n")
recs, no_doi = ds.parse_export("savedrecs.txt", wos_plain)
assert [r["doi"] for r in recs] == ["10.1016/j.foodchem.2020.127316"] and no_doi == 1, (recs, no_doi)
assert recs[0]["title"] == "Grape pomace as a source of polyphenols"
ris = "TY  - JOUR\r\nTI  - Brewers spent grain\r\nPY  - 2021\r\nJO  - Foods\r\nDO  - 10.3390/foods10010001\r\nER  - \r\n"
recs, _ = ds.parse_export("scopus.ris", ris.encode())
assert recs[0]["doi"] == "10.3390/foods10010001" and recs[0]["journal"] == "Foods", recs
csv_text = '﻿"Authors","Title","Year","Source title","DOI"\n"A","One","2022","Molecules","10.3390/molecules27010246"\n"B","Two","2021","X",""\n'
recs, no_doi = ds.parse_export("scopus.csv", csv_text.encode("utf-8"))
assert len(recs) == 1 and no_doi == 1 and recs[0]["year"] == "2022", (recs, no_doi)
tab = "PT\tAU\tTI\tSO\tPY\tDI\nJ\tX\tTab title\tJ FOOD\t2018\t10.1111/ijfs.15211\n".encode("utf-16")
recs, _ = ds.parse_export("savedrecs.txt", tab)
assert recs[0]["doi"] == "10.1111/ijfs.15211", recs
try:
    ds.parse_export("savedrecs.xls", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 100)
    raise AssertionError(".xls accepted")
except ds.SearchError as exc:
    assert exc.kind == "bad_file"

# --- saving the list -----------------------------------------------------------------
inbox = TMP / "inbox"
records = ds.run_search({"keywords": "grape", "max_results": 20}, ["scopus"], GOOD)["records"]
first = ds.save_list(inbox, "grape pomace", records)
second = ds.save_list(inbox, "grape pomace", records[:3])
assert first.name.endswith("_grape_pomace.txt") and second.name.endswith("_grape_pomace_2.txt"), (first, second)
assert first.read_text(encoding="utf-8").split() == [r["doi"] for r in records]
detail = (inbox / "_подробно" / (first.stem + ".csv")).read_bytes()
assert detail.startswith(b"\xef\xbb\xbf") and "Название".encode() in detail
print("search tests: OK")
