@echo off

cd %HOMEPATH%\projects\poor-man-dns\dist\

call "..\..\venv-3.12\scripts\activate.bat"
start python -X dev ..\app\main.py

rem pause
