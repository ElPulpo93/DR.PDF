; Dr.PDF Windows installer (Inno Setup 6)
; Build the app first with PyInstaller, then compile this script with ISCC.exe (or run build.bat).

#define AppName "Dr.PDF"
; keep AppVersion in step with version.py
#define AppVersion "3.4.3"
#define AppExe "Dr.PDF.exe"
; File Explorer thumbnails + preview pane (shell\DrPdfShell.dll, see shell\build_shell.py).
; The file name carries the version: Explorer may still hold the old one while updating.
#define ShellDll "DrPdfShell-" + AppVersion + ".dll"
#define ThumbClsid "{{333059B3-1456-46CB-8755-29155D498ED2}"
#define PreviewClsid "{{D362B4EE-570A-4322-8004-E3DBDEB4AD07}"

[Setup]
; same AppId as Dr.Converter, so this installs as an upgrade of it
AppId={{6B1E3C2A-4F7D-4C1B-9B8E-2D5A7C0F1E42}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppName}
VersionInfoVersion={#AppVersion}
VersionInfoDescription=Dr.PDF installer
DefaultDirName={autopf}\{#AppName}
UsePreviousAppDir=no
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; Installs for the current user without needing admin rights;
; the user can choose "all users" in the first dialog if they are an admin.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=installer
OutputBaseFilename=Dr.PDF-Setup
SetupIconFile=drpdf.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
ChangesAssociations=yes
#ifdef SIGN
; build.bat passes /DSIGN and the "drsign" tool when a code-signing certificate is set
SignTool=drsign
SignedUninstaller=yes
#endif

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"
Name: "pdfmenu"; Description: "Add ""Edit with Dr.PDF"" when right-clicking a PDF"; GroupDescription: "Shortcuts:"
Name: "sendto"; Description: "Add to the right-click ""Send to"" menu (for converting)"; GroupDescription: "Shortcuts:"
#ifexist "shell\DrPdfShell.dll"
Name: "explorer"; Description: "Show PDF thumbnails and previews in File Explorer"; GroupDescription: "File Explorer:"
#endif

[InstallDelete]
; clean up the old Dr.Converter name
Type: files; Name: "{autoprograms}\Dr.Converter.lnk"
Type: files; Name: "{autodesktop}\Dr.Converter.lnk"
Type: files; Name: "{usersendto}\Dr.Converter.lnk"
Type: filesandordirs; Name: "{autopf}\Dr.Converter"
; File Explorer helpers of earlier versions (one with the current version is installed)
Type: files; Name: "{app}\DrPdfShell-*.dll"
; browser extension files from 3.3.6
Type: files; Name: "{app}\drpdf_host.exe"
Type: files; Name: "{app}\com.drpdf.host.json"
Type: filesandordirs; Name: "{app}\Chrome extension"

[Files]
Source: "dist\Dr.PDF\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
#ifexist "shell\DrPdfShell.dll"
Source: "shell\DrPdfShell.dll"; DestDir: "{app}"; DestName: "{#ShellDll}"; Flags: ignoreversion uninsrestartdelete; Tasks: explorer
#endif

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon
Name: "{usersendto}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: sendto

[Registry]
#ifexist "shell\DrPdfShell.dll"
; File Explorer: thumbnails for every PDF, and the preview pane when Dr.PDF opens PDFs
Root: HKA; Subkey: "Software\Classes\CLSID\{#ThumbClsid}"; ValueType: string; ValueName: ""; ValueData: "Dr.PDF thumbnails"; Flags: uninsdeletekey; Tasks: explorer
Root: HKA; Subkey: "Software\Classes\CLSID\{#ThumbClsid}\InprocServer32"; ValueType: string; ValueName: ""; ValueData: "{app}\{#ShellDll}"; Tasks: explorer
Root: HKA; Subkey: "Software\Classes\CLSID\{#ThumbClsid}\InprocServer32"; ValueType: string; ValueName: "ThreadingModel"; ValueData: "Apartment"; Tasks: explorer
Root: HKA; Subkey: "Software\Classes\CLSID\{#PreviewClsid}"; ValueType: string; ValueName: ""; ValueData: "Dr.PDF preview"; Flags: uninsdeletekey; Tasks: explorer
Root: HKA; Subkey: "Software\Classes\CLSID\{#PreviewClsid}"; ValueType: string; ValueName: "DisplayName"; ValueData: "Dr.PDF preview"; Tasks: explorer
; (runs in Windows' own preview host process, not in Explorer)
Root: HKA; Subkey: "Software\Classes\CLSID\{#PreviewClsid}"; ValueType: string; ValueName: "AppID"; ValueData: "{{6D2B5079-2F0B-48DD-AB7F-97CEC514D30B}"; Tasks: explorer
Root: HKA; Subkey: "Software\Classes\CLSID\{#PreviewClsid}\InprocServer32"; ValueType: string; ValueName: ""; ValueData: "{app}\{#ShellDll}"; Tasks: explorer
Root: HKA; Subkey: "Software\Classes\CLSID\{#PreviewClsid}\InprocServer32"; ValueType: string; ValueName: "ThreadingModel"; ValueData: "Apartment"; Tasks: explorer
Root: HKA; Subkey: "Software\Microsoft\Windows\CurrentVersion\PreviewHandlers"; ValueType: string; ValueName: "{#PreviewClsid}"; ValueData: "Dr.PDF preview"; Flags: uninsdeletevalue; Tasks: explorer
Root: HKA; Subkey: "Software\Classes\SystemFileAssociations\.pdf\ShellEx\{{e357fccd-a995-4576-b01f-234630154e96}"; ValueType: string; ValueName: ""; ValueData: "{#ThumbClsid}"; Flags: uninsdeletekey; Tasks: explorer
Root: HKA; Subkey: "Software\Classes\Applications\{#AppExe}\ShellEx\{{e357fccd-a995-4576-b01f-234630154e96}"; ValueType: string; ValueName: ""; ValueData: "{#ThumbClsid}"; Tasks: explorer
Root: HKA; Subkey: "Software\Classes\Applications\{#AppExe}\ShellEx\{{8895b1c6-b41f-4c1c-a562-0d564250836f}"; ValueType: string; ValueName: ""; ValueData: "{#PreviewClsid}"; Tasks: explorer
#endif
Root: HKA; Subkey: "Software\Classes\SystemFileAssociations\.pdf\shell\DrPDFEdit"; ValueType: string; ValueName: ""; ValueData: "Edit with Dr.PDF"; Flags: uninsdeletekey; Tasks: pdfmenu
Root: HKA; Subkey: "Software\Classes\SystemFileAssociations\.pdf\shell\DrPDFEdit"; ValueType: string; ValueName: "Icon"; ValueData: "{app}\{#AppExe}"; Tasks: pdfmenu
Root: HKA; Subkey: "Software\Classes\SystemFileAssociations\.pdf\shell\DrPDFEdit\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"" --edit ""%1"""; Tasks: pdfmenu
; lets Windows offer Dr.PDF under "Open with" for PDFs
Root: HKA; Subkey: "Software\Classes\Applications\{#AppExe}\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"" --edit ""%1"""; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\Applications\{#AppExe}\SupportedTypes"; ValueType: string; ValueName: ".pdf"; ValueData: ""
Root: HKA; Subkey: "Software\Classes\.pdf\OpenWithList\{#AppExe}"; ValueType: string; ValueName: ""; ValueData: ""; Flags: uninsdeletekey
; the browser extension was removed in 3.3.7: clear its link left by a 3.3.6 install
Root: HKA; Subkey: "Software\Google\Chrome\NativeMessagingHosts\com.drpdf.host"; ValueType: none; Flags: deletekey
Root: HKA; Subkey: "Software\Microsoft\Edge\NativeMessagingHosts\com.drpdf.host"; ValueType: none; Flags: deletekey

[Run]
Filename: "{app}\{#AppExe}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent
