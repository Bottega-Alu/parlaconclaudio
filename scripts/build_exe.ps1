# Builds the two VoiceBridge executables, and the installer when Inno Setup is present.
#
#   VoiceBridge.exe        console-less launcher stub (repo root)
#   vb_bootstrap.exe       post-install runtime builder (installer/)
#   parlaconclaudio-voice-bridge-Setup-<ver>.exe Inno Setup package        (installer/Output/) - optional
#
# Neither exe bundles the app: VoiceBridge.exe starts the venv's base pythonw.exe
# (src/voice_bridge/gui_python.py, compiled in),
# and vb_bootstrap.exe builds that venv on the target host. App code changes need
# no rebuild here - only launcher/bootstrapper/icon changes do.
#
#   .\scripts\build_exe.ps1              # both exes
#   .\scripts\build_exe.ps1 -Installer   # also compile the .iss (needs Inno Setup 6);
#                                        # first fetches the pinned uv into installer\bin\
#                                        # (SHA-256 checked) - the installer must ship it
#   .\scripts\build_exe.ps1 -Installer -Python C:\path\to\venv\Scripts\python.exe
#                                        # e.g. from a git worktree, which has no venv\

param(
    [switch]$Installer,
    [string]$Python
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Py   = if ($Python) { $Python } else { Join-Path $Root "venv\Scripts\python.exe" }
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
    --paths $Root `
    --hidden-import src.voice_bridge.gui_python `
    @CommonExcludes `
    --exclude-module ctranslate2 `
    --exclude-module pystray `
    (Join-Path $Root "scripts\vb_launcher.py")
if ($LASTEXITCODE -ne 0) { throw "launcher build failed (exit $LASTEXITCODE)" }

Write-Host "`n=== 2/2  vb_bootstrap.exe (installer helper, console visible)" -ForegroundColor Cyan
# Console is intentional: pip output is the user's only diagnostic if a wheel fails.
# gpu_probe is compiled IN so --probe-json works during the wizard, before the
# payload is extracted to disk. voice_hooks is imported at module load (shared
# hook parsing with the app), so it must be compiled in for the same reason.
& $Py -m PyInstaller `
    --onefile --noconfirm `
    --name vb_bootstrap `
    --icon $Ico `
    --distpath (Join-Path $Root "installer") `
    --workpath (Join-Path $Work "build") `
    --specpath $Work `
    --paths $Root `
    --hidden-import src.core.gpu_probe `
    --hidden-import src.core.voice_hooks `
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

Write-Host "`n=== uv (pinned, shipped as {app}\bin\uv.exe)" -ForegroundColor Cyan
# The bootstrapper has no download fallback: the installer must carry uv. Fetched
# here from the GitHub release and accepted only if the zip matches the SHA-256
# below (the value published as uv-x86_64-pc-windows-msvc.zip.sha256). A cached
# installer\bin\uv.exe is reused only if it is byte-identical to the pinned one.
# To bump uv: change all three values, then re-run scripts/lock_deps.py.
$UvVersion   = "0.11.3"
$UvZipSha256 = "ae681c0aaec7cc96af184648cb88d73f8393ed60fa5880abdd6bdb910f9b227c"
$UvExeSha256 = "07876908e19cf9a875a01d3b702c89b25ded154cc736079feeb4ef78a8f4ca64"
$UvBin = Join-Path $Root "installer\bin"
$UvExe = Join-Path $UvBin "uv.exe"

function Test-PinnedUv {
    (Test-Path $UvExe) -and ((Get-FileHash $UvExe -Algorithm SHA256).Hash.ToLower() -eq $UvExeSha256)
}

if (Test-PinnedUv) {
    Write-Host "OK  $UvExe (uv $UvVersion, cached)" -ForegroundColor Green
} else {
    $UvUrl  = "https://github.com/astral-sh/uv/releases/download/$UvVersion/uv-x86_64-pc-windows-msvc.zip"
    $UvTmp  = Join-Path $Work "uv-$UvVersion"
    $UvZip  = Join-Path $UvTmp "uv.zip"
    Remove-Item -Recurse -Force $UvTmp -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force -Path $UvTmp, $UvBin | Out-Null
    Write-Host "downloading $UvUrl"
    Invoke-WebRequest -Uri $UvUrl -OutFile $UvZip -UseBasicParsing
    $got = (Get-FileHash $UvZip -Algorithm SHA256).Hash.ToLower()
    if ($got -ne $UvZipSha256) { throw "uv $UvVersion zip checksum mismatch: expected $UvZipSha256, got $got" }
    Expand-Archive -Path $UvZip -DestinationPath (Join-Path $UvTmp "x") -Force
    Copy-Item (Join-Path $UvTmp "x\uv.exe") $UvExe -Force
    if (-not (Test-PinnedUv)) { throw "extracted uv.exe does not match the pinned SHA-256 $UvExeSha256" }
    Write-Host "OK  $UvExe (uv $UvVersion, verified)" -ForegroundColor Green
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
