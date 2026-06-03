@echo off
set "APP_DIR=%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$port=8765; $listener=Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue; if (-not $listener) { Start-Process -WindowStyle Hidden -FilePath python -ArgumentList 'app.py' -WorkingDirectory '%APP_DIR%'; Start-Sleep -Seconds 2 }; Start-Process 'http://127.0.0.1:8765/'"
