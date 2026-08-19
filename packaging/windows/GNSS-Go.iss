#ifndef MyAppVersion
  #define MyAppVersion "0.1.1"
#endif
#define MyAppName "GNSS Go"
#define MyAppPublisher "GNSS Go contributors"

[Setup]
AppId={{AFD8EB3C-8D55-4B6D-A7C8-8194B9892C44}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\GNSS Go
DefaultGroupName=GNSS Go
OutputDir=..\..\release
OutputBaseFilename=GNSS-Go-Setup-{#MyAppVersion}-Windows-x64
SetupIconFile=..\..\src\gnssgo\gui\resources\icons\gnss_go.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
ChangesEnvironment=yes
PrivilegesRequired=lowest

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"; Flags: unchecked
Name: "addtopath"; Description: "Add GNSS Go command-line tools to PATH"; GroupDescription: "Command line:"; Flags: unchecked

[Files]
Source: "..\..\dist\GNSS-Go.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\dist\gnssgo.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\GNSS Go"; Filename: "{app}\GNSS-Go.exe"
Name: "{autodesktop}\GNSS Go"; Filename: "{app}\GNSS-Go.exe"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Environment"; ValueType: expandsz; ValueName: "Path"; ValueData: "{olddata};{app}"; Tasks: addtopath; Check: NeedsAddPath(ExpandConstant('{app}'))

[Run]
Filename: "{app}\GNSS-Go.exe"; Description: "Launch GNSS Go"; Flags: nowait postinstall skipifsilent

[Code]
function NeedsAddPath(Param: string): Boolean;
var
  Paths: string;
begin
  if not RegQueryStringValue(HKCU, 'Environment', 'Path', Paths) then
    Paths := '';
  Result := Pos(';' + Uppercase(Param) + ';', ';' + Uppercase(Paths) + ';') = 0;
end;
