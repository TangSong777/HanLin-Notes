#define MyAppName "HanLin-Notes"
#define SetupIntroZh "在本机整理笔记、文章和图片。升级便携版时，可以选择原来的程序文件夹。"
#define SetupIntroEn "Organize notes, articles and images on this computer. To upgrade a portable copy, select its existing folder."

#ifndef PayloadDir
  #error Use tools/make-portable.py to prepare PayloadDir.
#endif

#ifndef MyAppVersion
  #error Use tools/make-portable.py to read the project version.
#endif

#include "Brand\HanLinWizard.iss"

[Setup]
AppId={{F4E2BFC9-54D1-479E-A6C2-F716EE8932D7}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=TangSong777
VersionInfoProductName={#MyAppName}
VersionInfoVersion={#MyAppVersion}
DefaultDirName={localappdata}\Programs\HanLin\HanLin-Notes
DefaultGroupName={#MyAppName}
PrivilegesRequired=lowest
OutputBaseFilename=HanLin-Notes-Setup-{#MyAppVersion}
SetupIconFile=..\assets\hanlin-notes.ico
UninstallDisplayIcon={app}\HanLin-Notes.exe
CloseApplicationsFilter=*.exe,*.dll,*.pyd

[Files]
Source: "{#PayloadDir}\HanLin-Notes.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#PayloadDir}\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\LICENSE"; DestDir: "{app}"; DestName: "LICENSE.txt"; Flags: ignoreversion

[Icons]
Name: "{group}\HanLin-Notes"; Filename: "{app}\HanLin-Notes.exe"; WorkingDir: "{app}"

[Run]
Filename: "{app}\HanLin-Notes.exe"; WorkingDir: "{app}"; Description: "{cm:HanLinLaunch,{#MyAppName}}"; Flags: postinstall nowait skipifsilent
