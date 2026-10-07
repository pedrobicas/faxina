@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Gerando o Faxina.exe com o PyInstaller (precisa de internet so na primeira vez)...
echo.
py -3 -m pip install --upgrade pyinstaller || goto erro
py -3 -m PyInstaller --noconfirm --clean --onefile --name Faxina --add-data "faxina\web;faxina\web" faxina.py || goto erro
echo.
echo Pronto! O executavel esta em: dist\Faxina.exe
echo Ele roda em qualquer Windows, mesmo sem o Python instalado.
pause
exit /b 0

:erro
echo.
echo Algo deu errado. Confira se o Python esta instalado (comando "py").
pause
exit /b 1
