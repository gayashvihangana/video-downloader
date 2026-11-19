; Inno Setup script: makes release\VideoDownloader-Setup-<version>.exe
; Built by build\build.ps1 (it passes the version in).

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif

[Setup]
; Never change this id: Windows uses it to recognise updates/uninstall
AppId={{3CD4D9A6-E0A7-4CB3-99BE-E605BDF5EF67}
AppName=Video Downloader
AppVersion={#AppVersion}
AppPublisher=Gayash Vihangana
AppPublisherURL=https://github.com/gayashvihangana/video-downloader
DefaultDirName={localappdata}\Programs\Video Downloader
DefaultGroupName=Video Downloader
DisableProgramGroupPage=yes
; Installs for the current user only: no admin password needed
PrivilegesRequired=lowest
OutputDir=..\release
OutputBaseFilename=VideoDownloader-Setup-{#AppVersion}
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\VideoDownloader.exe
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Files]
Source: "..\dist\VideoDownloader\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\Video Downloader"; Filename: "{app}\VideoDownloader.exe"
Name: "{group}\Uninstall Video Downloader"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Video Downloader"; Filename: "{app}\VideoDownloader.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\VideoDownloader.exe"; Description: "Open Video Downloader"; Flags: nowait postinstall skipifsilent
