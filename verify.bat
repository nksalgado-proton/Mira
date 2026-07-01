@echo off
REM Mira verification.
REM
REM   verify.bat                       run the full test suite
REM   verify.bat tests\test_gateway.py run only the given file(s)
REM
REM Output also written to verify_output.txt at the repo root for paste-back.
REM
REM 2026-06-12 findings baked in:
REM  * The pytest exit code is PRESERVED. The old script ended with
REM    `type`, so a run that CRASHED mid-suite still reported exit 0.
REM  * QUARANTINE: the two bare-AdjustmentSurface suites run in their
REM    OWN pytest process. A latent machine-local Qt/PyQt bug, which
REM    predates the 2026-06-12 work - reproduced 4/4 at 4eb4d69 -
REM    makes ANY pytest process that constructs bare AdjustmentSurfaces
REM    fail-fast 0xC0000409 in Qt6Core during whichever suite runs NEXT
REM    in the same process. Both suites are green in isolation. The
REM    deep fix is its own session - spec/PROGRESS.md carries the full
REM    record and the one-command reproducer.
cd /d "%~dp0"

if "%~1"=="" goto fullsuite

python -m pytest -q %* > verify_output.txt 2>&1
call :finish %ERRORLEVEL%
exit /b %ERRORLEVEL%

:fullsuite
python -m pytest -q tests --ignore=tests/test_adjustment_surface_busy.py --ignore=tests/test_adjustment_surface_rotation.py > verify_output.txt 2>&1
set MAIN_EXIT=%ERRORLEVEL%
python -m pytest -q tests/test_adjustment_surface_busy.py tests/test_adjustment_surface_rotation.py > verify_quarantine.txt 2>&1
set QUAR_EXIT=%ERRORLEVEL%
type verify_output.txt
echo --- quarantined suites, own process - see header ---
type verify_quarantine.txt
if not "%MAIN_EXIT%"=="0" echo VERIFY FAILED - main pass pytest exit %MAIN_EXIT%
if not "%MAIN_EXIT%"=="0" exit /b %MAIN_EXIT%
if not "%QUAR_EXIT%"=="0" echo VERIFY FAILED - quarantine pass pytest exit %QUAR_EXIT%
if not "%QUAR_EXIT%"=="0" exit /b %QUAR_EXIT%
exit /b 0

:finish
type verify_output.txt
if not "%~1"=="0" echo VERIFY FAILED - pytest exit %~1
exit /b %~1
