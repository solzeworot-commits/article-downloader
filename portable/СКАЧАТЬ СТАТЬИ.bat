@echo off
rem Opens the article downloader window. Files with DOIs dropped onto this
rem file are added to the queue and started.
set "PYTHONHOME="
set "PYTHONPATH="
set "PYTHONNOUSERSITE=1"
if not exist "%~dp0python\pythonw.exe" (
    echo The "python" folder is missing. Unpack the whole archive, not just this file.
    pause
    exit /b 1
)
start "" "%~dp0python\pythonw.exe" -E -s "%~dp0scripts\app.py" %*
