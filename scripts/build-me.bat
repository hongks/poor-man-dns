@echo.
@echo this script should be run from the same level as the poor-man-dns/app/ folder.

pyinstaller --onefile ^
 --name "poor-man-dns" ^
 --add-data "app\\helpers\\*.py:helpers" ^
 --add-data "certs\\*.pem:certs" ^
 --hiddenimport cachetools ^
 --hiddenimport dns.message ^
 --hiddenimport dns.query ^
 --hiddenimport dns.rdatatype ^
 --hiddenimport httpx ^
 --hiddenimport sqlalchemy ^
 --hiddenimport yaml ^
 app\main.py

@echo.
