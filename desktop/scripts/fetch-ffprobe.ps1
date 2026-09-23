<#
.SYNOPSIS
    Vendor ffprobe.exe - the twin of the ffmpeg.exe the installer already ships.

.DESCRIPTION
    Downloads gyan.dev's VERSIONED "7.1 essentials" archive, extracts the single
    entry bin\ffprobe.exe, verifies it by SHA-256 and by its own -version line,
    and leaves it at src-tauri\vendor\ffprobe\ffprobe.exe for stage-app.ps1 to
    copy into app\bin beside ffmpeg.exe.

    Why ffprobe has to ship at all: the re-voice decodes every synthesised
    sentence, and every narrated render decodes every slide's MP3, through
    pydub.AudioSegment.from_file - and for anything that is not a .wav pydub
    runs its OWN prober (mediainfo_json -> get_prober_name in pydub\utils.py).
    That call is made by pydub, not by the app, so fixing the app's own ffprobe
    calls does not reach it. With no prober on the machine it raises
    FileNotFoundError [WinError 2] at once, which is what a clean install of
    0.9.0 did to both the re-voice and narrated rendering.

    Why THIS archive and not any other ffprobe:

      - The installer's ffmpeg comes from imageio-ffmpeg, which REPACKAGES
        gyan.dev's essentials build. The vendored binary identifies itself as
        "ffmpeg version 7.1-essentials_build-www.gyan.dev", built with gcc
        14.2.0 (MSYS2).
      - This archive's bin\ffmpeg.exe is BYTE-IDENTICAL to the one
        imageio-ffmpeg vendors - both 87,638,016 bytes, both SHA-256
        2ce797a0f88d7f067180338fb227f7b1928ea727bd9a4d7a1d022f7c52af71a3 - so
        the ffprobe beside it in the same zip is literally the binary that was
        compiled and released with the ffmpeg the app already ships, not merely
        "the same version".
      - A full build, a later release (the dev box's WinGet 8.0.1) or a nightly
        would be a DIFFERENT pair. The two binaries share libav* behaviour, and
        7.1 vs 8.0.1 already diverge where it matters here: an unbounded -af
        apad exits in 0.2 s on 8.0.1 and never finishes on 7.1.

    Idempotent: with the file already vendored at the pinned hash it does
    nothing. Loud on failure: a bad download, a changed archive or a mismatched
    binary throws with both hashes rather than shipping something unverified.

.PARAMETER Force
    Re-download and re-verify even when the vendored file already matches.

.NOTES
    Windows PowerShell 5.1+. ASCII-only on purpose (an em-dash breaks 5.1).
    The download is ~92 MB; the extracted binary is ~83 MB, which is what it
    adds to the installer.
#>
[CmdletBinding()]
param(
    [switch]$Force
)

$ErrorActionPreference = "Stop"
# Without this an undefined variable expands to empty and a path check silently
# tests "" instead of the file it was meant to test.
Set-StrictMode -Version Latest

$desktopDir = Split-Path -Parent $PSScriptRoot            # desktop\
$vendorDir  = Join-Path $desktopDir "src-tauri\vendor\ffprobe"
$target     = Join-Path $vendorDir "ffprobe.exe"

# --- the pin. Change these four together or not at all. --------------------
# gyan.dev publishes its versioned release archives from its own GitHub repo
# (GyanD/codexffmpeg); www.gyan.dev/ffmpeg/builds serves only the MOVING
# "release"/"git" links, which would silently hand us 8.x.
$ArchiveUrl    = "https://github.com/GyanD/codexffmpeg/releases/download/7.1/ffmpeg-7.1-essentials_build.zip"
$ArchiveSha256 = "fa7d4d7e795db0e2503f49f105f46ed5852386f0cfdd819899be3b65ebde24fc"
$EntryName     = "ffmpeg-7.1-essentials_build/bin/ffprobe.exe"
$FfprobeSha256 = "436bf02524d50135ed9965b90d1e0ad7f26c5c236132613a2edb87ef8b6873d0"
# The token both binaries must print on the first line of -version. The staging
# step compares ffmpeg's line to ffprobe's; this is the value they must share.
$VersionToken  = "7.1-essentials_build-www.gyan.dev"

function Say($m)  { Write-Host "  $m" }
function Ok($m)   { Write-Host "  [ok] $m" -ForegroundColor Green }
function Warn($m) { Write-Host "  [!]  $m" -ForegroundColor Yellow }

function Get-Sha256Lower($path) {
    return (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLower()
}

Write-Host ""
Write-Host "  Vendoring ffprobe $VersionToken" -ForegroundColor Cyan

if ((-not $Force) -and (Test-Path -LiteralPath $target)) {
    $have = Get-Sha256Lower $target
    if ($have -eq $FfprobeSha256) {
        Ok "already vendored (SHA-256 matches the pin)"
        exit 0
    }
    Warn "vendored ffprobe.exe does not match the pinned hash - refetching"
    Warn ("  expected " + $FfprobeSha256)
    Warn ("  found    " + $have)
}

New-Item -ItemType Directory -Path $vendorDir -Force | Out-Null

$zipPath = Join-Path $env:TEMP "ffmpeg-7.1-essentials_build.zip"
$tmpExe  = Join-Path $env:TEMP "ffprobe-7.1-staging.exe"

# The zip survives between runs only as a download cache; a partial or stale
# copy must never be trusted, so it is hash-checked before it is opened and
# deleted when it fails.
$needDownload = $true
if (Test-Path -LiteralPath $zipPath) {
    $zipHave = Get-Sha256Lower $zipPath
    if ($zipHave -eq $ArchiveSha256) {
        Ok "reusing the verified archive already in TEMP"
        $needDownload = $false
    } else {
        Warn "a stale ffmpeg archive was in TEMP - discarding it"
        Remove-Item -LiteralPath $zipPath -Force
    }
}

if ($needDownload) {
    Say "downloading $ArchiveUrl"
    # PS 5.1's progress bar makes Invoke-WebRequest an order of magnitude slower
    # on a download this size.
    $prevProgress = $ProgressPreference
    $ProgressPreference = "SilentlyContinue"
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    try {
        Invoke-WebRequest -Uri $ArchiveUrl -OutFile $zipPath -UseBasicParsing
    } catch {
        $ProgressPreference = $prevProgress
        throw ("downloading the ffmpeg 7.1 essentials archive failed: " + $_.Exception.Message + " (url: " + $ArchiveUrl + ")")
    }
    $ProgressPreference = $prevProgress
    Ok ("downloaded (" + [math]::Round((Get-Item -LiteralPath $zipPath).Length / 1MB, 1) + " MB)")

    $zipHave = Get-Sha256Lower $zipPath
    if ($zipHave -ne $ArchiveSha256) {
        Remove-Item -LiteralPath $zipPath -Force
        throw ("the downloaded archive is NOT the pinned one - refusing to ship it." +
               "`n    url      " + $ArchiveUrl +
               "`n    expected " + $ArchiveSha256 +
               "`n    got      " + $zipHave)
    }
    Ok "archive SHA-256 matches the pin"
}

# Extract the ONE entry rather than expanding the whole archive: the zip holds
# three ~87 MB binaries and Expand-Archive would write all of them.
Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip = [System.IO.Compression.ZipFile]::OpenRead($zipPath)
try {
    $entry = $zip.Entries | Where-Object { $_.FullName -eq $EntryName }
    if (-not $entry) {
        throw ("the archive has no entry '" + $EntryName + "' - the upstream layout changed; re-pin this script")
    }
    if (Test-Path -LiteralPath $tmpExe) { Remove-Item -LiteralPath $tmpExe -Force }
    [System.IO.Compression.ZipFileExtensions]::ExtractToFile($entry, $tmpExe, $true)
} finally {
    $zip.Dispose()
}

$exeHave = Get-Sha256Lower $tmpExe
if ($exeHave -ne $FfprobeSha256) {
    Remove-Item -LiteralPath $tmpExe -Force
    throw ("the extracted ffprobe.exe is NOT the pinned binary - refusing to ship it." +
           "`n    entry    " + $EntryName +
           "`n    expected " + $FfprobeSha256 +
           "`n    got      " + $exeHave)
}
Ok "ffprobe.exe SHA-256 matches the pin"

# Run it. A hash proves the bytes; this proves the bytes are the ffprobe we
# think they are AND that the machine can execute it (an antivirus that denies
# reads on ffmpeg binaries has cost this project a day before now).
$prevEap = $ErrorActionPreference
$ErrorActionPreference = "Continue"
$verOut = (& $tmpExe -version 2>&1 | Out-String)
$verOk  = ($LASTEXITCODE -eq 0)
$ErrorActionPreference = $prevEap
$firstLine = ($verOut -split "`n")[0].Trim()
# A rejected binary is deleted on the way out, like every other refusal here:
# nothing unverified is left lying in TEMP for a later hand to pick up.
if (-not $verOk) {
    Remove-Item -LiteralPath $tmpExe -Force -ErrorAction SilentlyContinue
    throw ("the extracted ffprobe.exe would not run: " + $firstLine)
}
if ($firstLine -notmatch [regex]::Escape($VersionToken)) {
    Remove-Item -LiteralPath $tmpExe -Force -ErrorAction SilentlyContinue
    throw ("the extracted ffprobe reports the wrong build." +
           "`n    expected to contain " + $VersionToken +
           "`n    reported            " + $firstLine)
}
Ok $firstLine

Move-Item -LiteralPath $tmpExe -Destination $target -Force
Remove-Item -LiteralPath $zipPath -Force
Ok ("vendored ffprobe -> src-tauri\vendor\ffprobe\ffprobe.exe (" +
    [math]::Round((Get-Item -LiteralPath $target).Length / 1MB) + " MB)")
Write-Host ""

# ffprobe leaves its own exit code behind; PowerShell surfaces the LAST native
# exit code as the script's, so a successful run could look like a failure to
# npm and abort the dist chain.
exit 0
