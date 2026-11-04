@echo off
setlocal EnableExtensions

set "SCRIPTS=%~dp0"
set "TRAY=%SCRIPTS%\tray_watcher.py"

echo Stopping old ArticleDownloader tray process...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
 "$ps = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*ArticleDownloader*tray_watcher.py*' }; foreach($p in $ps){ Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }"

timeout /t 1 /nobreak >nul

for /f "delims=" %%P in ('where pythonw.exe 2^>nul') do (
    set "PYTHONW=%%P"
    goto :got_pythonw
)

:got_pythonw
if not defined PYTHONW (
    echo ERROR: pythonw.exe not found.
    pause
    exit /b 1
)

echo Starting tray...
start "" "%PYTHONW%" "%TRAY%"

timeout /t 3 /nobreak >nul

echo.
echo If the icon is not visible, click the ^ arrow next to the Windows clock.
echo Tray log:
echo %SCRIPTS%..\logs\tray_watcher.log
echo.
pause
endlocal
