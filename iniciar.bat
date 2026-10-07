@echo off
chcp 65001 >nul
title Faxina
cd /d "%~dp0"

rem Tenta o "py" (instalador oficial do Python) e depois o "python".
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 faxina.py %*
    goto fim
)
where python >nul 2>nul
if %errorlevel%==0 (
    python faxina.py %*
    goto fim
)

echo.
echo   O Python 3 nao foi encontrado neste computador.
echo   Baixe em https://www.python.org/downloads/
echo   Na instalacao, marque a opcao "Add python.exe to PATH".
echo.
pause
exit /b 1

:fim
if errorlevel 1 pause
