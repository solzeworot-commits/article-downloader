# © 2026 Bucaneiro. Бесплатная программа; условия — в файле LICENSE.
"""
Materials for registering the program in Rospatent (программа для ЭВМ):

    python tools/rospatent_package.py OUT_DIR [--author "Фамилия Имя Отчество"]
                                       [--fragment-pages 25] [--chrome PATH]

Writes into OUT_DIR (keep it outside the repository: it holds the author's
real name):
  Исходный_текст_полный.html / .pdf  — the whole source listing, numbered;
  Исходный_текст_фрагмент.pdf        — the first and last N pages of it;
  Реферат.txt                        — draft of the abstract for the form;
  Как_подать.txt                     — the steps.
The PDF needs Playwright and Chromium (--chrome, or the bundled browser).
"""
import argparse
import html
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORTABLE = ROOT / "portable"
FILES = [
    "scripts/app.py",
    "scripts/doi_search.py",
    "scripts/review.py",
    "scripts/support.py",
    "scripts/ai_helper.py",
    "scripts/i18n.py",
    "scripts/article_downloader.py",
    "scripts/ui/index.html",
    "СКАЧАТЬ СТАТЬИ.bat",
]
TITLE = "Загрузчик статей"
PSEUDONYM = "Bucaneiro"


def version() -> str:
    for line in (PORTABLE / "scripts" / "app.py").read_text(encoding="utf-8").splitlines():
        if line.startswith("APP_VERSION"):
            return line.split('"')[1]
    return ""


def listing_html(author: str) -> str:
    parts = [f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<title>{TITLE} — исходный текст</title>
<style>
  @page {{ size: A4; margin: 16mm 14mm 16mm 16mm; }}
  body {{ font-family: "DejaVu Sans", Arial, sans-serif; font-size: 10pt; }}
  .title {{ page-break-after: always; text-align: center; padding-top: 70mm; }}
  .title h1 {{ font-size: 18pt; margin-bottom: 8mm; }}
  h2 {{ font-size: 11pt; margin: 6mm 0 2mm; page-break-after: avoid; }}
  pre {{ font-family: "DejaVu Sans Mono", Consolas, monospace; font-size: 7.6pt; line-height: 1.25;
        white-space: pre-wrap; word-break: break-all; margin: 0; }}
  .n {{ color: #888; }}
</style></head><body>
<div class="title">
  <h1>Программа для ЭВМ «{TITLE}»</h1>
  <p>Версия {version()}</p>
  <p>Автор и правообладатель: {html.escape(author)} (псевдоним {PSEUDONYM})</p>
  <p>Исходный текст программы</p>
  <p>{date.today().year}</p>
</div>"""]
    for name in FILES:
        text = (PORTABLE / name).read_text(encoding="utf-8").replace("\r\n", "\n")
        lines = text.rstrip("\n").split("\n")
        width = len(str(len(lines)))
        body = "\n".join(f'<span class="n">{i:>{width}}</span>  {html.escape(line)}'
                         for i, line in enumerate(lines, 1))
        parts.append(f"<h2>Файл {html.escape(name)} ({len(lines)} строк)</h2><pre>{body}</pre>")
    parts.append("</body></html>")
    return "\n".join(parts)


def to_pdf(src: Path, dest: Path, chrome: str | None):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=chrome) if chrome else p.chromium.launch()
        page = browser.new_page()
        page.goto(src.resolve().as_uri())
        page.pdf(path=str(dest), format="A4", print_background=False, display_header_footer=True,
                 header_template="<span></span>",
                 footer_template=f'<div style="font-size:8px;width:100%;text-align:center">'
                                 f'{TITLE}, исходный текст — лист <span class="pageNumber"></span> '
                                 f'из <span class="totalPages"></span></div>',
                 margin={"top": "16mm", "bottom": "16mm", "left": "16mm", "right": "14mm"})
        browser.close()


def fragment(full: Path, dest: Path, each: int) -> int:
    from pypdf import PdfReader, PdfWriter
    reader = PdfReader(str(full))
    total = len(reader.pages)
    keep = list(range(total)) if total <= 2 * each else [*range(each), *range(total - each, total)]
    writer = PdfWriter()
    for i in keep:
        writer.add_page(reader.pages[i])
    with dest.open("wb") as f:
        writer.write(f)
    return total


def referat(author: str) -> str:
    size_kb = round(sum((PORTABLE / n).stat().st_size for n in FILES) / 1024)
    return f"""РЕФЕРАТ (черновик для заявления)

Название программы: «{TITLE}»
Автор: {author} (псевдоним {PSEUDONYM})
Правообладатель: {author}
Версия: {version()}

Аннотация:
Программа для поиска и получения научных статей по идентификаторам DOI.
Формирует списки DOI по ключевым словам через Scopus Search API, Web of Science
Starter API и OpenAlex, помогает отобрать статьи по аннотациям, скачивает PDF
через выделенный браузер с доступом по подписке организации пользователя,
составляет список литературы по ГОСТ Р 7.0.100-2018, файлы RIS и BibTeX и
схему отбора PRISMA. Работает без установки из папки на компьютере с Windows.
Область применения: научная и учебная работа, подготовка обзоров литературы.

Тип ЭВМ: IBM PC-совместимый персональный компьютер
Язык: Python 3.13, JavaScript, HTML, CSS
ОС: Windows 10, Windows 11
Объём исходного текста: {size_kb} КБ ({len(FILES)} файлов)
"""


HOWTO = """КАК ПОДАТЬ ЗАЯВКУ В РОСПАТЕНТ (программа для ЭВМ)

Перед подачей сверьте всё с официальным руководством ФИПС по услуге регистрации
программ для ЭВМ (new.fips.ru → «Заявителям» → «Программы для ЭВМ и базы данных»):
требования меняются.

1. Где подать: на сайте ФИПС или через Госуслуги (нужна квалифицированная
   электронная подпись) либо на бумаге в Роспатент.
2. Что приложить:
   • заявление о государственной регистрации программы для ЭВМ;
   • реферат — черновик в файле «Реферат.txt»;
   • депонируемые материалы — исходный текст. Обычно подают фрагмент:
     «Исходный_текст_фрагмент.pdf» (первые и последние листы). Сколько листов
     нужно, проверьте в руководстве ФИПС; полный текст тоже есть в папке;
   • согласие автора на обработку персональных данных и на указание сведений
     о нём;
   • документ об уплате госпошлины (размер — на сайте ФИПС, глава 25.3 НК РФ).
3. Псевдоним: в заявлении указываются настоящие ФИО. Связь с псевдонимом
   Bucaneiro видна из депонируемого текста: в начале каждого файла стоит
   «© 2026 Bucaneiro», на титульном листе — ФИО и псевдоним.
4. Если вы постоянно живёте за пределами России, дела с Роспатентом, возможно,
   придётся вести через патентного поверенного (ст. 1247 ГК РФ) — уточните.
5. Храните доказательства авторства: историю изменений на GitHub, задания и
   переписку о разработке, архивы версий с датами.
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--author", default="[Фамилия Имя Отчество]")
    ap.add_argument("--fragment-pages", type=int, default=25)
    ap.add_argument("--chrome")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    full_html = out / "Исходный_текст_полный.html"
    full_html.write_text(listing_html(args.author), encoding="utf-8")
    (out / "Реферат.txt").write_bytes(b"\xef\xbb\xbf" + referat(args.author).replace("\n", "\r\n").encode("utf-8"))
    (out / "Как_подать.txt").write_bytes(b"\xef\xbb\xbf" + HOWTO.replace("\n", "\r\n").encode("utf-8"))
    try:
        full_pdf = out / "Исходный_текст_полный.pdf"
        to_pdf(full_html, full_pdf, args.chrome)
        total = fragment(full_pdf, out / "Исходный_текст_фрагмент.pdf", args.fragment_pages)
        print(f"PDF: {total} листов, фрагмент: {min(total, 2 * args.fragment_pages)} листов")
    except ImportError as exc:
        print(f"PDF не сделан ({exc}); HTML можно распечатать в PDF из браузера.")
    print(f"готово: {out}")


if __name__ == "__main__":
    main()
