#define AppName "直播演播厅"
#define AppVersion "1.0.0"
#define AppExe "LiveStage.exe"

[Setup]
AppId={{8E2C1B34-6E52-4C8E-9E0B-5B1C7E2A9D41}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=LiveStage
DefaultDirName={localappdata}\Programs\LiveStage
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\installer
OutputBaseFilename=直播演播厅-安装程序
SetupIconFile=..\assets\app.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
InfoBeforeFile=说明.txt
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "..\dist\LiveStage\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; 微软提供可再分发的 WebView2 引导程序，缺失时静默安装
Source: "vendor\MicrosoftEdgeWebview2Setup.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"; IconFilename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; IconFilename: "{app}\{#AppExe}"
Name: "{app}\安装虚拟声卡（VB-Cable）"; Filename: "https://vb-audio.com/Cable/"

[Run]
Filename: "{app}\{#AppExe}"; Description: "立即启动 直播演播厅"; Flags: nowait postinstall skipifsilent

[Code]
function WebView2Installed(): Boolean;
var
  Version: String;
begin
  Result :=
    RegQueryStringValue(HKLM, 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Version)
    or RegQueryStringValue(HKLM, 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Version)
    or RegQueryStringValue(HKCU, 'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Version);
end;

function VBCableInstalled(): Boolean;
begin
  Result :=
    RegKeyExists(HKLM, 'SOFTWARE\VB-Audio\Cable')
    or RegKeyExists(HKLM64, 'SOFTWARE\VB-Audio\Cable');
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Code: Integer;
begin
  if CurStep <> ssPostInstall then
    exit;

  if not WebView2Installed() then
  begin
    WizardForm.StatusLabel.Caption := '正在安装 WebView2 运行库…';
    ShellExec('', ExpandConstant('{tmp}\MicrosoftEdgeWebview2Setup.exe'),
      '/silent /install', '', SW_SHOW, ewWaitUntilTerminated, Code);
  end;

  if not VBCableInstalled() then
  begin
    if MsgBox('还差一个虚拟声卡（VB-Audio Virtual Cable），AI 声音要靠它送进直播间。' + #13#10 + #13#10 +
              '点「是」打开官方网站下载。下载后解压，右键 VBCABLE_Setup_x64.exe 选择「以管理员身份运行」，' +
              '装完重启电脑，再打开本程序。' + #13#10 + #13#10 +
              '（VB-Cable 的授权不允许把它打包进别的安装包，所以这里只做引导。）',
              mbConfirmation, MB_YESNO) = IDYES then
      ShellExec('open', 'https://vb-audio.com/Cable/', '', '', SW_SHOWNORMAL, ewNoWait, Code);
  end;
end;
