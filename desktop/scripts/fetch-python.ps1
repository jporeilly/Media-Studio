<#
.SYNOPSIS
    Vendor a self-contained Python runtime into the installer.

.DESCRIPTION
    Downloads the official Windows "embeddable package" into
    src-tauri\vendor\python and installs the app's requirements alongside it,
    so the shipped app depends on nothing already present on the machine.

    Why the embeddable zip rather than PyInstaller: the dependency set includes
    moviepy, faster-whisper, onnxruntime, kokoro-onnx, pywin32 and pymupdf, which
    is exactly the sort of dynamic-import-heavy code PyInstaller's static
    analysis gets wrong - and it gets it wrong at RUNTIME, on the attendee's
    machine. A vendored tree is just files: what was tested is what ships.

    Idempotent - skips the download when the pinned version AND requirements are
    already staged.

.PARAMETER Version
    Python version to vendor. Pinned to 3.12.x on purpose: every heavy wheel
    (onnxruntime, ctranslate2 via faster-whisper, numpy, pymupdf) has a cp312
    build, and 3.12 avoids the 3.13 audioop removal that pydub relies on. The app
    itself supports 3.11+.

.PARAMETER Force
    Re-download and rebuild even when the stamp matches.

.NOTES
    Windows PowerShell 5.1+. ASCII-only on purpose.
    This step is LARGE: the media stack (onnxruntime, torch-free ctranslate2,
    numpy, pillow) makes the vendored tree well over 1 GB and the download +
    install takes several minutes.
