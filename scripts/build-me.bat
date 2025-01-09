@echo.
@echo this script should be run from the same level as the poor-man-dns/app/ folder.

cd run

pyinstaller --onefile ^
 --name "poor-man-dns" ^
 --add-data "..\\app\\helpers\\*.py:helpers" ^
 --add-data "..\\app\\static:static" ^
 --add-data "..\\app\\templates\\*.html:templates" ^
 --add-data "..\\certs\\*.pem:certs" ^
 --hidden-import aiohttp[speedups] ^
 --hidden-import aiohttp-jinja2 ^
 --hidden-import jinja2 ^
 --hidden-import dnspython ^
 --hidden-import aioquic ^
 --hidden-import cryptography ^
 --hidden-import httpx ^
 --hidden-import idna ^
 --hidden-import cachetools ^
 --hidden-import psutil ^
 --hidden-import pyyaml ^
 --hidden-import sqlalchemy ^
 ..\app\main.py

@echo.
