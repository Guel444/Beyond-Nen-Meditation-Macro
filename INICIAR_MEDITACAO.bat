@echo off
setlocal
cd /d "%~dp0"
title Beyond Nen - Meditation Macro v1.0.0

echo ========================================================
echo           BEYOND NEN - MEDITATION MACRO v1.0.0
echo ========================================================
echo.

where py >nul 2>nul
if %errorlevel%==0 (
    set "PYTHON=py -3"
) else (
    where python >nul 2>nul
    if %errorlevel%==0 (
        set "PYTHON=python"
    ) else (
        echo [ERRO] Python nao foi encontrado.
        echo Instale Python 3.10 ou superior e marque "Add Python to PATH".
        echo https://www.python.org/downloads/
        echo.
        pause
        exit /b 1
    )
)

echo [1/2] Instalando/verificando dependencias...
%PYTHON% -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 (
    echo.
    echo [ERRO] Nao foi possivel instalar as dependencias.
    echo Verifique sua conexao e a instalacao do Python.
    pause
    exit /b 1
)

echo.
echo [2/2] Abrindo o Meditation Macro...
%PYTHON% meditation_macro.py
if errorlevel 1 (
    echo.
    echo [ERRO] O macro foi encerrado com erro.
    pause
    exit /b 1
)

endlocal
