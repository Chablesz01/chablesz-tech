Unicode true
!include "MUI2.nsh"
!include "LogicLib.nsh"
!include "WinVer.nsh"
!include "x64.nsh"
Name "ChableszShow Voice & Camera v17"
OutFile "..\artifacts\ChableszShow-Voice-Camera-v17.exe"
InstallDir "$LOCALAPPDATA\ChableszShowVoiceCamera\v17"
RequestExecutionLevel user
SetCompressor /SOLID lzma
!define MUI_ABORTWARNING
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"
Function .onInit
  ${IfNot} ${AtLeastWin10}
    MessageBox MB_ICONSTOP "ChableszShow requires Windows 10 or Windows 11."
    Abort
  ${EndIf}
  ${IfNot} ${RunningX64}
    MessageBox MB_ICONSTOP "This installer requires 64-bit Windows."
    Abort
  ${EndIf}
  IfFileExists "$INSTDIR\app\app.py" 0 fresh
    MessageBox MB_ICONSTOP "Voice & Camera v17 is already installed. Close it and uninstall Voice & Camera v17 before reinstalling. Your saved libraries and the earlier app are retained."
    Abort
  fresh:
FunctionEnd
Section "ChableszShow"
  SetOutPath "$INSTDIR"
  File /r "..\payload\*"
  DetailPrint "Installing the included Microsoft WebView2 runtime. No setup downloads are required."
  ExecWait '$\"$INSTDIR\prerequisites\MicrosoftEdgeWebView2RuntimeInstallerX64.exe$\" /silent /install' $0
  ; An existing runtime can return an already-installed exit code; verify the registry instead.
  ReadRegStr $1 HKCU "Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" "pv"
  ${If} $1 == ""
    SetRegView 32
    ReadRegStr $1 HKLM "Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" "pv"
    SetRegView lastused
  ${EndIf}
  ${If} $1 == ""
  ${OrIf} $1 == "0.0.0.0"
    MessageBox MB_ICONSTOP "Microsoft WebView2 setup did not complete. ChableszShow has not been registered. Contact support with setup exit code $0."
    SetErrorLevel 1
    Abort
  ${EndIf}
  SetOutPath "$INSTDIR\app"
  CreateShortcut "$DESKTOP\ChableszShow Voice Camera.lnk" "$INSTDIR\runtime\pythonw.exe" '$\"$INSTDIR\app\launcher.py$\"'
  CreateDirectory "$SMPROGRAMS\ChableszShow Voice Camera"
  CreateShortcut "$SMPROGRAMS\ChableszShow Voice Camera\ChableszShow.lnk" "$INSTDIR\runtime\pythonw.exe" '$\"$INSTDIR\app\launcher.py$\"'
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\ChableszShowVoiceCamera17" "DisplayName" "ChableszShow Voice & Camera v17"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\ChableszShowVoiceCamera17" "DisplayVersion" "17"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\ChableszShowVoiceCamera17" "UninstallString" '$\"$INSTDIR\Uninstall.exe$\"'
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\ChableszShowVoiceCamera17" "InstallLocation" "$INSTDIR"
SectionEnd
Section "Uninstall"
  ; Only remove the exact dedicated version directory. Never recurse into a chosen user folder.
  ${If} $INSTDIR != "$LOCALAPPDATA\ChableszShowVoiceCamera\v17"
    MessageBox MB_ICONSTOP "Unexpected installation directory. Uninstall stopped to protect your files."
    Abort
  ${EndIf}
  Delete "$DESKTOP\ChableszShow Voice Camera.lnk"
  RMDir /r "$SMPROGRAMS\ChableszShow Voice Camera"
  RMDir /r "$INSTDIR\app"
  RMDir /r "$INSTDIR\runtime"
  RMDir /r "$INSTDIR\prerequisites"
  Delete "$INSTDIR\payload-sha256.json"
  Delete "$INSTDIR\Uninstall.exe"
  RMDir "$INSTDIR"
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\ChableszShowVoiceCamera17"
  ; APPDATA libraries, original desktop app, and old ZIP archives are retained.
SectionEnd
