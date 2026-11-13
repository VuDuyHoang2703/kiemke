@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Dang khoi dong He Thong Kiem Ke Hang Thue Robot...
start "" http://127.0.0.1:5000
python app.py
pause

