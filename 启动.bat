@echo off
cd /d "%~dp0"
for %%f in ("*.exe") do ( start "" "%%f" & exit /b )
echo No exe found here. Source mode: python app.py
pause
