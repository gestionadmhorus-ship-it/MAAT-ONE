#ifndef AppVersion
  #define AppVersion "1.1.0"
#endif
[Setup]
AppId={{F454E50D-C8DE-46E4-B373-80328B99DD6F}
AppName=MAAT One
AppVersion={#AppVersion}
AppPublisher=MAAT One
AppPublisherURL=https://github.com/gestionadmhorus-ship-it/MAAT-ONE
DefaultDirName={localappdata}\Programs\MAAT One
DefaultGroupName=MAAT One
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=..\release_dist
OutputBaseFilename=MAAT-One-{#AppVersion}-Setup
SetupIconFile=..\assets\maat-one.ico
UninstallDisplayIcon={app}\MAAT One.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[Files]
Source: "..\dist\MAAT One\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autodesktop}\MAAT One"; Filename: "{app}\MAAT One.exe"; WorkingDir: "{app}"
Name: "{group}\MAAT One"; Filename: "{app}\MAAT One.exe"; WorkingDir: "{app}"

[Run]
Filename: "{app}\MAAT One.exe"; Description: "Abrir MAAT One"; Flags: nowait postinstall skipifsilent
