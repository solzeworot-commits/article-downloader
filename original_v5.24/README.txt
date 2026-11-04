ArticleDownloader v5.24 — загрузчик статей
==========================================

Работает на рабочем компе (институциональный доступ). Папка синхронизируется
через OneDrive, поэтому задания можно ставить и смотреть результаты с любого
компьютера.

ЧТО МОЖНО ДАТЬ НА ВХОД
----------------------
  DOI                10.1007/s12393-026-09447-8
  ссылка на статью   https://www.sciencedirect.com/science/article/pii/...
  поиск по названию  title: Название статьи || 2017 || Журнал || #24

В строке title: всё после названия необязательно, порядок любой; #24 — номер
по списку, попадает в имя файла (pdf\TITLE_24_Название.pdf).
Поиск по названию идёт по КиберЛенинке, OpenAlex, Europe PMC, Crossref и
Bing. Файл сохраняется, только если совпало название (а при установленном
pypdf — ещё и текст первых страниц PDF). Если сайт с PDF недоступен по сети,
файл берётся из копии в Wayback Machine (web.archive.org).
OpenAlex без ключа даёт на IP ~150 поисков в сутки. Бесплатный ключ
(openalex.org) положить в openalex_key.txt рядом с папкой scripts.
Если нашёлся DOI или страница статьи, но бесплатного PDF нет, результат
NO_ACCESS: TITLE_FOUND_NO_FREE_PDF: <DOI или платная страница>.
Отбракованные проверкой PDF лежат в pdf\_rejected — их стоит просмотреть.
Файлы Supporting Information за статью больше не принимаются.

КАК ПОСТАВИТЬ В ОЧЕРЕДЬ
-----------------------
1) Положить в inbox\ текстовый файл .txt: по одному DOI или title: на строку.
   Трей-вотчер сам разложит его на задания.
2) Или вручную: inbox\<имя>.csv (заголовок query, ниже строка) + пустой
   inbox\<имя>.ready.

Результаты: pdf\ — файлы; done\ и failed\ — задание + .result.txt с логом;
inbox\<имя>.blocked — нужна ручная капча в окне Яндекс.Браузера.

ВРУЧНУЮ
-------
  python scripts\article_downloader.py "10.1021/jf4024195"
  python scripts\article_downloader.py "title: Пивная дробина как сырьё || 2019"

УДАЛЁННОЕ УПРАВЛЕНИЕ (с домашнего компа)
-----------------------------------------
Создать пустой файл в remote_control\: START.flag, STOP.flag, RESTART.flag
или STATUS.flag. Состояние — remote_control\status.txt (обновляется каждые
30 с, если на рабочем компе запущен remote_watcher_control.py).

ФАЙЛЫ
-----
scripts\article_downloader.py    загрузчик (все издательства, CNKI, title:)
scripts\watch_queue.py           очередь inbox -> done/failed
scripts\tray_watcher.py          значок в трее, запускает очередь, .txt-списки
scripts\restart_tray_watcher.bat перезапуск трея
scripts\remote_watcher_control.py удалённые команды через remote_control\
scripts\doctor.bat               диагностика: что запущено, что висит
scripts\reset_browser.bat        жёстко перезапустить выделенный Яндекс.Браузер

Браузер: выделенный профиль Яндекс.Браузера
%LOCALAPPDATA%\ArticleDownloader\YandexProfile, CDP-порт 9223. Загрузчик сам
его запускает; куки и входы на сайты сохраняются. Капчи не обходятся — окно
выводится на передний план и ждёт ручного прохождения.

Нужно: Python 3, pip install playwright psutil pystray pillow pypdf
