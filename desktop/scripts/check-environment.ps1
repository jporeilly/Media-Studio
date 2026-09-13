<#
.SYNOPSIS
    Check the machine can run Media Studio Enterprise, and say what to do when
    it cannot.

.DESCRIPTION
    Written to be run AFTER install, by whoever is setting up a workshop machine,
    and to be readable by someone who did not build the app.

    It reports rather than blocks. Only two things are genuine FAILures - the
    WebView2 runtime and a usable Python (with uvicorn/fastapi) - because without
    them the window does not open at all. Everything else is a WARN with a fix
    attached:

      - ffmpeg absent only affects video RENDERING; import, transcription and the
        UI all work without it.
      - Ollama absent only affects the optional local-AI features.

    Treating those as hard failures would teach people to ignore the output.

.PARAMETER Json
    Emit the results as JSON only - for piping into a provisioning log.

.EXAMPLE
    .\check-environment.ps1

.NOTES
    Windows PowerShell 5.1+. ASCII-only on purpose.
#>
[CmdletBinding()]
param(
    [switch]$Json
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$script:Checks   = @()
$script:Failures = 0
$script:Warnings = 0
$script:Fixes    = @()

function Say {
    param([string]$Text = "", [string]$Colour = "Gray")
    if (-not $Json) { Write-Host $Text -ForegroundColor $Colour }
}

function Report {
    param(
        [string]$Name,
        [ValidateSet("OK", "FAIL", "WARN", "SKIP")][string]$State,
        [string]$Detail = "",
        [string]$Fix = ""
    )
    $script:Checks += [ordered]@{ name = $Name; state = $State; detail = $Detail; fix = $Fix }
    if (-not $Json) {
        $colour = @{ OK = "Green"; FAIL = "Red"; WARN = "Yellow"; SKIP = "DarkGray" }[$State]
        Write-Host ("  [{0,-4}] " -f $State) -ForegroundColor $colour -NoNewline
        Write-Host ("{0,-28}" -f $Name) -NoNewline
        Write-Host $Detail -ForegroundColor DarkGray
    }
    if ($State -eq "FAIL") {
        $script:Failures++
        if ($Fix) { $script:Fixes += "  # $Name`n  $Fix" }
    } elseif ($State -eq "WARN") {
        $script:Warnings++
        if ($Fix) { $script:Fixes += "  # $Name (optional)`n  $Fix" }
    }
}

# 127.0.0.1 rather than "localhost": localhost can resolve to ::1 first, and the
# probe then reports a healthy service as down.
function Test-Port([string]$TargetHost, [int]$Port, [int]$TimeoutMs = 1500) {
    try {
        $c = New-Object System.Net.Sockets.TcpClient
        $iar = $c.BeginConnect($TargetHost, $Port, $null, $null)
        $ok = $iar.AsyncWaitHandle.WaitOne($TimeoutMs, $false)
        if ($ok) { $c.EndConnect($iar) }
        $c.Close()
        return $ok
    } catch { return $false }
}

. (Join-Path $PSScriptRoot "lib\common.ps1")

Say ""
Say "  Media Studio Enterprise - environment check" "Cyan"
Say "  Reports what is missing and how to fix it. Only WebView2 and Python are" "DarkGray"
Say "  hard requirements; everything else is optional and says so." "DarkGray"
Say ""

# -- the two things that stop the window opening ----------------------------
Say "  Required" "Cyan"

# WebView2. The installer bundles the bootstrapper, so this should already be
# satisfied on a machine that ran it; the check matters for a machine being
# prepared for the app, or one where the runtime was removed.
$wv2Keys = @(
    "HKLM:\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",
    "HKLM:\SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",
    "HKCU:\SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
)
$wv2 = $null
foreach ($k in $wv2Keys) {
    if (Test-Path $k) {
        try {
            $v = (Get-ItemProperty -Path $k -ErrorAction Stop).pv
            if ($v) { $wv2 = $v; break }
        } catch {}
    }
}
if ($wv2) {
    Report "WebView2 runtime" "OK" $wv2
} else {
    Report "WebView2 runtime" "FAIL" "not found - the app window cannot render" `
        "winget install -e --id Microsoft.EdgeWebView2Runtime"
}

# Python. Resolve-PyExe knows both layouts: $INSTDIR\python for an install,
# desktop\src-tauri\vendor\python for a checkout, then PATH.
$script:PyExe = Resolve-PyExe $PSScriptRoot
$bundled = $script:PyExe -and (Test-Path -LiteralPath $script:PyExe) -and
           ($script:PyExe -like "*\python\python.exe")

if (-not $script:PyExe) {
    Report "Python 3.11+" "FAIL" "no interpreter found, bundled or on PATH" `
        "reinstall the app, or install Python: winget install -e --id Python.Python.3.12"
} else {
    $ver = & $script:PyExe -c "import sys;print('.'.join(map(str,sys.version_info[:3])))" 2>$null
    if ($bundled) {
        Report "Python (bundled)" "OK" ("" + $ver + " - shipped with the app, nothing to install")
    } else {
        Report "Python 3.11+" "OK" ("" + $ver + " - from PATH (running from a checkout)")
    }

    # The imports the shell actually needs to serve the UI. This is the failure
    # this whole check exists for; confirming python.exe merely EXISTS misses it.
    $probe = & $script:PyExe -c "import uvicorn,fastapi;print('ok')" 2>&1
    if ($LASTEXITCODE -eq 0) {
        Report "Python core deps" "OK" "uvicorn, fastapi"
    } elseif ($bundled) {
        Report "Python core deps" "FAIL" "the bundled runtime cannot import uvicorn/fastapi" `
            "the install is incomplete - reinstall"
    } else {
        Report "Python core deps" "WARN" "uvicorn/fastapi not importable from this interpreter" `
            "activate the venv, or pip install -r requirements.txt"
    }

    # The media stack: heavier, optional at the "does the app open" level.
    $media = & $script:PyExe -c "import numpy,PIL,moviepy,pydub,faster_whisper,onnxruntime;print('ok')" 2>&1
    if ($LASTEXITCODE -eq 0) {
        Report "Media stack" "OK" "numpy, moviepy, pydub, faster-whisper, onnxruntime"
    } else {
        Report "Media stack" "WARN" "not fully importable - transcription/rendering may be degraded" `
            "reinstall the app, or pip install -r requirements.txt"
    }
}

# ffmpeg: a RUNTIME dependency of moviepy/pydub, not a pip wheel here.
$ffmpeg = Get-Command ffmpeg.exe -ErrorAction SilentlyContinue
if ($ffmpeg) {
    Report "ffmpeg" "OK" ("on PATH: " + $ffmpeg.Source)
} else {
    Report "ffmpeg" "WARN" "not on PATH - video rendering will fail" `
        "winget install -e --id Gyan.FFmpeg   (or add imageio-ffmpeg to requirements)"
}

# -- data directory ----------------------------------------------------------
Say ""
Say "  Data" "Cyan"

$data    = Resolve-DataDir $PSScriptRoot
$dataDir = $data.Path
$dataWhy = $data.Why

if (-not $dataDir) {
    Report "Data directory" "WARN" "app root not found - run this from the install or a checkout"
} else {
    $writable = $false
    try {
        if (-not (Test-Path -LiteralPath $dataDir)) {
            New-Item -ItemType Directory -Path $dataDir -Force | Out-Null
        }
        # Probe by writing. os.access / ACL inspection both lie about read-only.
        $probeFile = Join-Path $dataDir (".writeprobe-" + [Guid]::NewGuid().ToString("N"))
        Set-Content -LiteralPath $probeFile -Value "x" -Encoding ASCII
        Remove-Item -LiteralPath $probeFile -Force
        $writable = $true
    } catch {}

    if ($writable) {
        Report "Data directory" "OK" "$dataDir ($dataWhy)"
    } else {
        Report "Data directory" "FAIL" "$dataDir is not writable" `
            "the app writes into its own tree - install per-user (writable), not to Program Files"
    }

    $freeGb = $null
    try {
        $drive = (Get-Item -LiteralPath $dataDir).PSDrive
        if ($drive -and $drive.Free) { $freeGb = [math]::Round($drive.Free / 1GB, 1) }
    } catch {}
    if ($null -eq $freeGb) {
        Report "Disk space" "SKIP" "could not determine free space"
    } elseif ($freeGb -lt 3) {
        Report "Disk space" "WARN" "$freeGb GB free - rendering and models need room" "free up space on the data drive"
    } else {
        Report "Disk space" "OK" "$freeGb GB free"
    }
}

# -- optional local AI -------------------------------------------------------
Say ""
Say "  Local AI (optional - the app runs without it)" "Cyan"
if (Test-Port "127.0.0.1" 11434) {
    Report "Ollama" "OK" "running on 11434"
} else {
    Report "Ollama" "WARN" "not running on 11434 - local AI features will be unavailable" `
        "winget install -e --id Ollama.Ollama   (then pull a model, e.g. 'ollama pull gemma3:12b')"
}

# -- summary -----------------------------------------------------------------
if ($Json) {
    [ordered]@{
        failures = $script:Failures
        warnings = $script:Warnings
        checks   = $script:Checks
    } | ConvertTo-Json -Depth 5
    exit ([int]($script:Failures -gt 0))
}

Say ""
if ($script:Failures -eq 0 -and $script:Warnings -eq 0) {
    Write-Host "  Everything checks out." -ForegroundColor Green
} elseif ($script:Failures -eq 0) {
    Write-Host ("  Ready to run. " + $script:Warnings + " optional item(s) not configured.") -ForegroundColor Yellow
} else {
    Write-Host ("  " + $script:Failures + " blocking problem(s), " +
                $script:Warnings + " optional.") -ForegroundColor Red
}
if ($script:Fixes.Count -gt 0) {
    Say ""
    Say "  Suggested commands:" "Cyan"
    $script:Fixes | ForEach-Object { Say $_ "DarkGray" }
}
Say ""

exit ([int]($script:Failures -gt 0))
