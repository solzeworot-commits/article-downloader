# -*- coding: utf-8 -*-
# © 2026 Bucaneiro. Бесплатная программа; условия — в файле ЛИЦЕНЗИЯ.txt.
"""
A table of the downloaded articles for a review (v8.0).

pdf_text() takes the text of a PDF with pypdf (bundled with the program),
without the reference list at the end and cut to a size a model reads at a
modest price. The AI helper (ai_helper.extract_table) fills the user's
columns from it; an article without a PDF is read by its abstract. to_xlsx()
writes the table as an Excel file with zipfile alone: inline strings, a bold
header, wrapped text, no extra library.
"""
from __future__ import annotations

import csv
import os
import re
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

MAX_CHARS = 30000            # about 7–8 thousand tokens
MAX_PAGES = 40
REFERENCES_RE = re.compile(r"\n\s*(?:\d+\.?\s*)?(references|reference list|bibliography|literature cited|"
                           r"список литературы|литература|библиографический список)\s*\n", re.I)


def pdf_text(path: Path, max_chars: int = MAX_CHARS) -> str:
    """The article's text, the references cut off; "" when the PDF has no text layer."""
    from pypdf import PdfReader
    try:
        reader = PdfReader(str(path))
        parts = []
        for page in reader.pages[:MAX_PAGES]:
            try:
                parts.append(page.extract_text() or "")
            except Exception:
                continue
    except Exception:
        return ""
    text = "\n".join(parts)
    # The reference list: the last such heading in the second half of the text.
    cut = [m.start() for m in REFERENCES_RE.finditer(text) if m.start() > len(text) * 0.4]
    if cut:
        text = text[:cut[-1]]
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text).strip()
    return text[:max_chars]


def sanitized(doi: str) -> str:
    """The DOI as it appears in the names of the downloaded files (see article_downloader.sanitize_filename)."""
    return re.sub(r"\s+", "_", re.sub(r'[<>:"/\\|?*]+', "_", doi)).strip("._ ").lower()


def find_pdfs(dois: list[str], pdf_dir: Path, report: Path, items: list[dict]) -> dict:
    """{doi: path of its PDF} from this session's queue, Отчёт.csv and the names in the PDF folder."""
    found: dict[str, Path] = {}
    wanted = {d.lower(): d for d in dois}

    def take(query: str, file: str):
        doi = wanted.get(str(query or "").strip().lower())
        if doi and file and doi not in found and Path(file).is_file():
            found[doi] = Path(file)

    for item in items:
        if item.get("status") == "success":
            take(item.get("query"), item.get("file"))
    try:
        with report.open("r", encoding="utf-8-sig", newline="") as f:
            rows = list(csv.reader(f, delimiter=";"))
        for row in reversed(rows[1:]):          # the newest first
            if len(row) >= 4:
                take(row[1], row[3])
    except OSError:
        pass
    if len(found) < len(dois) and pdf_dir.is_dir():
        names = {p.name.lower(): p for p in pdf_dir.glob("*.pdf")}
        for doi in dois:
            if doi in found:
                continue
            key = sanitized(doi)
            match = next((p for n, p in names.items() if key in n), None)
            if match:
                found[doi] = match
    return found


# ---------------------------------------------------------------------------
# Excel
# ---------------------------------------------------------------------------

def _col(n: int) -> str:
    name = ""
    while n:
        n, r = divmod(n - 1, 26)
        name = chr(65 + r) + name
    return name


def _clean(text) -> str:
    # Characters XML 1.0 does not allow would break the file.
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(text or ""))


def to_xlsx(path: Path, header: list[str], rows: list[list[str]], widths: list[int] | None = None,
            sheet: str = "Table"):
    """One sheet: a bold frozen header row, wrapped text aligned to the top."""
    widths = widths or [24] * len(header)
    cols = "".join(f'<col min="{i}" max="{i}" width="{w}" customWidth="1"/>' for i, w in enumerate(widths, 1))
    lines = []
    for r, row in enumerate([header] + rows, 1):
        cells = "".join(
            f'<c r="{_col(c)}{r}" t="inlineStr" s="{1 if r == 1 else 2}"><is><t xml:space="preserve">'
            f"{escape(_clean(value))}</t></is></c>" for c, value in enumerate(row, 1))
        lines.append(f'<row r="{r}">{cells}</row>')
    sheet_xml = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                 '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                 '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" '
                 'state="frozen"/></sheetView></sheetViews>'
                 f"<cols>{cols}</cols><sheetData>{''.join(lines)}</sheetData>"
                 f'<autoFilter ref="A1:{_col(len(header))}{len(rows) + 1}"/></worksheet>')
    styles = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
              '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
              '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font>'
              '<font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
              '<fills count="2"><fill><patternFill patternType="none"/></fill>'
              '<fill><patternFill patternType="gray125"/></fill></fills>'
              '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
              '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
              '<cellXfs count="3"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
              '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1" applyAlignment="1">'
              '<alignment wrapText="1" vertical="top"/></xf>'
              '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1">'
              '<alignment wrapText="1" vertical="top"/></xf></cellXfs>'
              '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>')
    workbook = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                f'<sheets><sheet name="{escape(sheet[:31])}" sheetId="1" r:id="rId1"/></sheets>'
                f'<definedNames><definedName name="_xlnm._FilterDatabase" localSheetId="0" hidden="1">'
                f"'{escape(sheet[:31])}'!$A$1:${_col(len(header))}${len(rows) + 1}</definedName></definedNames>"
                "</workbook>")
    wb_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
               '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
               '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
               'worksheet" Target="worksheets/sheet1.xml"/>'
               '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
               'styles" Target="styles.xml"/></Relationships>')
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
            'officeDocument" Target="xl/workbook.xml"/></Relationships>')
    types = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
             '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
             '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
             '<Default Extension="xml" ContentType="application/xml"/>'
             '<Override PartName="/xl/workbook.xml" '
             'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
             '<Override PartName="/xl/worksheets/sheet1.xml" '
             'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
             '<Override PartName="/xl/styles.xml" '
             'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>')
    tmp = path.with_suffix(".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", types)
        z.writestr("_rels/.rels", rels)
        z.writestr("xl/workbook.xml", workbook)
        z.writestr("xl/_rels/workbook.xml.rels", wb_rels)
        z.writestr("xl/worksheets/sheet1.xml", sheet_xml)
        z.writestr("xl/styles.xml", styles)
    os.replace(tmp, path)
