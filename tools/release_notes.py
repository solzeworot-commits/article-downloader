# © 2026 Bucaneiro. Бесплатная программа; условия — в файле LICENSE.
"""
Release page text with the checksum of the archive:

    python tools/release_notes.py dist/ArticleDownloader_Portable.zip dist/release-notes.md

Takes .github/release-notes.md and adds the SHA-256 of the zip with a short
way to check it in Windows, so a copy passed around can be told from the
official one.
"""
import hashlib
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def main():
    archive, out = Path(sys.argv[1]), Path(sys.argv[2])
    notes = (HERE.parent / ".github" / "release-notes.md").read_text(encoding="utf-8").rstrip()
    notes += f"""

## Как проверить, что архив настоящий

Программа бесплатная, автор — Bucaneiro. Если архив вам прислали не с этой страницы или продали, сверьте контрольную сумму:

1. Нажмите **Пуск**, наберите `cmd` и откройте **«Командная строка»**.
2. Вставьте команду и нажмите **Enter** (если архив лежит не в «Загрузках», поправьте путь):

   ```
   certutil -hashfile "%USERPROFILE%\\Downloads\\{archive.name}" SHA256
   ```

3. Длинная строка из букв и цифр должна совпасть с этой:

   `{sha256(archive)}`

Не совпадает — архив изменён, не запускайте его и скачайте заново с этой страницы.
"""
    out.write_text(notes + "\n", encoding="utf-8")
    print(f"SHA-256 {archive.name}: {sha256(archive)}")


if __name__ == "__main__":
    main()
