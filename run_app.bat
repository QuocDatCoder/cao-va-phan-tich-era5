@echo off
title He Thong Cao & Phan Tich Khi Tuong ERA5
setlocal enabledelayedexpansion

echo ========================================================
echo   KHOI DONG HE THONG CAO & PHAN TICH KHI TUONG ERA5
echo   Do An Seminar Chuyen Nganh - ECMWF / Copernicus CDS
echo ========================================================
echo.

:: Di chuyen den thu muc chua file bat nay
cd /d "%~dp0"

:: 1. Kiem tra xem Python da duoc cai dat tren may chua
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [LOI] Khong tim thay Python tren may tinh!
    echo Vui long cai dat Python 3.9 tro len va tich chon "Add Python to PATH".
    echo.
    pause
    exit /b
)

:: 2. Kiem tra xem moi truong ao .venv da ton tai chua
if not exist ".venv\Scripts\activate.bat" (
    echo [THONG BAO] Phat hien day la lan dau chay tren may nay!
    echo He thong dang tu dong tao moi truong ao va cai dat thu vien...
    echo (Qua trinh nay chi dien ra 1 lan duy nhat, xin vui long doi khoang 1-2 phut)
    echo.
    
    python -m venv .venv
    call .venv\Scripts\activate.bat
    python -m pip install --upgrade pip
    pip install -r requirements.txt
    
    echo.
    echo [THANH CONG] Da cai dat xong toan bo thu vien can thiet!
    echo.
) else (
    call .venv\Scripts\activate.bat
)

:: 3. Khoi dong Web App Streamlit tren trinh duyet
echo Dang khoi dong Web App tren trinh duyet...
echo Dia chi Web: http://localhost:8501
echo.

streamlit run app.py

pause
