# build_mira_with_nuitka.ps1
# Build Mira as a standalone onefile Windows .exe via (commercial) Nuitka.
# Mirrors E:\python\windows\build_xdtd_with_nuitka.ps1, adapted to Mira's
# packages, bundled data, and the mira\__main__.py entry point.
#
# Run from a Windows shell:  powershell -ExecutionPolicy Bypass -File .\build_mira_with_nuitka.ps1
#
# ASCII-only on purpose: invoked via `powershell.exe` (Windows PowerShell 5.1)
# which reads BOM-less files as the system ANSI code page, so any UTF-8 multi-
# byte char (em-dash, box-drawing) corrupts the parse. Keep this file ASCII.

# --- 1. Paths ----------------------------------------------------------------
# The script lives in the repo root, so derive everything from its own location.
$REPO_DIR     = $PSScriptRoot
$BUILD_OUTPUT = "$REPO_DIR\dist"

# EDIT if you build inside a venv. Point this at the Python whose environment
# holds the commercial Nuitka + Mira's deps. "python" uses the active env.
$VENV_PYTHON  = "python"
# Example: $VENV_PYTHON = "$REPO_DIR\.venv\Scripts\python.exe"

Set-Location $REPO_DIR

# --- 2. Pre-build sanity -----------------------------------------------------
# The bundled binaries are NOT in git (CLAUDE.md) - fail early if missing, the
# same spirit as the XdTd script's engine-file check. ffmpeg comes from the
# imageio_ffmpeg wheel at runtime (core/video_extract.py, core/proc.py docs),
# so we don't gate on bin\ffmpeg - that include is handled by
# --include-package-data=imageio_ffmpeg below.
foreach ($p in @("bin\exiftool.exe", "bin\exiftool_files")) {
    if (-not (Test-Path "$REPO_DIR\$p")) {
        Write-Host "CRITICAL: $p not found - bundle the binaries before building." -ForegroundColor Red
        exit 1
    }
}

# Onefile mode compresses with 'zstandard'. Without it the .exe still builds,
# but uncompressed (much larger). Warn early rather than mid-build.
& $VENV_PYTHON -c "import zstandard" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "WARNING: 'zstandard' not installed - onefile .exe will NOT be compressed." -ForegroundColor Yellow
    Write-Host "         Fix:  $VENV_PYTHON -m pip install zstandard" -ForegroundColor Yellow
}

# --- 3. Clean previous build -------------------------------------------------
if (Test-Path $BUILD_OUTPUT) {
    Write-Host "Cleaning previous build output..." -ForegroundColor Yellow
    Remove-Item -Recurse -Force $BUILD_OUTPUT
}
New-Item -ItemType Directory -Force -Path $BUILD_OUTPUT | Out-Null

# --- 4. ExifTool DLLs --------------------------------------------------------
# --include-data-dir skips DLLs, so gather them as explicit --include-data-files
# (mirrors build.bat). Paths are repo-relative with forward slashes for Nuitka.
$dllArgs = @()
Get-ChildItem -Path "$REPO_DIR\bin\exiftool_files" -Recurse -Filter *.dll | ForEach-Object {
    $rel     = $_.FullName.Substring($REPO_DIR.Length + 1) -replace '\\', '/'
    $destDir = ($rel -replace '/[^/]+$', '/')
    $dllArgs += "--include-data-files=$rel=$destDir"
}
Write-Host "Gathered $($dllArgs.Count) ExifTool DLL include flags" -ForegroundColor Cyan

# --- 5. Build ----------------------------------------------------------------
Write-Host "Starting Nuitka build..." -ForegroundColor Green
& $VENV_PYTHON -m nuitka `
    --standalone `
    --onefile `
    --enable-plugin=pyqt6 `
    --include-qt-plugins=sensible,multimedia `
    --windows-console-mode=disable `
    --follow-imports `
    --include-package=mira `
    --include-package=core `
    --include-package=reverse_geocoder `
    --include-package-data=reverse_geocoder `
    --include-package=pillow_heif `
    --include-package=rawpy `
    --include-package=imageio_ffmpeg `
    --include-package-data=imageio_ffmpeg `
    --include-data-dir="assets=assets" `
    --include-data-dir="bin\exiftool_files=bin/exiftool_files" `
    --include-data-files="bin\exiftool.exe=bin/exiftool.exe" `
    $dllArgs `
    --enable-plugin=data-hiding `
    --company-name="Nelson Salgado" `
    --product-name="Mira" `
    --file-version=0.1.0 `
    --product-version=0.1.0 `
    --jobs=8 `
    --assume-yes-for-downloads `
    --output-dir="$BUILD_OUTPUT" `
    --output-filename=Mira.exe `
    --windows-icon-from-ico="assets\icons\mira.ico" `
    --python-flag=-m `
    --main="mira"

# --- 6. Result ---------------------------------------------------------------
if ($LASTEXITCODE -ne 0) {
    Write-Host "Build failed (exit $LASTEXITCODE)." -ForegroundColor Red
    exit 1
}
Write-Host ""
Write-Host "Build succeeded: $BUILD_OUTPUT\Mira.exe" -ForegroundColor Green
