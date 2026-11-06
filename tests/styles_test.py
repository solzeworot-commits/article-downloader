"""Reference styles (v7.8): python styles_test.py <portable/scripts>

Exact entries of the seven built-in styles for three typical articles (three
authors with page range, two authors with an article number, eight authors),
the et al. rules, the order and labels of the list, the Word file, the index
of journal styles and the journal style by a fake doi.org.
"""
import json
import os
import sys
import tempfile
import threading
import xml.etree.ElementTree as ET
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = 8021
os.environ.update(ARTICLE_DOI_URL=f"http://127.0.0.1:{PORT}/", NO_PROXY="127.0.0.1,localhost")
sys.path.insert(0, sys.argv[1])
import cite_styles as cs  # noqa: E402

R1 = {"doi": "10.1016/j.foodchem.2019.01.001", "title": "Ultrasound-assisted extraction of polyphenols from grape pomace",
      "journal": "Food Chemistry", "journal_abbr": "Food Chem.", "year": "2019", "volume": "284", "issue": "",
      "pages": "11-17", "authors": [{"family": "Alves", "given": "Fernanda Maria"}, {"family": "Smith", "given": "John P."},
                                    {"family": "Lee", "given": "C."}]}
R2 = {"doi": "10.3390/molecules26051234", "title": "Grape marc as a source of anthocyanins?", "journal": "Molecules",
      "journal_abbr": "Molecules", "year": "2021", "volume": "26", "issue": "5", "pages": "1234",
      "authors": [{"family": "Ivanov", "given": "Ivan"}, {"family": "Petrova", "given": "Anna-Maria"}]}
R3 = {"doi": "10.1021/acs.jafc.0c01234", "title": "Phenolic compounds in winery waste",
      "journal": "Journal of Agricultural and Food Chemistry", "journal_abbr": "J. Agric. Food Chem.", "year": "2020",
      "volume": "68", "issue": "12", "pages": "3456-3467",
      "authors": [{"family": f"Author{i}", "given": "Ann B."} for i in range(1, 9)]}

EXPECTED = {
    "apa": [
        "Alves, F. M., Smith, J. P., & Lee, C. (2019). Ultrasound-assisted extraction of polyphenols from grape pomace. "
        "Food Chemistry, 284, 11–17. https://doi.org/10.1016/j.foodchem.2019.01.001",
        "Ivanov, I., & Petrova, A.-M. (2021). Grape marc as a source of anthocyanins? Molecules, 26(5), Article 1234. "
        "https://doi.org/10.3390/molecules26051234",
    ],
    "harvard": [
        "Alves, F.M., Smith, J.P., Lee, C., 2019. Ultrasound-assisted extraction of polyphenols from grape pomace. "
        "Food Chem. 284, 11–17. https://doi.org/10.1016/j.foodchem.2019.01.001",
        "Ivanov, I., Petrova, A.-M., 2021. Grape marc as a source of anthocyanins? Molecules 26, 1234. "
        "https://doi.org/10.3390/molecules26051234",
    ],
    "vancouver": [
        "Alves FM, Smith JP, Lee C. Ultrasound-assisted extraction of polyphenols from grape pomace. Food Chem. "
        "2019;284:11-17. doi:10.1016/j.foodchem.2019.01.001",
        "Ivanov I, Petrova AM. Grape marc as a source of anthocyanins? Molecules. 2021;26(5):1234. "
        "doi:10.3390/molecules26051234",
    ],
    "acs": [
        "Alves, F. M.; Smith, J. P.; Lee, C. Ultrasound-assisted extraction of polyphenols from grape pomace. Food Chem. "
        "2019, 284, 11–17. https://doi.org/10.1016/j.foodchem.2019.01.001.",
        "Ivanov, I.; Petrova, A.-M. Grape marc as a source of anthocyanins? Molecules 2021, 26 (5), 1234. "
        "https://doi.org/10.3390/molecules26051234.",
    ],
    "ieee": [
        "F. M. Alves, J. P. Smith, and C. Lee, “Ultrasound-assisted extraction of polyphenols from grape pomace,” "
        "Food Chem., vol. 284, pp. 11–17, 2019, doi: 10.1016/j.foodchem.2019.01.001.",
        "I. Ivanov and A.-M. Petrova, “Grape marc as a source of anthocyanins?” Molecules, vol. 26, no. 5, "
        "Art. no. 1234, 2021, doi: 10.3390/molecules26051234.",
    ],
    "chicago": [
        "Alves, Fernanda Maria, John P. Smith, and C. Lee. 2019. “Ultrasound-assisted extraction of polyphenols from "
        "grape pomace.” Food Chemistry 284: 11–17. https://doi.org/10.1016/j.foodchem.2019.01.001.",
        "Ivanov, Ivan, and Anna-Maria Petrova. 2021. “Grape marc as a source of anthocyanins?” Molecules 26 (5): 1234. "
        "https://doi.org/10.3390/molecules26051234.",
    ],
    "nature": [
        "Alves, F. M., Smith, J. P. & Lee, C. Ultrasound-assisted extraction of polyphenols from grape pomace. "
        "Food Chem. 284, 11–17 (2019).",
        "Ivanov, I. & Petrova, A.-M. Grape marc as a source of anthocyanins? Molecules 26, 1234 (2021).",
    ],
}
for style, (first, second) in EXPECTED.items():
    fn = cs.STYLES[style][2]
    assert cs.plain(fn(R1)) == first, (style, cs.plain(fn(R1)))
    assert cs.plain(fn(R2)) == second, (style, cs.plain(fn(R2)))

