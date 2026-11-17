@echo off
setlocal
cd /d "%~dp0dashboard"

if not exist ".venv\Scripts\python.exe" (
    echo [1/3] Membuat virtual environment...
    python -m venv .venv
    if errorlevel 1 goto :error
)

echo [2/3] Memeriksa dependensi...
".venv\Scripts\python.exe" -m pip install --upgrade pip --quiet
".venv\Scripts\python.exe" -m pip install -r requirements.txt --quiet
if errorlevel 1 goto :error

echo [3/3] Menjalankan server MQTT + dashboard di http://localhost:8000
set OPEN_BROWSER=1
".venv\Scripts\python.exe" -u -m uvicorn app.main:app --host 0.0.0.0 --port 8000
goto :eof

:error
echo.
echo Gagal menyiapkan dependensi.
echo Pastikan Python 3.10+ terpasang dan ada koneksi internet untuk instalasi pertama.
pause
