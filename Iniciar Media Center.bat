@echo off
setlocal
cd /d "%~dp0"

if not exist "%~dp0.venv\Scripts\python.exe" (
    echo Ambiente Python nao encontrado.
    echo Execute primeiro as instrucoes de instalacao do README.md.
    pause
    exit /b 1
)

"%~dp0.venv\Scripts\python.exe" -m streamlit run "%~dp0app.py"

if errorlevel 1 (
    echo.
    echo O Media Center foi encerrado com erro. Confira a mensagem acima.
    pause
)

endlocal
