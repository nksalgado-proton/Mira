@echo off
REM Build Mira as a standalone onefile Windows executable via Nuitka.
REM
REM Prerequisites:
REM   pip install nuitka PyQt6 rawpy Pillow numpy scipy mutagen pillow-heif reverse_geocoder

setlocal

set OUTPUT_DIR=dist
if not exist %OUTPUT_DIR% mkdir %OUTPUT_DIR%

REM Clean previous build
if exist %OUTPUT_DIR%\Mira.exe del %OUTPUT_DIR%\Mira.exe

REM Generate DLL include flags for exiftool (Nuitka --include-data-dir skips DLLs)
python -c "from pathlib import Path; dlls=list(Path('bin/exiftool_files').rglob('*.dll')); f=open('_dll_includes.txt','w'); [f.write('--include-data-files='+str(d).replace(chr(92),'/')+'='+str(d.parent).replace(chr(92),'/')+'/\n') for d in dlls]; f.close(); print(f'Generated {len(dlls)} DLL include flags')"

REM Build with all includes
python -m nuitka ^
    --standalone ^
    --onefile ^
    --windows-disable-console ^
    --enable-plugin=pyqt6 ^
    --include-qt-plugins=sensible,multimedia ^
    --include-package=mira ^
    --include-package=core ^
    --include-data-dir=assets=assets ^
    --include-data-dir=bin/exiftool_files=bin/exiftool_files ^
    --include-data-dir=bin/ffmpeg=bin/ffmpeg ^
    @_dll_includes.txt ^
    --output-dir=%OUTPUT_DIR% ^
    --output-filename=Mira.exe ^
    --windows-icon-from-ico=assets/icons/mira.ico ^
    --assume-yes-for-downloads ^
    mira\__main__.py

if errorlevel 1 (
    echo Build failed.
    exit /b 1
)

echo.
echo Build succeeded: %OUTPUT_DIR%\Mira.exe
endlocal
