@echo off
setlocal
cd /d "%~dp0"
title Beyond Nen - Meditation Macro v1.0.0

echo ========================================================
echo           BEYOND NEN - MEDITATION MACRO v1.0.0
echo ========================================================
echo.
echo Este modo so e necessario se o Roblox estiver sendo executado
echo como Administrador ou se houver bloqueio de permissao de entrada.
echo.
powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%ComSpec%' -ArgumentList '/c', '""%~dp0INICIAR_MEDITACAO.bat""' -Verb RunAs"
endlocal
