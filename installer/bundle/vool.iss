; Inno Setup script for the self-contained VOOL Windows installer.
; Packages the staging directory produced by build_bundle.ps1 into a double-click
; VOOL-Setup.exe. Per-user install (no admin needed). The .exe is unsigned unless you
; compile on a machine with a code-signing cert configured; unsigned installers trigger
; SmartScreen until signed.
;
; Build:  build_bundle.ps1 -Stage <stage>   then   ISCC /DStage=<stage> vool.iss

#ifndef Stage
  #define Stage "G:\vool-build\bundle"
#endif
#ifndef OutDir
  #define OutDir "G:\vool-build\dist"
#endif
#define AppName "VOOL"
; Passed by the build as /DAppVersion=<core/app_version.py VOOL_VERSION>. Hardcoding it here
; let the installer report 0.4.0 while the runtime and the release channel had moved on, so an
; installed build could not be identified from Add/Remove Programs.
#ifndef AppVersion
  #define AppVersion "0.0.0-unstamped"
#endif

[Setup]
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Parad0x Labs
DefaultDirName={autopf}\VOOL
DefaultGroupName=VOOL
DisableProgramGroupPage=yes
OutputDir={#OutDir}
OutputBaseFilename=VOOL-Setup
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
PrivilegesRequired=lowest
; We stop the running VOOL server ourselves (see [Code]) before touching files, so Setup never
; needs to prompt the user to close "Python", and never tries to restart it (the launcher does).
RestartApplications=no
CloseApplications=no
SetupIconFile={#SourcePath}\..\assets\vool.ico
UninstallDisplayIcon={app}\ollama\ollama.exe

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Files]
; Enumerate the intended payload explicitly so compiler output and scratch directories under Stage
; cannot be copied into the installed bundle.
Source: "{#Stage}\app\*"; DestDir: "{app}\app"; Flags: recursesubdirs createallsubdirs ignoreversion
; Embedded models seed the persistent per-user store used by the launcher and are preserved on uninstall.
Source: "{#Stage}\models\*"; DestDir: "{localappdata}\VOOL\models"; Flags: recursesubdirs createallsubdirs ignoreversion skipifsourcedoesntexist uninsneveruninstall
Source: "{#Stage}\ollama\*"; DestDir: "{app}\ollama"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "{#Stage}\python\*"; DestDir: "{app}\python"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "{#Stage}\bundle_manifest.json"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#Stage}\bundle_supervisor.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#Stage}\VOOL.cmd"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#Stage}\vool-open.ps1"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#Stage}\vool.vbs"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#Stage}\vool_window.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#Stage}\vool_protocol_handler.py"; DestDir: "{app}"; Flags: ignoreversion

[Registry]
; User-facing VOOL deep link. HKCU matches PrivilegesRequired=lowest and never requires admin.
Root: HKCU; Subkey: "Software\Classes\vool"; ValueType: string; ValueName: ""; ValueData: "URL:VOOL Protocol"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\vool"; ValueType: string; ValueName: "URL Protocol"; ValueData: ""; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Software\Classes\vool\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\python\pythonw.exe,0"; Flags: uninsdeletekeyifempty
Root: HKCU; Subkey: "Software\Classes\vool\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\python\pythonw.exe"" ""{app}\vool_protocol_handler.py"" ""%1"""; Flags: uninsdeletekeyifempty

[Icons]
; Launch via wscript so the VBS runs windowless (no console flash) with no dependency on the
; .vbs file association -- a .vbs cannot be executed directly (CreateProcess fails, code 193).
Name: "{group}\VOOL"; Filename: "{sys}\wscript.exe"; Parameters: """{app}\vool.vbs"""; IconFilename: "{#SourcePath}\..\assets\vool.ico"; WorkingDir: "{app}"
Name: "{group}\Uninstall VOOL"; Filename: "{uninstallexe}"
Name: "{autodesktop}\VOOL"; Filename: "{sys}\wscript.exe"; Parameters: """{app}\vool.vbs"""; IconFilename: "{#SourcePath}\..\assets\vool.ico"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{sys}\wscript.exe"; Parameters: """{app}\vool.vbs"""; Description: "Launch VOOL now"; Flags: nowait postinstall skipifsilent

; No [UninstallDelete] for the data dir: uninstall removes ONLY the program files. The user's
; models, wallet, keys, memory and receipts in {localappdata}\VOOL are preserved (a reinstall
; reuses them, no multi-GB re-download and no lost wallet). A full reset stays a manual choice.

[Code]
procedure StopNulla();
var
  ResultCode: Integer;
begin
  { Stop EVERY VOOL-owned python before touching files, so install/uninstall never hit a locked DLL
    (libcrypto-3.dll is loaded by BOTH the server vool_api_server.py AND the window host
    vool_window.py). Targets only python/pythonw whose exe lives under the VOOL install dir, or that
    run one of VOOL's scripts -- never the user's own Python, and never this powershell itself. Then
    waits for the OS to release the file handles before extraction begins. }
  Exec('powershell.exe',
    '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -Command "$r = $env:LOCALAPPDATA + ''\Programs\VOOL''; Get-NetTCPConnection -LocalPort 11435 -State Listen -EA SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -EA SilentlyContinue }; Get-CimInstance Win32_Process -EA SilentlyContinue | Where-Object { ($_.Name -eq ''python.exe'' -or $_.Name -eq ''pythonw.exe'') -and (($_.ExecutablePath -like ($r + ''*'')) -or ($_.CommandLine -like ''*vool_api_server.py*'') -or ($_.CommandLine -like ''*vool_window.py*'')) } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -EA SilentlyContinue }; Start-Sleep -Milliseconds 900"',
    '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Sleep(1500);
end;

function InitializeSetup(): Boolean;
begin
  StopNulla();
  Result := True;
end;

function InitializeUninstall(): Boolean;
begin
  StopNulla();
  Result := True;
end;
