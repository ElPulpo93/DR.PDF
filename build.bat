@echo off
rem Rebuilds Dr.PDF and its installer (installer\Dr.PDF-Setup.exe)
rem
rem Code signing (recommended for deployment): set these first, then run build.bat
rem   set DRPDF_SIGN_PFX=C:\path\to\your-code-signing-certificate.pfx
rem   set DRPDF_SIGN_PASSWORD=its-password
rem The app, the installer and the uninstaller are then signed. Without them, the build is
rem unsigned (Windows SmartScreen and antivirus tools may then warn about it).
cd /d "%~dp0"
.venv\Scripts\python -m PyInstaller Dr.PDF.spec --noconfirm || exit /b 1
rem File Explorer thumbnails + preview (needs Visual Studio C++; skipped without it)
.venv\Scripts\python shell\build_shell.py
if defined DRPDF_SIGN_PFX (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0sign.ps1" "%~dp0dist\Dr.PDF\Dr.PDF.exe" || exit /b 1
  if exist "%~dp0shell\DrPdfShell.dll" powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0sign.ps1" "%~dp0shell\DrPdfShell.dll" || exit /b 1
  "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" /DSIGN "/Sdrsign=powershell -NoProfile -ExecutionPolicy Bypass -File $q%~dp0sign.ps1$q $f" installer.iss || exit /b 1
) else (
  "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" installer.iss || exit /b 1
)
