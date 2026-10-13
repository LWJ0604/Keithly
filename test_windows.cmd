@echo off
cd /d "%~dp0"
set "KXCI_SIM_ONLY=1"
".venv\Scripts\python.exe" -m unittest discover -s tests -v
