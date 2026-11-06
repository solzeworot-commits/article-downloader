"""
English interface: python i18n_test.py <portable/scripts>

Translates ui/index.html as the program serves it and checks that no Russian
is left apart from real folder and file names, that every piece in
ui/lang/en.json still occurs in the page (no stale entries), that every
tr() text in the Python code has an English template with the same
{fields}, and that the Russian page is served unchanged. Run by the Windows
workflow.
"""
import ast
import json
import re
import sys
from pathlib import Path

SCRIPTS = Path(sys.argv[1])
sys.path.insert(0, str(SCRIPTS))
import i18n  # noqa: E402

CYR = re.compile(r"[А-Яа-яЁё]")
# Real names on disk and the language button keep their Russian text.
ALLOWED = ["Список литературы", "_архив", "Текст : электронный", "ЛИЦЕНЗИЯ", "Русский"]


def fail(what):
    raise SystemExit("i18n test FAILED: " + what)


page = (SCRIPTS / "ui" / "index.html").read_text(encoding="utf-8")
data = json.loads(i18n.LANG_FILE.read_text(encoding="utf-8"))

i18n.set_lang("ru")
if i18n.translate_page(page) != page:
    fail("the Russian page must be served unchanged")

i18n.set_lang("en")
english = i18n.translate_page(page)
if '<html lang="en">' not in english:
    fail("lang attribute")
left = english
for name in ALLOWED:
    left = left.replace(name, "")
rest = sorted({line.strip()[:120] for line in left.splitlines() if CYR.search(line)})
if rest:
    fail("Russian left on the English page:\n  " + "\n  ".join(rest[:40]))

stale = [k for k in data["page"] if k not in page]
if stale:
    fail("pieces no longer in the page: " + "; ".join(stale[:20]))

missing = []
for name in ("app.py", "doi_search.py", "review.py", "support.py", "ai_helper.py", "cite_styles.py", "pdf_table.py"):
    tree = ast.parse((SCRIPTS / name).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "tr" and node.args:
            arg = node.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                if arg.value not in data["messages"]:
                    missing.append(f"{name}: {arg.value[:80]}")
if missing:
    fail("tr() texts without English:\n  " + "\n  ".join(missing))
for ru, en in data["messages"].items():
    if sorted(re.findall(r"\{\w+\}", ru)) != sorted(re.findall(r"\{\w+\}", en)):
        fail(f"fields differ: {ru!r} / {en!r}")
    if CYR.search(en):
        fail(f"Russian in an English message: {en!r}")

print(f"i18n tests: OK ({len(data['page'])} page pieces, {len(data['messages'])} messages)")
