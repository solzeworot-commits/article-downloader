"""The table of the articles (v8.0): python table_test.py <portable/scripts>

A real PDF made here (text layer, a reference list to cut off), finding the
PDF of a DOI in the session queue, in Отчёт.csv and by the file name, and the
Excel file (checked with openpyxl when it is installed).
"""
import csv
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

sys.path.insert(0, sys.argv[1])
import pdf_table as pt  # noqa: E402


def make_pdf(path: Path, lines: list[str]):
    """A one-page PDF with Helvetica text, one line per Tj."""
    ops = ["BT", "/F1 11 Tf", "50 780 Td", "14 TL"]
    for line in lines:
        ops.append("(" + line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") + ") Tj T*")
    ops.append("ET")
    stream = "\n".join(ops).encode("latin-1")
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R "
            b"/Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for n, obj in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{n} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    path.write_bytes(bytes(out))


tmp = Path(tempfile.mkdtemp(prefix="ad_table_"))
pdf_dir = tmp / "pdf"
pdf_dir.mkdir()
body = ["Ultrasound-assisted extraction of polyphenols from grape pomace", "Abstract",
        "Grape pomace was extracted with 50% aqueous ethanol.", "Methods",
        "Ultrasound 40 kHz, 60 C, 30 min; response surface methodology.", "Results",
        "The best yield was 45 mg GAE/g dry weight.", "Conclusions", "Ultrasound doubles the yield.",
        "References", "1. Smith J. Old paper. Food Chem. 2001;1:1-2.", "2. Lee K. Another one. 2005."]
p1 = pdf_dir / "Elsevier_10.1016_j.foodchem.2019.01.001.pdf"
make_pdf(p1, body)
text = pt.pdf_text(p1)
assert "45 mg GAE/g" in text and "aqueous ethanol" in text, text
assert "Old paper" not in text and "Another one" not in text, "the reference list is cut off"
assert pt.pdf_text(tmp / "missing.pdf") == ""
broken = tmp / "broken.pdf"
broken.write_bytes(b"%PDF-1.4 not really")
assert pt.pdf_text(broken) == ""
assert len(pt.pdf_text(p1, max_chars=40)) == 40

# finding the PDF of a DOI
p2 = pdf_dir / "Wiley_10.1002_jsfa.1234.pdf"
make_pdf(p2, ["Second article"])
p3 = pdf_dir / "Springer_10.1007_s00217-020-0001-1.pdf"
make_pdf(p3, ["Third article"])
report = tmp / "Отчёт.csv"
with report.open("w", encoding="utf-8-sig", newline="") as f:
    w = csv.writer(f, delimiter=";")
    w.writerow(["Время", "Запрос", "Результат", "Файл", "Сообщение"])
    w.writerow(["2026-11-06", "10.1002/JSFA.1234", "скачано", str(p2), ""])
items = [{"query": "10.1016/j.foodchem.2019.01.001", "status": "success", "file": str(p1)}]
dois = ["10.1016/j.foodchem.2019.01.001", "10.1002/jsfa.1234", "10.1007/s00217-020-0001-1", "10.9999/none"]
found = pt.find_pdfs(dois, pdf_dir, report, items)
assert found == {dois[0]: p1, dois[1]: p2, dois[2]: p3}, found
assert pt.find_pdfs(dois, tmp / "nope", tmp / "nope.csv", []) == {}

# the Excel file
xlsx = tmp / "table.xlsx"
header = ["№", "Авторы", "Название", "Метод", "Результаты"]
rows = [["1", "Alves & Smith", "Ultrasound <extraction> & co", "УЗ 40 кГц", "45 мг GAE/г"],
        ["2", "Ivanov", "Grape marc\x0b with a control char", "—", "—"]]
pt.to_xlsx(xlsx, header, rows, [5, 20, 40, 30, 30], sheet="Таблица")
with zipfile.ZipFile(xlsx) as z:
    names = set(z.namelist())
    assert {"[Content_Types].xml", "xl/workbook.xml", "xl/worksheets/sheet1.xml", "xl/styles.xml"} <= names
    sheet = ET.fromstring(z.read("xl/worksheets/sheet1.xml"))
ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
cells = [c.find("m:is/m:t", ns).text for c in sheet.findall(".//m:c", ns)]
assert cells[:5] == header and "Ultrasound <extraction> & co" in cells and "45 мг GAE/г" in cells, cells
assert "Grape marc with a control char" in cells, "control characters are dropped"
assert pt._col(1) == "A" and pt._col(26) == "Z" and pt._col(27) == "AA" and pt._col(53) == "BA"
try:
    import openpyxl
    wb = openpyxl.load_workbook(xlsx)
    ws = wb.active
    assert ws.title == "Таблица" and ws["D2"].value == "УЗ 40 кГц" and ws.max_row == 3, (ws.title, ws["D2"].value)
    checked = "and opened with openpyxl"
except ImportError:
    checked = "(openpyxl is not installed here)"
print(f"table tests: OK {checked}")
