@echo off
REM cria_mira_exe.bat — wrapper de build do Mira.
REM Invoca build_mira_with_nuitka.ps1 a partir da raiz do repo (o script
REM resolve seus paths pelo $PSScriptRoot, entao precisa rodar de la).

setlocal
cd /d "%~dp0"

echo === cria_mira_exe: rodando build_mira_with_nuitka.ps1 ===
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\build_mira_with_nuitka.ps1"
set "RC=%ERRORLEVEL%"

echo.
if "%RC%"=="0" (
    echo === Build terminou com sucesso. Confira dist\Mira.exe ===
) else (
    echo === Build FALHOU com codigo %RC% — veja o log acima ===
)
echo.
pause
endlocal
exit /b %RC%
