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
#define AppVersion     "1.0.11"
#define AppPublisher   "Frisco"
#define AppExeName     "VoiceBridge.exe"
#define BootstrapExe   "vb_bootstrap.exe"
; Scheduled task that starts the bridge at sign-in. Same name as TASK_NAME in
; src/voice_bridge/autostart.py, which creates it (installer, tray toggle).
#define AutostartTask  "parlaconclaudio-voice-bridge"

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
; No Startup-folder shortcut any more: the "autostart" task registers a per-user
; Task Scheduler logon task instead (restart on failure, no time limit), from
; [Code] ApplyAutostart once the venv exists. Toggle later from the tray menu.

[Run]
; The runtime bootstrap is NOT here: [Run] ignores exit codes, so a failed
; bootstrap used to end with "Launch" and a launcher that cannot find the venv.
; It runs from [Code] (CurStepChanged/ssPostInstall -> RunBootstrap), which
; checks the exit code. Launch is offered only if the bootstrap succeeded.
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#AppName}}"; \
  Flags: nowait postinstall skipifsilent; Check: BootstrapSucceeded

[InstallDelete]
; v1.0.3 shipped as AppName "VoiceBridge": same AppId, so upgrading leaves its shortcuts behind
; (double autostart). Remove them; the new ones are created under the renamed AppName.
Type: files;          Name: "{userstartup}\VoiceBridge.lnk"
; v1.0.4-1.0.6 autostart was this Startup shortcut; v1.0.7 uses the logon task.
Type: files;          Name: "{userstartup}\{#AppName}.lnk"
Type: files;          Name: "{autodesktop}\VoiceBridge.lnk"
Type: filesandordirs; Name: "{userprograms}\VoiceBridge"

[UninstallRun]
; Remove the logon task. schtasks directly, not the venv: it may already be gone.
; Exit code 1 (task absent, e.g. autostart was off) is harmless here.
Filename: "{sys}\schtasks.exe"; Parameters: "/Delete /TN ""{#AutostartTask}"" /F"; \
  Flags: runhidden waituntilterminated; RunOnceId: "DeleteAutostartTask"
; Remove the autostart fallback (per-user Run value, written when the task could
; not be registered; RUN_KEY_PATH/RUN_VALUE_NAME in autostart.py). Exit 1 = absent.
Filename: "{sys}\reg.exe"; Parameters: "delete ""HKCU\Software\Microsoft\Windows\CurrentVersion\Run"" /v ""{#AutostartTask}"" /f"; \
  Flags: runhidden waituntilterminated; RunOnceId: "DeleteAutostartRunValue"

[UninstallDelete]
; Created after install, so Inno does not track them. src/ and scripts/ are wiped
; whole rather than per-__pycache__: Python scatters .pyc through every subpackage
; and listing them one by one leaves residue behind the first time one is added.
Type: filesandordirs; Name: "{app}\venv"
Type: filesandordirs; Name: "{app}\src"
Type: filesandordirs; Name: "{app}\scripts"
Type: files;          Name: "{app}\install-state.json"
Type: files;          Name: "{app}\bootstrap.log"
Type: files;          Name: "{app}\voicebridge.log"
Type: files;          Name: "{app}\voicebridge_debug.log"

