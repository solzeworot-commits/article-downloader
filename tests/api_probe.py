"""
Probe of the real services without keys: python api_probe.py <portable/scripts>

Shows what Scopus and Web of Science answer to an invalid key (so the
"неверный ключ" / "нет доступа" split in doi_search.py can be checked),
whether OpenAlex search and keyword hints return data, and what the DOI
lookup of the review module gets from OpenAlex and Crossref. Each probe runs
on its own: one failing service does not hide the others. Run by the Windows
workflow.
"""
import sys
import traceback

sys.path.insert(0, sys.argv[1])
import doi_search as ds  # noqa: E402
import review as rv  # noqa: E402


def probe(title, action):
    try:
        print(title, action())
    except Exception as exc:
        print(title, "ERROR", type(exc).__name__, getattr(exc, "message", exc))
        traceback.print_exc(limit=1)


DOIS = ["10.3390/foods9111627", "10.1016/s0308-8146(98)00245-3"]
probe("Crossref lookup:", lambda: {d: rv.gost(dict(m, doi=d)) for d, m in rv.fetch_crossref(DOIS).items()})
probe("Scopus, invalid key:", lambda: ds.check_key("scopus", {"scopus": "0" * 32}))
probe("WoS, invalid key:", lambda: ds.check_key("wos", {"wos": "0" * 40}))
probe("OpenAlex search:", lambda: ds.run_search({"keywords": "grape pomace\npolyphenols", "max_results": 5}, ["openalex"], {})["per_source"])
probe("OpenAlex hints:", lambda: [(h["term"], h["count"]) for h in ds.suggest_keywords("grape pomace\npolyphenols", {})["terms"][:8]])
probe("Europe PMC abstracts:", lambda: {d: len(t) for d, t in rv.fetch_europepmc_abstracts(DOIS).items()})
probe("OpenAlex lookup:", lambda: {d: (m["year"], len(m["abstract"])) for d, m in rv.fetch_openalex(DOIS, {}).items()})
# v8.3: the Wakefield 1998 Lancet article is retracted; the citation search on one article
probe("OpenAlex retracted:", lambda: rv.fetch_retracted(["10.1016/s0140-6736(97)11096-0", DOIS[0]], {}))
probe("OpenAlex citations:", lambda: (lambda r: (r["seeds"], r["checked"], len(r["candidates"]), r["error"],
                                                 [(c["doi"], c["links"], c["cited_by"]) for c in r["candidates"][:5]]))(
    rv.snowball([DOIS[0]], set(), {}, per_article=50, limit=10)))
