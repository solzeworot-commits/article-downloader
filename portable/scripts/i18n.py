# -*- coding: utf-8 -*-
# © 2026 Bucaneiro. Бесплатная программа; условия — в файле ЛИЦЕНЗИЯ.txt.
"""
Interface language, Russian or English (v7.3).

The program is written in Russian. ui/lang/en.json holds the English text:
"page" maps every Russian piece of ui/index.html (as it stands in the file,
HTML text and script strings alike) to English, and translate_page() swaps
them in one pass, longest first, before the page is served; "messages" maps the
messages built in Python (tr("Нет связи с {service}…", service=…)) to
English templates with the same {fields}. tests/i18n_test.py checks that
nothing Russian is left on the English page and that every tr() text has a
translation.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

LANG_FILE = Path(__file__).resolve().parent / "ui" / "lang" / "en.json"
LANGS = ("ru", "en")
# Windows UI languages whose users most likely read Russian (primary
# language ids): Russian, Ukrainian, Belarusian, Kazakh, Kyrgyz, Uzbek, Tajik,
# Turkmen, Azerbaijani, Armenian, Georgian.
RUSSIAN_READING = {0x19, 0x22, 0x23, 0x3F, 0x40, 0x43, 0x28, 0x42, 0x2C, 0x2B, 0x37}
# The same languages as language codes (macOS): Russian, Ukrainian, Belarusian, Kazakh,
# Kyrgyz, Uzbek, Tajik, Turkmen, Azerbaijani, Armenian, Georgian.
RUSSIAN_READING_CODES = {"ru", "uk", "be", "kk", "ky", "uz", "tg", "tk", "az", "hy", "ka"}

LANG = "ru"
_page: dict[str, str] | None = None
_page_re: re.Pattern | None = None
_messages: dict[str, str] = {}


def _load():
    global _page, _page_re, _messages
    if _page is None:
        data = json.loads(LANG_FILE.read_text(encoding="utf-8"))
        _page = data.get("page", {})
        _messages = data.get("messages", {})
        # One pass, longest piece first, never inside a Russian word: a short
        # piece such as "с" (seconds) must not bite "Список", and a Russian
        # folder name kept in an English text is not translated again.
        keys = sorted(_page, key=len, reverse=True)
        _page_re = re.compile("(?<![А-Яа-яЁё])(?:" + "|".join(map(re.escape, keys)) + ")(?![А-Яа-яЁё])")


def set_lang(code: str) -> str:
    global LANG
    LANG = code if code in LANGS else "ru"
    return LANG


def detect_lang() -> str:
    """English unless Windows speaks Russian or a neighbouring language."""
    forced = os.environ.get("ARTICLE_LANG", "").lower()
    if forced in LANGS:
        return forced
    if os.name == "nt":
        try:
            import ctypes
            lang_id = ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF
            return "ru" if lang_id in RUSSIAN_READING else "en"
        except Exception:
            return "ru"
    if sys.platform == "darwin":
        import osutil
        code = osutil.mac_language()
        if code:
            return "ru" if code in RUSSIAN_READING_CODES else "en"
    return "ru"


def tr(text: str, **fields) -> str:
    if LANG == "en":
        _load()
        text = _messages.get(text, text)
    return text.format(**fields) if fields else text


def translate_page(page: str) -> str:
    if LANG != "en":
        return page
    _load()
    page = _page_re.sub(lambda m: _page[m.group(0)], page)
    return page.replace('<html lang="ru">', '<html lang="en">', 1)
