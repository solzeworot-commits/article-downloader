@echo off
rem Kills ONLY the dedicated ArticleDownloader Yandex instances (ports 9223 and 9237)
rem and removes their "reopen tabs" files. Cookies and logins are kept.
cd /d "%~dp0.."
python "%~dp0doctor.py" reset-browser --hard
pause
