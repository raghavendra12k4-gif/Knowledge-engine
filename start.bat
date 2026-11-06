@echo off
cd /d "%~dp0"

start "Backend" cmd /k ".venv\Scripts\activate.bat && uvicorn app.main:app --reload"
start "Frontend" cmd /k "cd web && npm run dev"

timeout /t 4 /nobreak >nul
start http://localhost:5173

rem .\start.bat: paste in terminal to start the server
