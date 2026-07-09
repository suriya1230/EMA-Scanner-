@echo off
cd /d "c:\Users\T490\Downloads\ema_scanners1\ema_scanners\ema_scanner_v2"
"C:\Users\T490\AppData\Local\Programs\Python\Python312\python.exe" -m uvicorn app.main:app --host 0.0.0.0 --port 8000 > "c:\Users\T490\Downloads\ema_scanners1\backend5.log" 2>&1
