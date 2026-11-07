; Inno Setup script — compiled by build_installer.bat (needs Inno Setup 6)
#define AppName "LocalChat"
#define AppVersion "1.9.0"

[Setup]
AppId={{B7C2D5A1-6E3F-4D1B-9A52-0A1C2D3E4F50}
AppName={#AppName}
AppVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
PrivilegesRequired=lowest
OutputDir=..\build\installer
OutputBaseFilename=LocalChat-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayName={#AppName}
WizardStyle=modern
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\LocalChat.exe

[Files]
Source: "..\build\dist\LocalChat\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\LocalChat.exe"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\LocalChat.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Run]
Filename: "{app}\LocalChat.exe"; Description: "Launch LocalChat"; Flags: nowait postinstall skipifsilent

[Code]
function OllamaInstalled: Boolean;
begin
  Result := FileExists(ExpandConstant('{localappdata}\Programs\Ollama\ollama.exe'))
         or FileExists(ExpandConstant('{commonpf}\Ollama\ollama.exe'));
end;

function WebView2Installed: Boolean;
begin
  Result := RegKeyExists(HKLM32, 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}')
         or RegKeyExists(HKCU, 'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}');
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if (CurStep = ssPostInstall) and (not WebView2Installed) then
    MsgBox('Microsoft Edge WebView2 Runtime was not detected. Windows 11 has it by default; on Windows 10 install it from https://developer.microsoft.com/microsoft-edge/webview2/ so the app window can open.',
           mbInformation, MB_OK);
  if (CurStep = ssPostInstall) and (not OllamaInstalled) then
    MsgBox('Ollama was not found on this computer. LocalChat needs Ollama to run models. ' +
           'Install it from https://ollama.com/download and then start LocalChat.',
           mbInformation, MB_OK);
end;
// User data in %LOCALAPPDATA%\LocalChat (memory, settings, identity) is intentionally NOT removed on uninstall.
