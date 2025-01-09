@echo off
rem cd <poor-man-dns location>

call "..\venv\scripts\activate.bat"
python -u ../app/main.py

rem pause
