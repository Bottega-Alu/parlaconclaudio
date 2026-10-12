; VoiceBridge installer - Inno Setup 6
;
; Ships ONLY the app payload (~20 MB). Python, the venv, every dependency and the
; Whisper model are fetched on the target host by vb_bootstrap.exe, which picks a
; GPU or CPU profile from what the machine actually has. That keeps one installer
; valid for an RTX box and a GPU-less laptop alike.
;
; Build:  ISCC.exe installer\VoiceBridge.iss
; Output: installer\Output\parlaconclaudio-voice-bridge-Setup-<version>.exe

#define AppName        "parlaconclaudio - voice-bridge"
#define AppVersion     "1.0.4"
#define AppPublisher   "Frisco"
#define AppExeName     "VoiceBridge.exe"
#define BootstrapExe   "vb_bootstrap.exe"

[Setup]
AppId={{7B3C1E42-9D5A-4F18-A6C7-2E8B4D1F9A03}
AppName={#AppName}
UninstallDisplayName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
VersionInfoVersion={#AppVersion}

; Per-user install: no UAC prompt, and - crucially - the venv created after the
; install stays writable, so repair and upgrade never need elevation.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
; Folder name kept as "VoiceBridge" on purpose: changing it would orphan the existing venv.
DefaultDirName={localappdata}\Programs\VoiceBridge
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
AllowNoIcons=yes

OutputDir=Output
OutputBaseFilename=parlaconclaudio-voice-bridge-Setup-{#AppVersion}
SetupIconFile=..\assets\voicebridge.ico
UninstallDisplayIcon={app}\assets\voicebridge.ico
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "en"; MessagesFile: "compiler:Default.isl"
Name: "it"; MessagesFile: "compiler:Languages\Italian.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "autostart";   Description: "Start {#AppName} when I sign in"; GroupDescription: "Startup"

[Files]
Source: "..\{#AppExeName}";  DestDir: "{app}"; Flags: ignoreversion
Source: "{#BootstrapExe}";   DestDir: "{app}"; Flags: ignoreversion
Source: "..\src\*";          DestDir: "{app}\src";     Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "__pycache__,*.pyc"
Source: "..\scripts\*";      DestDir: "{app}\scripts"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "__pycache__,*.pyc"
Source: "..\sounds\*";       DestDir: "{app}\sounds";  Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\assets\*";       DestDir: "{app}\assets";  Flags: ignoreversion
Source: "..\README.md";      DestDir: "{app}"; Flags: ignoreversion
Source: "..\LICENSE";        DestDir: "{app}"; Flags: ignoreversion
; Optional: drop uv.exe in installer\bin\ to make setup work fully offline-capable
; (minus the wheels themselves). Without it the bootstrapper fetches uv itself.
Source: "bin\uv.exe";        DestDir: "{app}\bin"; Flags: ignoreversion skipifsourcedoesntexist

[Icons]
Name: "{group}\{#AppName}";           Filename: "{app}\{#AppExeName}"; IconFilename: "{app}\assets\voicebridge.ico"
Name: "{group}\Repair runtime";       Filename: "{app}\{#BootstrapExe}"; Parameters: "--root ""{app}"""; IconFilename: "{app}\assets\voicebridge.ico"
Name: "{autodesktop}\{#AppName}";     Filename: "{app}\{#AppExeName}"; IconFilename: "{app}\assets\voicebridge.ico"; Tasks: desktopicon
Name: "{userstartup}\{#AppName}";     Filename: "{app}\{#AppExeName}"; IconFilename: "{app}\assets\voicebridge.ico"; Tasks: autostart

[Run]
; The heavy lifting. Console stays visible: pip output is the user's only clue
; if a wheel fails, and hiding it would turn a fixable error into a silent one.
Filename: "{app}\{#BootstrapExe}"; \
  Parameters: "--root ""{app}"" --profile {code:GetChosenProfile}"; \
  StatusMsg: "Downloading and configuring the runtime (this takes a few minutes)..."; \
  Flags: waituntilterminated

Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#AppName}}"; \
  Flags: nowait postinstall skipifsilent

[InstallDelete]
; v1.0.3 shipped as AppName "VoiceBridge": same AppId, so upgrading leaves its shortcuts behind
; (double autostart). Remove them; the new ones are created under the renamed AppName.
Type: files;          Name: "{userstartup}\VoiceBridge.lnk"
Type: files;          Name: "{autodesktop}\VoiceBridge.lnk"
Type: filesandordirs; Name: "{userprograms}\VoiceBridge"

[UninstallDelete]
; Created after install, so Inno does not track them. src/ and scripts/ are wiped
; whole rather than per-__pycache__: Python scatters .pyc through every subpackage
; and listing them one by one leaves residue behind the first time one is added.
Type: filesandordirs; Name: "{app}\venv"
Type: filesandordirs; Name: "{app}\src"
Type: filesandordirs; Name: "{app}\scripts"
Type: files;          Name: "{app}\install-state.json"
Type: files;          Name: "{app}\voicebridge.log"
Type: files;          Name: "{app}\voicebridge_debug.log"

[Code]
var
  ProfilePage: TInputOptionWizardPage;
  DetectedInfo: String;
  RecommendedProfile: String;

{ Minimal JSON string reader - the probe output is a flat, machine-written object,
  so a full parser would be overkill here. }
function ReadJsonString(const Json, Key: String): String;
var
  P, Q: Integer;
  Needle: String;
begin
  Result := '';
  Needle := '"' + Key + '":';
  P := Pos(Needle, Json);
  if P = 0 then Exit;
  P := P + Length(Needle);
  while (P <= Length(Json)) and (Json[P] = ' ') do Inc(P);
  if (P <= Length(Json)) and (Json[P] = '"') then
  begin
    Inc(P);
    Q := P;
    while (Q <= Length(Json)) and (Json[Q] <> '"') do Inc(Q);
    Result := Copy(Json, P, Q - P);
  end
  else
  begin
    Q := P;
    while (Q <= Length(Json)) and (Json[Q] <> ',') and (Json[Q] <> #10) and (Json[Q] <> '}') do Inc(Q);
    Result := Trim(Copy(Json, P, Q - P));
  end;
end;

{ Run the shared probe from the extracted payload and read back its verdict. }
procedure DetectHardware;
var
  TmpJson, Json, GpuName, Vram, Reason: String;
  RawJson: AnsiString;  { LoadStringFromFile only accepts AnsiString }
  ResultCode: Integer;
begin
  RecommendedProfile := 'cpu';
  DetectedInfo := 'Hardware detection did not run.';

  ExtractTemporaryFile('{#BootstrapExe}');
  TmpJson := ExpandConstant('{tmp}\vbprobe.json');

  if not Exec(ExpandConstant('{tmp}\{#BootstrapExe}'),
              '--root "' + ExpandConstant('{app}') + '" --probe-json "' + TmpJson + '"',
              '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
  begin
    DetectedInfo := 'Could not run hardware detection; defaulting to the CPU profile.';
    Exit;
  end;

  if not LoadStringFromFile(TmpJson, RawJson) then Exit;
  Json := String(RawJson);

  RecommendedProfile := ReadJsonString(Json, 'recommended');
  if RecommendedProfile = '' then RecommendedProfile := 'cpu';

  GpuName := ReadJsonString(Json, 'gpu_name');
  Vram    := ReadJsonString(Json, 'vram_mb');
  Reason  := ReadJsonString(Json, 'reason');

  DetectedInfo := 'Detected: ' + GpuName + ' (' + Vram + ' MB VRAM)' + #13#10 +
                  'Model: ' + ReadJsonString(Json, 'model') +
                  '  Precision: ' + ReadJsonString(Json, 'compute_type') + #13#10 +
                  Reason;
end;

procedure InitializeWizard;
begin
  ProfilePage := CreateInputOptionPage(wpSelectTasks,
    'Runtime profile',
    'Choose what VoiceBridge should download for this machine.',
    'Setup inspected your hardware. The recommended option is preselected; ' +
    'change it only if you know better.',
    True, False);

  ProfilePage.Add('Automatic - use the detected hardware (recommended)');
  ProfilePage.Add('Local GPU - force CUDA (adds ~1.6 GB of CUDA kernels)');
  ProfilePage.Add('CPU only - slower transcription, no CUDA download');
  ProfilePage.Add('Cloud only - Groq / Deepgram, smallest download, needs API keys');
  ProfilePage.SelectedValueIndex := 0;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  // Detect once, right before showing the choice page.
  if CurPageID = wpSelectTasks then
  begin
    WizardForm.StatusLabel.Caption := 'Inspecting hardware...';
    DetectHardware;
    ProfilePage.SubCaptionLabel.Caption := DetectedInfo;
    if RecommendedProfile = 'gpu' then
      ProfilePage.CheckListBox.ItemCaption[0] :=
        'Automatic - local GPU detected (recommended)'
    else
      ProfilePage.CheckListBox.ItemCaption[0] :=
        'Automatic - no usable GPU, CPU will be used (recommended)';
  end;
end;

function GetChosenProfile(Param: String): String;
begin
  case ProfilePage.SelectedValueIndex of
    1: Result := 'gpu';
    2: Result := 'cpu';
    3: Result := 'cloud';
  else
    Result := 'auto';
  end;
end;
