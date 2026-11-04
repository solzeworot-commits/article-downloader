@echo off
rem Same as the main launcher, but in a console window, so start-up errors stay visible.
set "PYTHONHOME="
set "PYTHONPATH="
set "PYTHONNOUSERSITE=1"
"%~dp0..\python\python.exe" -E -s "%~dp0app.py" %*
pause
