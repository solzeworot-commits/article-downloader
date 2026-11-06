# © 2026 Bucaneiro. Бесплатная программа; условия — в файле LICENSE.
"""
Index of journal reference styles for the "Список литературы" window:

    python tools/make_csl_index.py <styles folder> portable/scripts/csl_styles.json.gz

<styles folder> is a copy of the Citation Style Language styles repository
(github.com/citation-style-language/styles), for example the
citeproc_styles/styles folder of the citeproc-py-styles package on PyPI
(pip download --no-deps citeproc-py-styles, then unzip the wheel). Only the
id, the title, the citation format and the independent parent of every style
are kept; the styles themselves are not copied. The style metadata is
licensed under CC BY-SA 3.0 by the CSL project, so is this index.
"""
import gzip
import html
import json
import re
import sys
from pathlib import Path

TITLE_RE = re.compile(r"<title>(.*?)</title>", re.S)
FORMAT_RE = re.compile(r'citation-format="([a-z-]+)"')
PARENT_RE = re.compile(r'href="[^"]*/styles/([^"/]+)"\s+rel="independent-parent"|rel="independent-parent"\s+href="[^"]*/styles/([^"/]+)"')


def main():
    src, out = Path(sys.argv[1]), Path(sys.argv[2])
    styles, formats = [], {}
    files = sorted(src.glob("*.csl")) + sorted((src / "dependent").glob("*.csl"))
    for path in files:
        text = path.read_text(encoding="utf-8", errors="replace")
        head = text[: text.find("</info>") + 7] if "</info>" in text else text[:4000]
        title = TITLE_RE.search(head)
        if not title:
            continue
        parent = PARENT_RE.search(head)
        parent_id = (parent.group(1) or parent.group(2)) if parent else ""
        fmt = FORMAT_RE.search(head)
        formats[path.stem] = fmt.group(1) if fmt else ""
        styles.append([path.stem, html.unescape(re.sub(r"\s+", " ", title.group(1)).strip()),
                       formats[path.stem], parent_id])
    for style in styles:                    # a dependent style without its own format takes the parent's
        if not style[2] and style[3]:
            style[2] = formats.get(style[3], "")
    styles.sort(key=lambda s: s[1].lower())
    data = {"source": "Citation Style Language styles repository, CC BY-SA 3.0",
            "count": len(styles), "styles": styles}
    with gzip.open(out, "wt", encoding="utf-8", compresslevel=9) as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    print(f"{len(styles)} styles -> {out} ({out.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
