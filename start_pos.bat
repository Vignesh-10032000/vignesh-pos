@echo off
title Vignesh Growth Lab POS - Startup Manager
echo ==============================================================
echo   VIGNESH GROWTH LAB POS - STARTUP MANAGER
echo ==============================================================
echo.

:: Navigate to the workspace
cd /d "c:\Users\L E N O V O\Desktop\New folder\flask-pos-main"

:: Ensure dependencies are installed
echo [1/3] Checking dependencies...
python -c "import waitress, flask_compress, flask_caching, flask_limiter, flask_cors, psutil" 2>nul
if %errorlevel% neq 0 goto install_deps
echo All python dependencies verified!
goto start_server

:install_deps
echo Installing missing dependencies...
pip install -r requirements.txt
goto start_server

:start_server
:: Check if PM2 is installed
where pm2 >nul 2>nul
if %errorlevel% equ 0 goto start_pm2
goto start_standalone

:start_pm2
echo [2/3] Starting POS Server with PM2 (Auto-Recovery Enabled)...
pm2 delete pos-server >nul 2>nul || ver >nul
pm2 start serve.py --name "pos-server" --interpreter python
echo POS Server started in background via PM2!
goto check_health

:start_standalone
echo [2/3] Starting POS Server with Waitress (Standalone Mode)...
rem Kill any process running on port 5000
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :5000 ^| findstr LISTENING') do taskkill /f /pid %%a >nul 2>nul || ver >nul
rem Start serve.py in a background process
start /B python serve.py > "%temp%\pos_server_log.txt" 2>&1
echo POS Server launched in background!
goto check_health

:check_health
echo Waiting for POS Server to be ready (health check)...
:check_loop
powershell -Command "try { $r = Invoke-RestMethod -Uri http://localhost:5000/health -TimeoutSec 1; if ($r.status -eq 'ok') { exit 0 } else { exit 1 } } catch { exit 1 }"
if %errorlevel% neq 0 goto check_wait
echo POS Server is UP and healthy!
goto start_ngrok

:check_wait
timeout /t 1 /nobreak >nul
goto check_loop

:start_ngrok
:: Starting Ngrok
echo [3/3] Launching Ngrok tunnel...
taskkill /f /im ngrok.exe >nul 2>nul || ver >nul

if exist ngrok.exe goto start_local_ngrok
goto start_global_ngrok

:start_local_ngrok
start /B ngrok.exe start --config ngrok.yml pos
goto end_startup

:start_global_ngrok
start /B ngrok start --config ngrok.yml pos
goto end_startup

:end_startup
echo.
echo ==============================================================
echo   POS AND NGROK STARTUP COMPLETED SUCCESSFULLY!
echo   POS Local Address : http://localhost:5000
echo   Ngrok Public URL  : https://cycling-jab-traction.ngrok-free.dev
echo ==============================================================
timeout /t 5
