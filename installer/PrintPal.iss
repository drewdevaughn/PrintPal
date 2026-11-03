[Setup]
AppId={{8E5C2D7A-9F41-4B6E-B2A7-3C5D1E9F4A62}
AppName=PrintPal
AppVersion=1.0.0
AppPublisher=PrintPal
DefaultDirName={localappdata}\Programs\PrintPal
DefaultGroupName=PrintPal
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist\installer
OutputBaseFilename=PrintPal-Setup-1.0.0
SetupIconFile=..\assets\printpal.ico
UninstallDisplayIcon={app}\PrintPal.exe
UninstallDisplayName=PrintPal
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
SetupLogging=yes
CloseApplications=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "startup"; Description: "Start PrintPal when I sign in"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\PrintPal\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\PrintPal"; Filename: "{app}\PrintPal.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\PrintPal"; Filename: "{app}\PrintPal.exe"; WorkingDir: "{app}"; Tasks: desktopicon
Name: "{userstartup}\PrintPal"; Filename: "{app}\PrintPal.exe"; WorkingDir: "{app}"; Tasks: startup

[Run]
Filename: "{app}\PrintPal.exe"; Description: "{cm:LaunchProgram,PrintPal}"; Flags: nowait postinstall skipifsilent