[CustomMessages]
; No accented letters on purpose: this file stays plain ASCII.
en.BootstrapStatus=Downloading and configuring the runtime (this takes a few minutes)...
it.BootstrapStatus=Download e configurazione del runtime in corso (richiede qualche minuto)...
en.BootstrapFailed=The runtime setup did not complete (exit code %1).%n%n%2%n%nFull log:%n%3%n%n{#AppName} cannot start until this is fixed. To retry:%nStart > {#AppName} > Repair runtime
it.BootstrapFailed=La configurazione del runtime non si e' conclusa (codice di uscita %1).%n%n%2%n%nLog completo:%n%3%n%n{#AppName} non potra' avviarsi finche' non viene risolto. Per riprovare:%nStart > {#AppName} > Repair runtime
en.BootstrapReasonBlocked=Windows blocked a program the setup needs (Smart App Control / Application Control / antivirus): uv.exe and python.exe are unsigned downloads. If Smart App Control is on, turn it off in Windows Security > App & browser control, or allow the file in your antivirus.
it.BootstrapReasonBlocked=Windows ha bloccato un programma necessario al setup (Smart App Control / Controllo applicazioni / antivirus): uv.exe e python.exe sono download senza firma digitale. Se Smart App Control risulta attivo, disattivalo in Sicurezza di Windows > Controllo app e browser, oppure consenti il file nell'antivirus.
en.BootstrapReasonPermission=Access denied while writing the runtime. Quit {#AppName} if it is running (tray icon > Exit) and check that the install folder is not read-only or locked by sync/antivirus. No administrator rights are needed.
it.BootstrapReasonPermission=Accesso negato durante la scrittura del runtime. Chiudi {#AppName} se in esecuzione (icona nel tray > Exit) e verifica che la cartella di installazione non sia in sola lettura o bloccata da sync/antivirus. Non servono diritti di amministratore.
en.BootstrapReasonNoPayload=The application files were not found in the install folder. Run this setup again.
it.BootstrapReasonNoPayload=I file dell'applicazione non sono stati trovati nella cartella di installazione. Esegui di nuovo questo setup.
en.BootstrapReasonGeneric=A download or a package install failed (network, or a package that could not be installed). The log shows the exact error.
it.BootstrapReasonGeneric=Un download o l'installazione di un pacchetto non sono riusciti (rete, oppure pacchetto non installabile). Il log riporta l'errore esatto.
en.BootstrapNoLog=The runtime builder exited (code %1) without writing its log, so it most likely could not run at all: Smart App Control or an antivirus blocking the unsigned vb_bootstrap.exe is the usual cause.%n%nIf Smart App Control is on, turn it off in Windows Security > App & browser control, then run:%nStart > {#AppName} > Repair runtime
it.BootstrapNoLog=Il configuratore del runtime e' terminato (codice %1) senza scrivere il log, quindi con ogni probabilita' non e' riuscito nemmeno a partire: di solito e' Smart App Control o un antivirus che blocca vb_bootstrap.exe, privo di firma digitale.%n%nSe Smart App Control risulta attivo, disattivalo in Sicurezza di Windows > Controllo app e browser, poi esegui:%nStart > {#AppName} > Repair runtime
en.BootstrapNotStarted=Setup could not start the runtime builder:%n%1%n%nWindows error %2: %3%n%nThis usually means Smart App Control (or another application-control policy / antivirus) blocked it: vb_bootstrap.exe is not digitally signed. Turn Smart App Control off in Windows Security > App & browser control, then run:%nStart > {#AppName} > Repair runtime
it.BootstrapNotStarted=Il setup non ha potuto avviare il configuratore del runtime:%n%1%n%nErrore Windows %2: %3%n%nDi solito significa che Smart App Control (o un'altra policy di controllo applicazioni / antivirus) l'ha bloccato: vb_bootstrap.exe non ha firma digitale. Disattiva Smart App Control in Sicurezza di Windows > Controllo app e browser, poi esegui:%nStart > {#AppName} > Repair runtime
en.AutostartFailed=Could not register the sign-in task (code %1). The app works; enable "Start at sign-in" later from its tray menu.
it.AutostartFailed=Impossibile registrare l'avvio all'accesso (codice %1). L'app funziona; attiva "Avvio automatico all'accesso" in seguito dal menu nel tray.

[Code]
const
  { Exit codes of installer/vb_bootstrap.py (EXIT_* there). }
  BOOT_OK         = 0;
  BOOT_NO_PAYLOAD = 2;
  BOOT_PERMISSION = 3;
  BOOT_BLOCKED    = 4;

var
  ProfilePage: TInputOptionWizardPage;
  DetectedInfo: String;
  RecommendedProfile: String;
  BootstrapOk: Boolean;

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

{ Message text with %n turned into real line breaks. }
function Msg(const Text: String): String;
begin
  Result := Text;
  StringChangeEx(Result, '%n', #13#10, True);
end;

{ Size of the bootstrap log, -1 if absent. Compared before/after the run to tell
  "ran and failed" (log grew) from "never got to run" (log untouched). }
function LogSize(const Path: String): Integer;
begin
  if not FileSize(Path, Result) then
    Result := -1;
end;

{ Decision tree, in order:
    1. Exec itself fails       -> Windows refused to start the exe (Smart App Control,
                                  antivirus, policy): BootstrapNotStarted + Win32 error
    2. exit 0                  -> success: the Finished page offers to launch
    3. non-zero, log untouched -> the exe started but died before logging (e.g. the
                                  PyInstaller bootloader was blocked): BootstrapNoLog
    4. non-zero, log written   -> BootstrapFailed, reason picked by exit code
  The console stays visible (SW_SHOW): pip output is the user's only live clue if a
  wheel fails, and hiding it would turn a fixable error into a silent one. On a
  failure the bootstrapper waits for Enter in that console, unless setup is silent. }
procedure RunBootstrap;
var
  Exe, Params, LogPath, Reason, Text: String;
  ResultCode, SizeBefore: Integer;
begin
  BootstrapOk := False;
  Exe := ExpandConstant('{app}\{#BootstrapExe}');
  LogPath := ExpandConstant('{app}\bootstrap.log');
  Params := '--root "' + ExpandConstant('{app}') + '" --profile ' + GetChosenProfile('');
  if WizardSilent then
    Params := Params + ' --no-pause';

  WizardForm.StatusLabel.Caption := CustomMessage('BootstrapStatus');
  SizeBefore := LogSize(LogPath);
  Log('Running bootstrap: ' + Exe + ' ' + Params);

  if not Exec(Exe, Params, ExpandConstant('{app}'), SW_SHOW, ewWaitUntilTerminated, ResultCode) then
  begin
    Log('Bootstrap could not start, Win32 error ' + IntToStr(ResultCode));
    { No line may start with "[" in [Code]: ISCC would read it as a section tag. }
    Text := FmtMessage(CustomMessage('BootstrapNotStarted'), [Exe, IntToStr(ResultCode), SysErrorMessage(ResultCode)]);
    SuppressibleMsgBox(Msg(Text), mbCriticalError, MB_OK, IDOK);
    Exit;
  end;

  Log('Bootstrap exit code ' + IntToStr(ResultCode));
  if ResultCode = BOOT_OK then
  begin
    BootstrapOk := True;
    Exit;
  end;

  if LogSize(LogPath) = SizeBefore then
  begin
    Text := FmtMessage(CustomMessage('BootstrapNoLog'), [IntToStr(ResultCode)]);
    SuppressibleMsgBox(Msg(Text), mbCriticalError, MB_OK, IDOK);
    Exit;
  end;

  case ResultCode of
    BOOT_NO_PAYLOAD: Reason := CustomMessage('BootstrapReasonNoPayload');
    BOOT_PERMISSION: Reason := CustomMessage('BootstrapReasonPermission');
    BOOT_BLOCKED:    Reason := CustomMessage('BootstrapReasonBlocked');
  else
    Reason := CustomMessage('BootstrapReasonGeneric');
  end;
  Text := FmtMessage(CustomMessage('BootstrapFailed'), [IntToStr(ResultCode), Reason, LogPath]);
  SuppressibleMsgBox(Msg(Text), mbError, MB_OK, IDOK);
end;

{ Autostart = per-user logon task, created/removed by src.voice_bridge.autostart
  (the same code as the tray toggle). Needs the venv, so it runs only after a
  successful bootstrap. Unchecked task -> disable, so an upgrade honours it.
  Best effort: a failure is logged and shown, never fails the install. }
procedure ApplyAutostart;
var
  PyW, Action, Params: String;
  ResultCode: Integer;
begin
  PyW := ExpandConstant('{app}\venv\Scripts\pythonw.exe');
  if WizardIsTaskSelected('autostart') then
    Action := 'enable'
  else
    Action := 'disable';
  Params := '-m src.voice_bridge.autostart ' + Action + ' --root "' + ExpandConstant('{app}') + '"';
  Log('Autostart: ' + PyW + ' ' + Params);
  if not Exec(PyW, Params, ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, ResultCode) then
  begin
    Log('Autostart could not start, Win32 error ' + IntToStr(ResultCode));
    ResultCode := -1;
  end
  else
    Log('Autostart exit code ' + IntToStr(ResultCode));
  if (ResultCode <> 0) and (Action = 'enable') then
    SuppressibleMsgBox(FmtMessage(CustomMessage('AutostartFailed'), [IntToStr(ResultCode)]),
      mbError, MB_OK, IDOK);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    RunBootstrap;
    if BootstrapOk then
      ApplyAutostart;
  end;
end;

{ Check: of the "Launch" postinstall entry. }
function BootstrapSucceeded: Boolean;
begin
  Result := BootstrapOk;
end;
