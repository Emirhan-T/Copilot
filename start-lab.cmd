@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Once README.md dosyasindaki Python kurulumunu tamamla.
  pause
  exit /b 1
)
echo Copilot: http://127.0.0.1:8766
echo Sunucuyu kapatmak icin Ctrl+C.
".venv\Scripts\copilot.exe" serve --port 8766
pause
