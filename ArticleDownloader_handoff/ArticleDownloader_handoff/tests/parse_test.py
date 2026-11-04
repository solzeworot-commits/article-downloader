"""Checks of DOI extraction: python parse_test.py <portable/scripts>"""
import io
import sys
import zipfile

sys.path.insert(0, sys.argv[1])
import app  # noqa: E402

bib = """1. Smith J. J Chrom A 2001; 921: 1-10. doi:10.1016/S0021-9673(01)00123-4.
2. Ivanov (doi: 10.1021/jf4024195). Food chem.
3. https://doi.org/10.1007/s12393-026-09447-8, accessed 2026
@article{x, doi = {10.3390/antiox9111124}, title={Grape}}
DO  - 10.1080/10408398.2020.1234567
https://www.sciencedirect.com/science/article/pii/S0308814622030552
title: Пивная дробина как сырьё || 2019
Просто строка с названием статьи про виноградные выжимки
query
10.1016/S0021-9673(01)00123-4
"""
entries, ignored = app.parse_entries(bib, False)
assert entries == [
    "10.1016/S0021-9673(01)00123-4",
    "10.1021/jf4024195",
    "10.1007/s12393-026-09447-8",
    "10.3390/antiox9111124",
    "10.1080/10408398.2020.1234567",
    "https://www.sciencedirect.com/science/article/pii/S0308814622030552",
    "title: Пивная дробина как сырьё || 2019",
], entries
assert ignored == 2, ignored

entries, ignored = app.parse_entries(bib, True)
assert entries[-1] == "title: Просто строка с названием статьи про виноградные выжимки", entries
assert ignored == 1

buf = io.BytesIO()
with zipfile.ZipFile(buf, "w") as z:
    z.writestr("word/document.xml",
               "<w:document><w:body><w:p><w:r><w:t>Ref 1: 10.1016/j.foodchem.2020.127316</w:t></w:r></w:p>"
               "<w:p><w:r><w:t>Ref 2: doi 10.3390/molecules27010246.</w:t></w:r></w:p></w:body></w:document>")
assert app.parse_entries(app.file_to_text("refs.docx", buf.getvalue()), False) == (
    ["10.1016/j.foodchem.2020.127316", "10.3390/molecules27010246"], 0)

buf = io.BytesIO()
with zipfile.ZipFile(buf, "w") as z:
    z.writestr("xl/sharedStrings.xml",
               "<sst><si><t>DOI</t></si><si><t>10.1002/jsfa.12345</t></si>"
               "<si><t>https://doi.org/10.1111/ijfs.15211</t></si></sst>")
    z.writestr("xl/worksheets/sheet1.xml",
               '<worksheet><sheetData><row><c t="inlineStr"><is><t>10.1021/acs.jafc.1c01234</t></is></c></row>'
               "</sheetData></worksheet>")
entries, _ = app.parse_entries(app.file_to_text("refs.xlsx", buf.getvalue()), False)
assert entries == ["10.1002/jsfa.12345", "10.1111/ijfs.15211", "10.1021/acs.jafc.1c01234"], entries

text = app.file_to_text("list.txt", "10.1016/j.x.2020.1\nстатья".encode("cp1251"))
assert app.parse_entries(text, False) == (["10.1016/j.x.2020.1"], 1)
print("parse tests: OK")
