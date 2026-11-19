# Builds the Windows installer:  release\VideoDownloader-Setup-<version>.exe
#
# Run from the project folder:   powershell -ExecutionPolicy Bypass -File build\build.ps1
#
# Needs: pip install -r requirements.txt pyinstaller
#        Inno Setup 6 (winget install JRSoftware.InnoSetup)
#        ffmpeg on PATH, and Deno in tools\deno\deno.exe

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

# Version comes from APP_VERSION in main.py
$version = (Select-String -Path main.py -Pattern '^APP_VERSION = "(.+)"').Matches[0].Groups[1].Value
Write-Host "Building Video Downloader $version" -ForegroundColor Cyan

# 1. Icon
if (-not (Test-Path assets\icon.ico)) { python build\make_icon.py }

# 2. App -> dist\VideoDownloader\VideoDownloader.exe
python -m PyInstaller --noconfirm --clean --windowed `
    --name VideoDownloader `
    --icon "$root\assets\icon.ico" `
    --add-data "$root\assets\icon.ico;assets" `
    --collect-data customtkinter `
    --collect-submodules gallery_dl `
    --collect-all yt_dlp_ejs `
    --workpath build\pyinstaller --specpath build\pyinstaller `
    main.py
if ($LASTEXITCODE) { throw "PyInstaller failed" }

$dist = "dist\VideoDownloader"

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
- CustomTkinter (MIT)           https://github.com/TomSchimansky/CustomTkinter
- Pillow (MIT-CMU)              https://python-pillow.org
- FFmpeg (GPL, see tools\ffmpeg\LICENSE.txt)  https://ffmpeg.org
- Deno (MIT)                    https://github.com/denoland/deno
"@ | Set-Content "$dist\THIRD-PARTY-NOTICES.txt" -Encoding utf8

# 6. Installer -> release\
$iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
          "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") |
        Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw "Inno Setup 6 not found (winget install JRSoftware.InnoSetup)" }

& $iscc "/DAppVersion=$version" build\installer.iss
if ($LASTEXITCODE) { throw "Inno Setup failed" }

Get-ChildItem release\*.exe | ForEach-Object {
    Write-Host ("Done: {0}  ({1:N0} MB)" -f $_.FullName, ($_.Length / 1MB)) -ForegroundColor Green
}
