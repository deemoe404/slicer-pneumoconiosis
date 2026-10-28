#ifndef Payload
  #error Payload must point to the staged application
#endif
#ifndef Output
  #error Output must point to the output directory
#endif
#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif
#ifndef BuildId
  #define BuildId "local"
#endif
#ifndef MinimumWindowsVersion
  #error MinimumWindowsVersion must come from inputs.json
#endif
#ifndef MinimumWindowsRelease
  #error MinimumWindowsRelease must come from inputs.json
#endif

[Setup]
AppId={{FD48D678-6B2D-4BDF-9664-84203732C037}
AppName=尘肺胸片标注
AppVersion={#AppVersion}
AppVerName=尘肺胸片标注 {#AppVersion}
AppPublisher=SUEP CT Research
DefaultDirName={autopf}\PneumoconiosisAnnotator
DefaultGroupName=尘肺胸片标注
UninstallDisplayIcon={app}\PneumoconiosisAnnotator.exe
OutputDir={#Output}
OutputBaseFilename=PneumoconiosisAnnotator-{#AppVersion}-windows-x64-{#BuildId}-Setup
ArchitecturesAllowed=x64os
ArchitecturesInstallIn64BitMode=x64os
; Slicer's per-process UTF-8 code page requires Windows 10 1903.
; The build runner's Windows Server version is not the application's minimum.
MinVersion={#MinimumWindowsVersion}
PrivilegesRequired=admin
Compression=lzma2/normal
SolidCompression=yes
WizardStyle=modern
DisableProgramGroupPage=yes
CloseApplications=yes
RestartApplications=no
LicenseFile={#Payload}\LICENSE
SetupLogging=yes

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Messages]
chinesesimplified.WindowsVersionNotSupported=本程序需要 {#MinimumWindowsRelease} 或更高版本（64 位），系统内部版本至少为 {#MinimumWindowsVersion}。
chinesesimplified.WinVersionTooLowError=本程序需要 {#MinimumWindowsRelease} 或更高版本（64 位），系统内部版本至少为 {#MinimumWindowsVersion}。
english.WindowsVersionNotSupported=This program requires 64-bit {#MinimumWindowsRelease} or later (minimum OS version {#MinimumWindowsVersion}).
english.WinVersionTooLowError=This program requires 64-bit {#MinimumWindowsRelease} or later (minimum OS version {#MinimumWindowsVersion}).

[Files]
Source: "{#Payload}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\尘肺胸片标注"; Filename: "{app}\PneumoconiosisAnnotator.exe"
Name: "{autodesktop}\尘肺胸片标注"; Filename: "{app}\PneumoconiosisAnnotator.exe"

[Run]
Filename: "{app}\PneumoconiosisAnnotator.exe"; Description: "启动尘肺胸片标注"; Flags: nowait postinstall skipifsilent runasoriginaluser

[Code]
function NextButtonClick(CurPageID: Integer): Boolean;
var
  I: Integer;
  InstallPath: String;
begin
  Result := True;
  if CurPageID <> wpSelectDir then Exit;
  InstallPath := WizardDirValue;
  for I := 1 to Length(InstallPath) do
    if Ord(InstallPath[I]) > 127 then begin
      MsgBox('Slicer 安装目录需使用英文字母路径。标注数据和胸片目录可以使用中文。', mbError, MB_OK);
      Result := False;
      Exit;
    end;
end;
