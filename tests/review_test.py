"""Checks of the literature review module (v7.0): python review_test.py <portable/scripts>"""
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
TMP = Path(tempfile.mkdtemp(prefix="ad_review_"))
os.environ.update(
    ARTICLE_OPENALEX_URL="http://127.0.0.1:8012/openalex",
    ARTICLE_CROSSREF_URL="http://127.0.0.1:8012/crossref",
    ARTICLE_EUROPEPMC_URL="http://127.0.0.1:8012/europepmc",
    NO_PROXY="127.0.0.1,localhost",
)
sys.path.insert(0, sys.argv[1])
sys.path.insert(0, str(HERE))
import review as rv  # noqa: E402
import fake_api  # noqa: E402

fake_api.start(8012)

# --- GOST R 7.0.100-2018 ------------------------------------------------------------
two = {"doi": "10.1016/j.foodchem.2020.126134", "title": "Grape pomace as a source of polyphenols.",
       "journal": "Food Chemistry", "year": "2020", "volume": "312", "pages": "126134",
       "authors": [{"family": "Smith", "given": "John A."}, {"family": "Brown", "given": "Ann"}]}
assert rv.gost(two) == ("Smith, J. A. Grape pomace as a source of polyphenols / J. A. Smith, A. Brown "
                        "// Food Chemistry. – 2020. – Vol. 312. – Art. 126134. – DOI: 10.1016/j.foodchem.2020.126134."), rv.gost(two)
four_ru = {"doi": "10.31857/s0044", "title": "Пивная дробина как сырьё", "journal": "Пищевая промышленность",
           "year": "2019", "volume": "5", "issue": "2", "pages": "10-20",
           "authors": [{"family": f, "given": g} for f, g in (("Иванов", "Иван Иванович"), ("Петров", "П. П."),
                                                                ("Сидоров", "С."), ("Кузнецов", "К. К."))]}
assert rv.gost(four_ru) == ("Пивная дробина как сырьё / И. И. Иванов, П. П. Петров, С. Сидоров, К. К. Кузнецов "
                            "// Пищевая промышленность. – 2019. – Т. 5, № 2. – С. 10–20. – DOI: 10.31857/s0044."), rv.gost(four_ru)
six = dict(two, authors=[{"family": f"Author{i}", "given": "Jean-Paul"} for i in range(6)], pages="246-260")
assert " / J.-P. Author0, J.-P. Author1, J.-P. Author2 [et al.] // " in rv.gost(six), rv.gost(six)
assert rv.gost_list([two, four_ru], alphabetical=True)[0].startswith("Пивная"), "Russian sources come first"
assert rv.split_name("Ludwig van Beethoven") == {"family": "van Beethoven", "given": "Ludwig"}

# --- RIS / BibTeX ---------------------------------------------------------------------
ris = rv.to_ris([two])
assert "AU  - Smith, John A.\r\nAU  - Brown, Ann" in ris and "DO  - 10.1016/j.foodchem.2020.126134" in ris and ris.rstrip().endswith("ER  -")
bib = rv.to_bibtex([two, two])
assert "@article{smith2020grape," in bib and "@article{smith2020grapea," in bib and "author = {Smith, John A. and Brown, Ann}" in bib, bib

# --- lookup by DOI (fake OpenAlex + Crossref) --------------------------------------------
# The fake knows no "*.7"; for "*.6" OpenAlex has no abstract and Europe PMC has.
dois = [f"10.9999/scopus.{i}" for i in range(10, 16)] + ["10.9999/scopus.6", "10.9999/scopus.7"]
meta, error = rv.enrich(dois, {})
assert not error, error
assert meta["10.9999/scopus.10"]["found"] and not meta["10.9999/scopus.7"]["found"]
m = meta["10.9999/scopus.10"]
assert m["authors"] == [{"family": "Petrova", "given": "Anna"}, {"family": "Smith", "given": "John A."}], m["authors"]
assert m["abstract"].startswith("This study examines 10.9999/scopus.10") and m["pages"] == "45-56" and m["year"] == "2021", m
assert meta["10.9999/scopus.6"]["abstract"] == "Europe PMC abstract of 10.9999/scopus.6 on grape pomace.", meta["10.9999/scopus.6"]

# --- review file and PRISMA ---------------------------------------------------------------
inbox = TMP / "inbox"
inbox.mkdir()
data = rv.load_review(inbox, "list.txt", dois)
for d, decision, reason in ((dois[0], "include", ""), (dois[1], "include", ""), (dois[2], "exclude", "не по теме"),
                            (dois[3], "exclude", ""), (dois[4], "maybe", "")):
    data["records"][d].update(meta[d], decision=decision, reason=reason)
rv.save_review(inbox, data)
again = rv.load_review(inbox, "list.txt", dois)
assert again["records"][dois[2]]["reason"] == "не по теме"
rv.save_meta(inbox, "list.txt", {"per_source": {"Scopus": 60, "Web of Science": 40}, "duplicates": 8, "no_doi": 16, "deselected": 1})
p = rv.prisma(again, rv.load_meta(inbox, "list.txt"), downloaded={dois[0]})
assert (p["identified"], p["duplicates"], p["no_doi"], p["screened"], p["excluded"], p["maybe"], p["included"],
        p["undecided"], p["retrieved"], p["not_retrieved"]) == (100, 8, 16, 8, 2, 1, 2, 3, 1, 1), p
assert p["reasons"] == {"не по теме": 1, "без причины": 1}, p["reasons"]
print("review tests: OK")
