@echo.
@echo this script should be run from the same level as the poor-man-dns/app/ folder.

pyinstaller --onefile ^
 --name "poor-man-dns" ^
 --add-data "app\\helpers\\*.py:helpers" ^
 --add-data "app\\static:app\\static" ^
 --add-data "app\\templates\\*.html:app\\templates" ^
 --add-data "certs\\*.pem:certs" ^
 --hidden-import aiohttp ^
 --hidden-import aiohttp_jinja2 ^
 --hidden-import cachetools ^
 --hidden-import dns.message ^
 --hidden-import dns.query ^
 --hidden-import dns.rdatatype ^
 --hidden-import httpx ^
 --hidden-import jinja2 ^
 --hidden-import psutil ^
 --hidden-import sqlalchemy ^
 --hidden-import yaml ^
 app\main.py

@echo.
