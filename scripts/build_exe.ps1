# Builds the two VoiceBridge executables, and the installer when Inno Setup is present.
#
#   VoiceBridge.exe        console-less launcher stub (repo root)
#   vb_bootstrap.exe       post-install runtime builder (installer/)
#   parlaconclaudio-voice-bridge-Setup-<ver>.exe Inno Setup package        (installer/Output/) - optional
#
# Neither exe bundles the app: VoiceBridge.exe starts venv\Scripts\pythonw.exe,
# and vb_bootstrap.exe builds that venv on the target host. App code changes need
# no rebuild here - only launcher/bootstrapper/icon changes do.
#
#   .\scripts\build_exe.ps1              # both exes
#   .\scripts\build_exe.ps1 -Installer   # also compile the .iss (needs Inno Setup 6)

param(
    [switch]$Installer
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Py   = Join-Path $Root "venv\Scripts\python.exe"
$Ico  = Join-Path $Root "assets\voicebridge.ico"
$Work = Join-Path $Root ".work\pyinstaller"

if (-not (Test-Path $Py))  { throw "venv interpreter not found: $Py" }
if (-not (Test-Path $Ico)) { throw "icon not found: $Ico  (regenerate with scripts/make_icon.py)" }

& $Py -m PyInstaller --version | Out-Null
if ($LASTEXITCODE -ne 0) { throw "PyInstaller missing. Run: $Py -m pip install pyinstaller" }

New-Item -ItemType Directory -Force -Path $Work | Out-Null

# Excluded from BOTH exes: these are the app's runtime deps, not the stubs'.
# Without this PyInstaller would try to pull ctranslate2/CUDA into a 20 MB launcher.
$CommonExcludes = @(
    "--exclude-module", "torch",
    "--exclude-module", "numpy",
    "--exclude-module", "PIL",
    "--exclude-module", "tkinter",
    "--exclude-module", "faster_whisper"
)

Write-Host "`n=== 1/2  VoiceBridge.exe (launcher, no console)" -ForegroundColor Cyan
& $Py -m PyInstaller `
    --noconsole --onefile --noconfirm `
    --name VoiceBridge `
    --icon $Ico `
    --distpath $Root `
    --workpath (Join-Path $Work "build") `
    --specpath $Work `
    @CommonExcludes `
    --exclude-module ctranslate2 `
    --exclude-module pystray `
    (Join-Path $Root "scripts\vb_launcher.py")
if ($LASTEXITCODE -ne 0) { throw "launcher build failed (exit $LASTEXITCODE)" }

Write-Host "`n=== 2/2  vb_bootstrap.exe (installer helper, console visible)" -ForegroundColor Cyan
# Console is intentional: pip output is the user's only diagnostic if a wheel fails.
# gpu_probe is compiled IN so --probe-json works during the wizard, before the
# payload is extracted to disk.
& $Py -m PyInstaller `
    --onefile --noconfirm `
    --name vb_bootstrap `
    --icon $Ico `
    --distpath (Join-Path $Root "installer") `
    --workpath (Join-Path $Work "build") `
    --specpath $Work `
    --paths $Root `
    --hidden-import src.core.gpu_probe `
    @CommonExcludes `
    --exclude-module ctranslate2 `
    (Join-Path $Root "installer\vb_bootstrap.py")
if ($LASTEXITCODE -ne 0) { throw "bootstrapper build failed (exit $LASTEXITCODE)" }

foreach ($exe in @((Join-Path $Root "VoiceBridge.exe"), (Join-Path $Root "installer\vb_bootstrap.exe"))) {
    $mb = [math]::Round((Get-Item $exe).Length / 1MB, 1)
    Write-Host "OK  $exe  ($mb MB)" -ForegroundColor Green
}

if (-not $Installer) {
    Write-Host "`n(skipping installer; pass -Installer to compile the .iss)" -ForegroundColor DarkGray
    exit 0
}

Write-Host "`n=== 3/3  parlaconclaudio-voice-bridge-Setup.exe (Inno Setup)" -ForegroundColor Cyan
# Per-user installs land in LOCALAPPDATA and are easy to miss - check it first.
$Iscc = @(
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1

if (-not $Iscc) {
    throw "Inno Setup 6 not found. Install it from https://jrsoftware.org/isdl.php then re-run."
}

& $Iscc (Join-Path $Root "installer\VoiceBridge.iss")
if ($LASTEXITCODE -ne 0) { throw "ISCC failed (exit $LASTEXITCODE)" }

$Setup = Get-ChildItem (Join-Path $Root "installer\Output") -Filter "*.exe" |
         Sort-Object LastWriteTime -Descending | Select-Object -First 1
$mb = [math]::Round($Setup.Length / 1MB, 1)
Write-Host "OK  $($Setup.FullName)  ($mb MB)" -ForegroundColor Green
