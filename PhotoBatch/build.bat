@echo off
cd /d "%~dp0"
set "PY=python"
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
echo Using: %PY%
"%PY%" -m pip install -r requirements.txt
if errorlevel 1 goto :fail
set "UPXARG="
if exist "_upx\upx.exe" set "UPXARG=--upx-dir _upx"
"%PY%" -m PyInstaller --noconfirm --onefile --noconsole --name PhotoStudio --add-data "index.html;." --add-data "assets\app.ico;assets" --icon "assets\app.ico" --exclude-module=numpy --version-file version_info.txt --hidden-import=core --hidden-import=server --hidden-import=dialogs --hidden-import=win32timezone %UPXARG% app.py
if errorlevel 1 goto :fail
echo.
echo Build OK: dist\PhotoStudio.exe
echo Rename it to the final product name before shipping.
goto :eof
:fail
echo Build FAILED.
pause
