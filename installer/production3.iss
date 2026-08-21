#define MyAppName "똑딱이 생산3팀 납기 통합조회"
#define MyAppVersion "0.1.5"
#define MyAppExeName "gui_app_pyside6.exe"
#define MySourceDir "..\dist\production3"

[Setup]
AppId={{B6E3E4AE-D430-4A48-9C98-3FD042E4C4A9}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=생산기획팀 RD
AppPublisherURL=https://github.com/interojo-160907/3-ddokddak
AppSupportURL=https://github.com/interojo-160907/3-ddokddak
AppUpdatesURL=https://github.com/interojo-160907/3-ddokddak/releases/latest
AppComments=생산3팀 전용 납기 통합조회 프로그램
DefaultDirName={localappdata}\Programs\DdokddakProduction3
DefaultGroupName={#MyAppName}
OutputBaseFilename=ddokddak-production3-setup-{#MyAppVersion}
OutputDir=output
Compression=zip
SolidCompression=no
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64compatible
ArchitecturesAllowed=x64compatible
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}
SetupIconFile=..\assets\ddokddak_app_icon.ico
WizardStyle=modern
WizardSizePercent=110
WizardImageFile=wizard_large.bmp
WizardSmallImageFile=wizard_small.bmp
WizardImageStretch=yes
DisableWelcomePage=no
DisableProgramGroupPage=yes
ShowLanguageDialog=no
CloseApplications=force
RestartApplications=no
VersionInfoCompany=생산기획팀 RD
VersionInfoDescription=생산3팀 전용 똑딱이 설치 프로그램
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyAppVersion}
VersionInfoVersion={#MyAppVersion}

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"

[Files]
Source: "{#MySourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"; Tasks: desktopicon
Name: "{userstartup}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"; Tasks: startup

[Tasks]
Name: "desktopicon"; Description: "바탕화면에 똑딱이 바로가기 만들기"; GroupDescription: "바로가기 및 자동 실행:"; Flags: checkedonce
Name: "startup"; Description: "Windows 시작 시 똑딱이 자동 실행"; GroupDescription: "바로가기 및 자동 실행:"; Flags: checkedonce

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "프로그램 실행"; Flags: nowait postinstall skipifsilent

[Code]
const
  RegistryPath = 'Software\Interojo\DdokddakProduction3';
  DataRootValue = 'DataRoot';
  ManagementApiUrlValue = 'ManagementApiUrl';
  ManagementApiUrl = 'https://script.google.com/macros/s/AKfycbzCTUmI8oBaj9HJXlztzYKZ8OU47XFlMUKGCtIzKZMYMIlkeOvap5AWHmmfCPbWpAl42A/exec';

var
  DataDirPage: TInputDirWizardPage;
  BrandLabel: TNewStaticText;

function SavedDataRoot(): String;
begin
  if not RegQueryStringValue(HKCU, RegistryPath, DataRootValue, Result) then
    Result := 'C:\똑딱이 생산3팀 API DATA';
end;

procedure InitializeWizard();
begin
  WizardForm.Caption := '똑딱이 설치';
  WizardForm.Font.Name := '맑은 고딕';
  WizardForm.Font.Size := 9;
  WizardForm.Color := $00FAF7F2;
  WizardForm.MainPanel.Color := clWhite;
  WizardForm.PageNameLabel.Font.Name := '맑은 고딕';
  WizardForm.PageNameLabel.Font.Size := 13;
  WizardForm.PageNameLabel.Font.Style := [fsBold];
  WizardForm.PageNameLabel.Font.Color := $00D66D0A;
  WizardForm.PageDescriptionLabel.Font.Name := '맑은 고딕';
  WizardForm.PageDescriptionLabel.Font.Color := $00746B63;
  WizardForm.WelcomeLabel1.Caption := '생산3팀 똑딱이 설치';
  WizardForm.WelcomeLabel1.Font.Name := '맑은 고딕';
  WizardForm.WelcomeLabel1.Font.Size := 18;
  WizardForm.WelcomeLabel1.Font.Style := [fsBold];
  WizardForm.WelcomeLabel1.Font.Color := $00D66D0A;
  WizardForm.WelcomeLabel2.Caption :=
    '납기 통합조회 업무 환경을 이 PC에 준비합니다.' + #13#10 + #13#10 +
    '설치 과정에서 데이터 저장 위치를 선택할 수 있으며,' + #13#10 +
    '업데이트 시에도 선택한 위치가 그대로 유지됩니다.';

  BrandLabel := TNewStaticText.Create(WizardForm);
  BrandLabel.Parent := WizardForm;
  BrandLabel.Caption := '생산기획팀 / RD   |   v{#MyAppVersion}';
  BrandLabel.Font.Name := '맑은 고딕';
  BrandLabel.Font.Size := 8;
  BrandLabel.Font.Color := $00877D73;
  BrandLabel.Left := ScaleX(18);
  BrandLabel.Top := WizardForm.ClientHeight - ScaleY(27);
  BrandLabel.Anchors := [akLeft, akBottom];

  DataDirPage := CreateInputDirPage(
    wpSelectDir,
    '데이터 저장 위치',
    '수집 데이터와 리드지 자료를 저장할 폴더를 선택해 주세요.',
    '기본 위치 사용을 권장합니다. 업데이트 후에도 선택한 위치는 그대로 유지됩니다.',
    False,
    ''
  );
  DataDirPage.Add('');
  DataDirPage.Values[0] := SavedDataRoot();
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    RegWriteStringValue(HKCU, RegistryPath, DataRootValue, DataDirPage.Values[0]);
    RegWriteStringValue(HKCU, RegistryPath, ManagementApiUrlValue, ManagementApiUrl);
  end;
end;

function SafeDataRoot(const PathValue: String): Boolean;
var
  Expanded: String;
begin
  Expanded := RemoveBackslashUnlessRoot(ExpandFileName(PathValue));
  Result := (Length(Expanded) > 3) and (ExtractFileDrive(Expanded) + '\' <> Expanded);
end;

procedure DeleteAppOwnedData(const DataRoot: String);
begin
  if not SafeDataRoot(DataRoot) then
    Exit;
  DelTree(AddBackslash(DataRoot) + 'bom', True, True, True);
  DelTree(AddBackslash(DataRoot) + 'process-status', True, True, True);
  DelTree(AddBackslash(DataRoot) + 'production-performance', True, True, True);
  DelTree(AddBackslash(DataRoot) + 'aps', True, True, True);
  DelTree(AddBackslash(DataRoot) + 'item-codes', True, True, True);
  DelTree(AddBackslash(DataRoot) + 'settings', True, True, True);
  DelTree(AddBackslash(DataRoot) + '리드지 미리보기 캐시', True, True, True);
  DelTree(AddBackslash(DataRoot) + '리드지 벡터 미리보기 캐시', True, True, True);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataRoot: String;
begin
  if CurUninstallStep = usUninstall then
  begin
    DataRoot := SavedDataRoot();
    DeleteAppOwnedData(DataRoot);
    RegDeleteKeyIncludingSubkeys(HKCU, RegistryPath);
    MsgBox(
      'API 수집 데이터와 캐시는 삭제했습니다.' + #13#10 +
      '리드지 PDF 백업, 리드지 수동 등록, 리드지 이미지 자료는 보존했습니다.',
      mbInformation,
      MB_OK
    );
  end;
end;
