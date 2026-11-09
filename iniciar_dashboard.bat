@echo off
REM Inicia o servidor local do dashboard e abre no navegador.
REM Para desligar: feche a janela "Servidor do dashboard" (ou Ctrl+C nela).
cd /d "%~dp0"
start "Servidor do dashboard" py -m http.server 8000
timeout /t 2 /nobreak >nul
start "" "http://localhost:8000/DashFinal_v5%%20(1).html"
