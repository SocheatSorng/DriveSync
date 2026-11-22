; Drive Sync installer (Inno Setup 6). Built by build.ps1 after PyInstaller:
;   iscc /DAppVersion=1.1.0 installer.iss   ->  dist\DriveSync-Setup.exe
;
; Installs for the current user only (no administrator rights needed) into
; %LOCALAPPDATA%\Programs\DriveSync. Settings, accounts and sync state live in
; %LOCALAPPDATA%\DriveSync and are kept on uninstall and update.
; Silent updates (/VERYSILENT, run by the app itself) close Drive Sync and start it again afterwards.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{DADA35B2-F37C-4216-811A-4F81FD3328AD}
AppName=Drive Sync
AppVersion={#AppVersion}
AppVerName=Drive Sync {#AppVersion}
AppPublisher=Drive Sync
AppPublisherURL=https://github.com/SocheatSorng/DriveSync
AppSupportURL=https://github.com/SocheatSorng/DriveSync/issues
AppUpdatesURL=https://github.com/SocheatSorng/DriveSync/releases
DefaultDirName={localappdata}\Programs\DriveSync
DisableDirPage=yes
DisableProgramGroupPage=yes
DisableReadyPage=yes
PrivilegesRequired=lowest
OutputDir=dist
OutputBaseFilename=DriveSync-Setup
SetupIconFile=assets\drivesync.ico
UninstallDisplayIcon={app}\DriveSync.exe
UninstallDisplayName=Drive Sync
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=force
RestartApplications=no
VersionInfoVersion={#AppVersion}
VersionInfoProductName=Drive Sync

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; Flags: unchecked
Name: "startup"; Description: "Start Drive Sync when I sign in to Windows"; Check: not WizardSilent

[Files]
Source: "dist\DriveSync.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{userprograms}\Drive Sync"; Filename: "{app}\DriveSync.exe"
Name: "{userprograms}\Uninstall Drive Sync"; Filename: "{uninstallexe}"
Name: "{userdesktop}\Drive Sync"; Filename: "{app}\DriveSync.exe"; Tasks: desktopicon
; Same shortcut name and arguments as the app's own "Start with Windows" setting.
Name: "{userstartup}\Drive Sync"; Filename: "{app}\DriveSync.exe"; Parameters: "--hidden"; Tasks: startup

[Run]
; Interactive install: offer to open it. Silent update (from the app): start it again in the tray.
Filename: "{app}\DriveSync.exe"; Description: "Open Drive Sync"; Flags: nowait postinstall skipifsilent
Filename: "{app}\DriveSync.exe"; Parameters: "--hidden"; Flags: nowait; Check: WizardSilent

[UninstallRun]
Filename: "{sys}\taskkill.exe"; Parameters: "/IM DriveSync.exe /F"; Flags: runhidden; RunOnceId: "StopDriveSync"

[UninstallDelete]
Type: files; Name: "{userstartup}\Drive Sync.lnk"

[Code]
// Uninstall: ask whether to also sign out of Google and delete settings, logins and sync history
// (DriveSync.exe --remove-data). Synced files on this PC and on Google Drive are never touched.
// A silent uninstall keeps everything.
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Code: Integer;
begin
  if (CurUninstallStep = usUninstall) and not UninstallSilent then
  begin
    Exec(ExpandConstant('{sys}\taskkill.exe'), '/IM DriveSync.exe /F', '', SW_HIDE, ewWaitUntilTerminated, Code);
    if MsgBox('Also remove your Drive Sync settings?' + #13#10#13#10 +
              'This signs Drive Sync out of your Google accounts and deletes its settings, folder list, ' +
              'logs and sync history.' + #13#10#13#10 +
              'Your files on this PC and on Google Drive are not touched either way.' + #13#10#13#10 +
              'Choose No to keep them for when you install Drive Sync again.',
              mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
      Exec(ExpandConstant('{app}\DriveSync.exe'), '--remove-data', '', SW_HIDE, ewWaitUntilTerminated, Code);
  end;
end;
