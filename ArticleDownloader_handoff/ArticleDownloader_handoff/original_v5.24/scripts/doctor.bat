@echo off
cd /d "%~dp0.."
python "%~dp0doctor.py" %*
pause
