# Builds the Windows installer for the version in core.py (APP_VERSION):
#     release\VideoDownloader-Setup-<version>.exe
#
# Run from the project folder:
#     powershell -ExecutionPolicy Bypass -File build\build.ps1          (release build)
#     powershell -ExecutionPolicy Bypass -File build\build.ps1 -Quick   (fast test build)
#
# -Quick reuses the previous build and compresses less: much faster, but the
# Setup.exe is bigger. Use the normal build for files you publish.
#
# Older Setup files are moved to release\old so release\ only holds the latest.
#
# Needs: pip install -r requirements.txt pyinstaller
#        Inno Setup 6 (winget install JRSoftware.InnoSetup)
#        ffmpeg on PATH, and Deno in tools\deno\deno.exe

param([switch]$Quick)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$timer = [System.Diagnostics.Stopwatch]::StartNew()

# Version comes from APP_VERSION in core.py
$version = (Select-String -Path core.py -Pattern '^APP_VERSION = "(.+)"').Matches[0].Groups[1].Value
$kind = if ($Quick) { "quick test build" } else { "release build" }
Write-Host "Building Video Downloader $version ($kind)" -ForegroundColor Cyan

# 1. Icon
if (-not (Test-Path assets\icon.ico)) { python build\make_icon.py }

# The old build folder must be deletable: a running copy of the app, or
# Windows Defender scanning a freshly copied file, can lock it for a moment
$running = Get-Process | Where-Object { $_.Path -like "$root\dist\*" }
if ($running) {
    throw "Close Video Downloader (running from the dist folder) first: $($running.ProcessName -join ', ')"
}
for ($try = 1; (Test-Path dist\VideoDownloader) -and $try -le 5; $try++) {
    try { Remove-Item dist\VideoDownloader -Recurse -Force -ErrorAction Stop }
    catch {
        if ($try -eq 5) { throw "dist\VideoDownloader is locked (antivirus scan?). Wait a minute and try again." }
        Start-Sleep -Seconds 3
    }
}

# 2. App -> dist\VideoDownloader\VideoDownloader.exe
#    One list of arguments. (Careful: PowerShell turns a one-item list from
#    an `if` into plain text, and @text then passes it letter by letter.)
$pyinstaller = @(
    "--noconfirm", "--windowed",
    "--name", "VideoDownloader",
    "--icon", "$root\assets\icon.ico",
    "--add-data", "$root\assets\icon.ico;assets",
    "--collect-data", "qfluentwidgets",
    "--collect-submodules", "gallery_dl",
    "--collect-all", "yt_dlp_ejs",
    "--workpath", "build\pyinstaller",
    "--specpath", "build\pyinstaller"
)
# A release build starts from scratch (--clean); -Quick reuses the cache
if (-not $Quick) { $pyinstaller += "--clean" }

python -m PyInstaller @pyinstaller main.py
if ($LASTEXITCODE) { throw "PyInstaller failed" }

$dist = "dist\VideoDownloader"

# Drop parts of Qt the app never uses (QML/Quick, virtual keyboard, PDF,
# software OpenGL, translations): about 45 MB smaller. The window only needs
# Widgets, Gui, Svg and the qwindows platform plugin.
$qt = "$dist\_internal\PySide6"
@(
    "opengl32sw.dll", "Qt6Quick*.dll", "Qt6Qml*.dll", "Qt6VirtualKeyboard*.dll",
    "Qt6Pdf.dll", "translations",
    "plugins\platforminputcontexts", "plugins\generic", "plugins\networkinformation",
    "plugins\tls", "plugins\imageformats\qpdf.dll",
    "plugins\platforms\qdirect2d.dll", "plugins\platforms\qminimal.dll",
    "plugins\platforms\qoffscreen.dll"
) | ForEach-Object { Remove-Item "$qt\$_" -Recurse -Force -ErrorAction SilentlyContinue }

# 3. Bundle ffmpeg (no ffplay, it isn't needed) and its licence
$ffmpeg = Split-Path -Parent (Get-Command ffmpeg).Source
New-Item -ItemType Directory -Force "$dist\tools\ffmpeg" | Out-Null
Get-ChildItem $ffmpeg -File | Where-Object Name -ne "ffplay.exe" |
    Copy-Item -Destination "$dist\tools\ffmpeg"
$license = Join-Path (Split-Path -Parent $ffmpeg) "LICENSE.txt"
if (Test-Path $license) { Copy-Item $license "$dist\tools\ffmpeg\LICENSE.txt" }

# 4. Bundle Deno (JavaScript runtime yt-dlp needs for YouTube)
New-Item -ItemType Directory -Force "$dist\tools\deno" | Out-Null
Copy-Item tools\deno\deno.exe "$dist\tools\deno\deno.exe"

# 5. Third-party notices
@"
Video Downloader $version
https://github.com/gayashvihangana/video-downloader

This app includes:
- yt-dlp (Unlicense)            https://github.com/yt-dlp/yt-dlp
- gallery-dl (GPL-2.0)          https://github.com/mikf/gallery-dl
- Qt for Python / PySide6 (LGPL-3.0)  https://www.qt.io/qt-for-python
- PySide6-Fluent-Widgets (GPL-3.0)    https://github.com/zhiyiYo/PyQt-Fluent-Widgets
- Pillow (MIT-CMU)              https://python-pillow.org
- FFmpeg (GPL, see tools\ffmpeg\LICENSE.txt)  https://ffmpeg.org
- Deno (MIT)                    https://github.com/denoland/deno
"@ | Set-Content "$dist\THIRD-PARTY-NOTICES.txt" -Encoding utf8

# 6. Installer -> release\
$iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
          "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") |
        Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw "Inno Setup 6 not found (winget install JRSoftware.InnoSetup)" }

# Keep only the latest installer in release\ (older ones go to release\old)
New-Item -ItemType Directory -Force release\old | Out-Null
Get-ChildItem release\VideoDownloader-Setup-*.exe -ErrorAction SilentlyContinue |
    Where-Object Name -ne "VideoDownloader-Setup-$version.exe" |
    Move-Item -Destination release\old -Force

# Maximum compression for releases; fast compression for -Quick
$compression = if ($Quick) { "lzma2/fast" } else { "lzma2/ultra64" }

& $iscc "/DAppVersion=$version" "/DCompression=$compression" /Q build\installer.iss
if ($LASTEXITCODE) { throw "Inno Setup failed" }

$setup = Get-Item "release\VideoDownloader-Setup-$version.exe"
Write-Host ("Done in {0:mm\:ss}: {1}  ({2:N0} MB)" -f $timer.Elapsed, $setup.FullName, ($setup.Length / 1MB)) -ForegroundColor Green
if ($Quick) {
    Write-Host "Quick build: fine for testing. Run without -Quick for the file you publish." -ForegroundColor Yellow
}