#>
[CmdletBinding()]
param(
    [string]$Version = "3.12.8",
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$desktopDir = Split-Path -Parent $PSScriptRoot            # desktop\
$repoRoot   = Split-Path -Parent $desktopDir              # media_studio_enterprise\
$vendorDir  = Join-Path $desktopDir "src-tauri\vendor\python"
$stampFile  = Join-Path $vendorDir ".version"
$reqFile    = Join-Path $repoRoot "requirements.txt"

function Say($m)  { Write-Host "  $m" }
function Ok($m)   { Write-Host "  [ok] $m" -ForegroundColor Green }
function Warn($m) { Write-Host "  [!]  $m" -ForegroundColor Yellow }

Write-Host ""
Write-Host "  Vendoring Python $Version" -ForegroundColor Cyan

if (-not (Test-Path -LiteralPath $reqFile)) {
    throw "requirements.txt not found at $reqFile"
}

# The stamp covers the runtime AND the requirements: a dependency added without
# a Python bump must still trigger a rebuild, or the installer silently ships
# the previous dependency set.
$reqHash = (Get-FileHash -LiteralPath $reqFile -Algorithm SHA256).Hash
$stamp = "$Version+$reqHash"

if ((-not $Force) -and (Test-Path -LiteralPath $stampFile)) {
    $existing = (Get-Content -LiteralPath $stampFile -Raw).Trim()
    if ($existing -eq $stamp) {
        Ok "already staged (Python $Version, requirements unchanged)"
        exit 0
    }
    Say "stamp differs - rebuilding"
}

if (Test-Path -LiteralPath $vendorDir) {
    Remove-Item -LiteralPath $vendorDir -Recurse -Force
}
New-Item -ItemType Directory -Path $vendorDir -Force | Out-Null

$zipName = "python-$Version-embed-amd64.zip"
$url = "https://www.python.org/ftp/python/$Version/$zipName"
$zipPath = Join-Path $env:TEMP $zipName

Say "downloading $url"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
Invoke-WebRequest -Uri $url -OutFile $zipPath -UseBasicParsing
Ok "downloaded ($([math]::Round((Get-Item $zipPath).Length / 1MB, 1)) MB)"

Expand-Archive -LiteralPath $zipPath -DestinationPath $vendorDir -Force
Remove-Item -LiteralPath $zipPath -Force
Ok "extracted to src-tauri\vendor\python"

# The embeddable distribution ships isolated: python*._pth lists the search
# path and comments out "import site", which disables site-packages entirely.
# Without this edit pip installs succeed and then nothing can be imported.
$pth = Get-ChildItem -LiteralPath $vendorDir -Filter "python*._pth" | Select-Object -First 1
if (-not $pth) { throw "no python*._pth in the embeddable package - layout changed?" }
$lines = Get-Content -LiteralPath $pth.FullName
$patched = $lines | ForEach-Object {
    if ($_ -match '^\s*#\s*import\s+site\s*$') { "import site" } else { $_ }
}
if ($patched -notcontains "Lib\site-packages") { $patched += "Lib\site-packages" }
Set-Content -LiteralPath $pth.FullName -Value $patched -Encoding ASCII
Ok "enabled site-packages in $($pth.Name)"

$py = Join-Path $vendorDir "python.exe"

# get-pip, because the embeddable package deliberately ships without it.
$getPip = Join-Path $env:TEMP "get-pip.py"
Say "installing pip"
Invoke-WebRequest -Uri "https://bootstrap.pypa.io/get-pip.py" -OutFile $getPip -UseBasicParsing
& $py $getPip --no-warn-script-location | Out-Null
if ($LASTEXITCODE -ne 0) { throw "get-pip failed" }
Remove-Item -LiteralPath $getPip -Force
Ok "pip installed"

Say "installing requirements (this takes several minutes - the media stack is large)"
& $py -m pip install --no-warn-script-location --disable-pip-version-check -r $reqFile
if ($LASTEXITCODE -ne 0) { throw "pip install -r requirements.txt failed" }

# uvicorn/fastapi are what the shell launches; prove they resolve now rather than
# discovering it on an attendee's machine. This is the HARD gate.
& $py -c "import uvicorn, fastapi; print('uvicorn', uvicorn.__version__)"
if ($LASTEXITCODE -ne 0) { throw "the vendored runtime cannot import uvicorn/fastapi" }

# The media stack is a SOFT check: a wheel problem here should be visible to the
# operator, but it must not stop the runtime shipping (the app still boots and
# serves the UI; only the affected feature is degraded).
& $py -c "import numpy, PIL, moviepy, pydub, faster_whisper, onnxruntime; print('media stack ok')"
if ($LASTEXITCODE -ne 0) { Warn "the media stack did not fully import - video/transcription features may be degraded" }

# pywin32 (PowerPoint COM export) often needs its DLLs discoverable. Report, do
# not fail: COM export is one feature, not the whole app. The stderr redirect
# must run with ErrorActionPreference "Continue": under "Stop", PS 5.1 turns the
# first stderr line of a native command into a terminating error, which would
# abort this >1 GB step just before its stamp is written.
$prevEapWin = $ErrorActionPreference
$ErrorActionPreference = "Continue"
& $py -c "import win32com.client; print('pywin32 ok')" 2>&1 | Out-Null
$winOk = ($LASTEXITCODE -eq 0)
$ErrorActionPreference = $prevEapWin
if (-not $winOk) { Warn "pywin32/win32com did not import cleanly - PowerPoint COM export may need pywin32_postinstall" }

# ffmpeg is a RUNTIME dependency of moviepy/pydub. requirements.txt ships no
# standalone ffmpeg wheel, but moviepy depends on imageio-ffmpeg, which vendors
# the binary into site-packages; stage-app.ps1 then ships it as app\bin\ffmpeg.exe
# (boot.py puts app\bin on PATH). Confirm it is here now, so a missing binary is
# discovered at build time and not at first render on a customer machine.
# ffprobe has its OWN step - fetch-ffprobe.ps1 - because no wheel vendors one
# and pydub needs a prober; see that script for why the version must match.
$ffPy = Join-Path (Split-Path -Parent $PSScriptRoot) "src-tauri\vendor\python\python.exe"
$prevEapFf = $ErrorActionPreference
$ErrorActionPreference = "Continue"
$ffExe = (& $ffPy -B -c "import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())" 2>&1 | Out-String).Trim()
$ffOk  = ($LASTEXITCODE -eq 0 -and $ffExe -and (Test-Path -LiteralPath $ffExe))
$ErrorActionPreference = $prevEapFf
if ($ffOk) {
    Ok "imageio-ffmpeg present - stage-app.ps1 ships it as app\bin\ffmpeg.exe (ffprobe comes from fetch-ffprobe.ps1)"
} else {
    Warn "imageio-ffmpeg not found in the runtime - video rendering will need ffmpeg on PATH"
}

Set-Content -LiteralPath $stampFile -Value $stamp -Encoding ASCII
$size = [math]::Round(((Get-ChildItem -LiteralPath $vendorDir -Recurse -File |
        Measure-Object -Property Length -Sum).Sum / 1MB), 0)
Ok "vendored runtime ready - $size MB"
Write-Host ""

# robocopy returns 1 for "files were copied" and pip leaves its own code behind.
# PowerShell surfaces the LAST native exit code as the script's, so a successful
# run would look like a failure to npm and abort the tauri build.
exit 0