# et al. rules for eight authors
assert cs.plain(cs.vancouver(R3)).startswith("Author1 AB, Author2 AB, Author3 AB, Author4 AB, Author5 AB, Author6 AB, et al. ")
assert cs.plain(cs.ieee(R3)).startswith("A. B. Author1 et al., “Phenolic")
assert cs.plain(cs.nature(R3)).startswith("Author1, A. B. et al. Phenolic")
assert cs.plain(cs.chicago(R3)).startswith("Author1, Ann B., Ann B. Author2, Ann B. Author3, et al. 2020.")
assert cs.plain(cs.apa(R3)).startswith("Author1, A. B., Author2, A. B.,") and "& Author8, A. B. (2020)" in cs.plain(cs.apa(R3))
r22 = dict(R1, authors=[{"family": f"F{i}", "given": "A."} for i in range(1, 23)])
assert "F19, A., . . . F22, A. (2019)" in cs.plain(cs.apa(r22)), cs.plain(cs.apa(r22))

# italics and bold where the style has them
assert cs.html(cs.apa(R1)).count("<i>") == 2 and "<i>Food Chemistry</i>, <i>284</i>" in cs.html(cs.apa(R1))
assert "<i>Food Chem.</i> <b>2019</b>, <i>284</i>" in cs.html(cs.acs(R1))
assert "<i>Food Chem.</i> <b>284</b>" in cs.html(cs.nature(R1))
assert "<i>" not in cs.html(cs.harvard(R1)) and "&amp;" in cs.html(cs.apa(R1))

# a record without authors, year or pages still reads
bare = {"doi": "10.1/x", "title": "Only a title", "journal": "J"}
for style in EXPECTED:
    assert cs.plain(cs.STYLES[style][2](bare)), style
assert cs.plain(cs.apa(bare)).startswith("Only a title. (n.d.).")

# list order and labels
items = cs.entries([R1, R2, R3], "ieee", alphabetical=False)
assert [i["label"] for i in items] == ["[1] ", "[2] ", "[3] "] and items[1]["text"].startswith("I. Ivanov")
items = cs.entries([R2, R3, R1], "apa", alphabetical=True)
assert [i["label"] for i in items] == ["", "", ""] and [i["text"][:6] for i in items] == ["Alves,", "Author", "Ivanov"]
assert [i["label"] for i in cs.entries([R1], "acs", False)] == ["(1) "]
gost = cs.entries([R1, R2], "gost", alphabetical=True)
assert gost[0]["label"] == "1. " and gost[0]["text"].startswith("Alves, F. M. Ultrasound-assisted")

# the Word file
with tempfile.TemporaryDirectory() as tmp:
    path = Path(tmp) / "list.docx"
    cs.to_docx(path, "Reference list — APA 7", cs.entries([R1, R2], "apa", True))
    with zipfile.ZipFile(path) as z:
        assert {"[Content_Types].xml", "_rels/.rels", "word/document.xml"} <= set(z.namelist())
        root = ET.fromstring(z.read("word/document.xml"))
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    paras = root.findall(".//w:p", ns)
    assert len(paras) == 3
    italic = [r.find("w:t", ns).text for r in paras[1].findall("w:r", ns) if r.find("w:rPr/w:i", ns) is not None]
    assert italic == ["Food Chemistry", "284"], italic
    text = "".join(t.text for t in paras[2].findall(".//w:t", ns))
    assert text == EXPECTED["apa"][1], text

# the index of journal styles
assert len(cs.csl_index()) > 10000
assert cs.csl_search("food chemistry")[0]["id"] == "food-chemistry"
fc = cs.csl_find("food-chemistry")
assert fc["parent"] == "apa" and fc["format"] == "author-date", fc
assert cs.csl_find("ieee")["format"] == "numeric"
assert cs.csl_search("") == [] and cs.csl_search("zzzz-no-such-journal") == []


# the journal style by a fake doi.org
class Fake(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        accept = self.headers.get("Accept", "")
        if self.path.endswith("missing"):
            self.send_response(404)
            self.end_headers()
            return
        assert "text/x-bibliography" in accept and "style=" in accept, accept
        style = accept.split("style=")[1].split(";")[0]
        body = (f"1. Formatted by {style} for {self.path[1:]}." if style == "ieee"
                else f"Formatted by {style} for {self.path[1:]}.").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/x-bibliography; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


server = ThreadingHTTPServer(("127.0.0.1", PORT), Fake)
threading.Thread(target=server.serve_forever, daemon=True).start()
assert cs.csl_format("10.1016/x", "apa", numeric=False) == "Formatted by apa for 10.1016/x."
assert cs.csl_format("10.1109/y", "ieee", numeric=True) == "Formatted by ieee for 10.1109/y.", "the number is cut"
try:
    cs.csl_format("10.1/missing", "apa", numeric=False)
    raise AssertionError("a missing DOI was formatted")
except cs.StyleError as exc:
    assert "404" in str(exc)
recs = [dict(R1, csl={"food-chemistry": "Cached text."}), dict(R2)]
items = cs.csl_entries(recs, fc, alphabetical=True)
assert items[0]["text"] == "Cached text." and not items[0]["fallback"] and items[0]["label"] == ""
assert items[1]["fallback"] and items[1]["text"].startswith("Ivanov, I., & Petrova"), "the closest built-in style"
numeric = cs.csl_entries(recs, cs.csl_find("ieee"), alphabetical=False)
assert [i["label"] for i in numeric] == ["1. ", "2. "] and numeric[1]["text"].startswith("Ivanov I")
server.shutdown()
print("styles tests: OK (7 built-in styles, journal styles)")
