"""
Probe of the real services without keys: python api_probe.py <portable/scripts>

Shows what Scopus and Web of Science answer to an invalid key (so the
"неверный ключ" / "нет доступа" split in doi_search.py can be checked) and
whether an OpenAlex search returns DOIs. Run by the Windows workflow.
"""
import sys

sys.path.insert(0, sys.argv[1])
import doi_search as ds  # noqa: E402

print("Scopus, invalid key:", ds.check_key("scopus", {"scopus": "0" * 32}))
print("WoS, invalid key:", ds.check_key("wos", {"wos": "0" * 40}))
result = ds.run_search({"keywords": "grape pomace\npolyphenols", "max_results": 5}, ["openalex"], {})
print("OpenAlex:", result["per_source"], [r["doi"] for r in result["records"]])
