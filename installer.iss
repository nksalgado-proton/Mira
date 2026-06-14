; Inno Setup Script for Mira
; Requires Inno Setup 6.x

[Setup]
AppName=Mira
AppVersion=0.1.0
AppPublisher=NKS
AppPublisherURL=https://github.com/nksalgado-proton
DefaultDirName={autopf}\NKS\Mira
DefaultGroupName=NKS\Mira
OutputDir=installer_output
OutputBaseFilename=Mira_Setup_0.1.0
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
SetupIconFile=assets\icons\app.ico
UninstallDisplayIcon={app}\Mira.exe

[Files]
Source: "dist\Mira.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "assets\*"; DestDir: "{app}\assets"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "bin\*"; DestDir: "{app}\bin"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Mira"; Filename: "{app}\Mira.exe"
Name: "{commondesktop}\Mira"; Filename: "{app}\Mira.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Run]
Filename: "{app}\Mira.exe"; Description: "Launch Mira"; Flags: nowait postinstall skipifsilent
