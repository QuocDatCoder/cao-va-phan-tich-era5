@echo off
title He Thong Cao & Phan Tich Khi Tuong ERA5
echo ========================================================
echo   KHOI DONG HE THONG CAO & PHAN TICH KHI TUONG ERA5
echo   Do An Seminar Chuyen Nganh - ECMWF / Copernicus CDS
echo ========================================================
echo.
echo Dang khoi dong Web App tren trinh duyet...
echo.
set TEMP=D:\temp
set TMP=D:\temp
D:\cao-va-phan-tich-era5\.venv\Scripts\streamlit.exe run D:\cao-va-phan-tich-era5\app.py
pause
