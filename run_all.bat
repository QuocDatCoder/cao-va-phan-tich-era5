@echo off
setlocal
REM Chay ca backend (FastAPI) va frontend (Streamlit) trong 2 cua so rieng.
REM   run_all.bat        -> dung cau hinh trong file .env
REM   run_all.bat mock   -> du lieu gia lap
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo Chua co moi truong .venv. Chay 2 lenh sau roi chay lai file nay:
  echo   python -m venv .venv
  echo   .venv\Scripts\python.exe -m pip install -r requirements.txt
  pause
  exit /b 1
)

set "PREFIX="
if /i "%~1"=="mock" (
  set "PREFIX=set CLIMATE_PROVIDER=mock&& set ERA5_SYNC_ENABLED=false&& "
  echo Che do MOCK: du lieu gia lap.
) else (
  set "CLIMATE_PROVIDER="
  echo Che do theo CLIMATE_PROVIDER trong file .env cua du an.
)

start "Backend ERA5 (cong 8000)" cmd /k "%PREFIX%.venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000"
echo Cho backend khoi dong...
timeout /t 6 /nobreak >nul
start "Frontend ERA5 (cong 8501)" cmd /k "set API_BASE_URL=http://127.0.0.1:8000&& set API_TIMEOUT_SECONDS=900&& set ADMIN_BASE_URL=http://localhost:8502&& .venv\Scripts\python.exe -m streamlit run app.py"
start "Admin ERA5 (cong 8502)" cmd /k "set API_BASE_URL=http://127.0.0.1:8000&& set API_TIMEOUT_SECONDS=60&& set MAIN_APP_URL=http://localhost:8501&& .venv\Scripts\python.exe -m streamlit run admin_app.py --server.port 8502 --server.headless true"

echo.
echo Dashboard: http://127.0.0.1:8501
echo Admin:     http://127.0.0.1:8502
echo Swagger:   http://127.0.0.1:8000/docs
echo Dung: dong 2 cua so den (hoac nhan Ctrl+C trong tung cua so).
