; Inno Setup script — built by tools/build_exe.py (ISCC /DAppVersion=x.y.z installer\AutoBrowser.iss)
#ifndef AppVersion
  #define AppVersion "6.0.0"
#endif

[Setup]
AppId={{5B0C2C61-6C3E-4C59-9A0E-2F7A1B3C4D5E}
AppName=Auto Browser
AppVersion={#AppVersion}
AppVerName=Auto Browser {#AppVersion}
AppPublisher=Auto Browser
AppPublisherURL=https://github.com/Thequancute215-sys/Auto-Browser
DefaultDirName={localappdata}\Programs\Auto Browser
DefaultGroupName=Auto Browser
DisableProgramGroupPage=yes
; Per-user install: no admin rights / UAC prompt needed
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=AutoBrowser-Setup-{#AppVersion}
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\AutoBrowser.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Tạo biểu tượng trên màn hình Desktop"; GroupDescription: "Biểu tượng:"

[Files]
Source: "..\dist\AutoBrowser\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Auto Browser"; Filename: "{app}\AutoBrowser.exe"; IconFilename: "{app}\AutoBrowser.exe"
Name: "{group}\Gỡ cài đặt Auto Browser"; Filename: "{uninstallexe}"
Name: "{userdesktop}\Auto Browser"; Filename: "{app}\AutoBrowser.exe"; IconFilename: "{app}\AutoBrowser.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\AutoBrowser.exe"; Description: "Mở Auto Browser ngay"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; Make sure the app is not running while files are removed
Filename: "{sys}\taskkill.exe"; Parameters: "/F /IM AutoBrowser.exe /T"; Flags: runhidden; RunOnceId: "KillApp"
