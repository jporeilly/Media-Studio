<#
    Copies the freshly built NSIS installer from Tauri's deeply nested output
    (desktop\src-tauri\target\release\bundle\nsis\) to the repo root's dist\
    folder - ONE short, memorable path for every build artifact. Run after
    tauri:build; wired into the "dist" npm script.

    ASCII-only on purpose (PowerShell 5.1).
#>
$ErrorActionPreference = "Stop"

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$nsis = Join-Path $here "..\src-tauri\target\release\bundle\nsis"
$dist = Join-Path $here "..\..\dist"

$exe = Get-ChildItem -Path $nsis -Filter "*-setup.exe" -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $exe) {
    Write-Error "no *-setup.exe found in $nsis - the tauri build did not produce one"
    exit 1
}

# The version the installer carries MUST be the version this checkout says. A
# failed tauri build leaves the PREVIOUS installer sitting in the bundle folder,
# and collecting it silently ships a stale artifact under a new version's name.
# Media Studio's single source of truth is __init__.py's __version__.
$initFile = Join-Path $here "..\..\__init__.py"
$want = $null
if (Test-Path -LiteralPath $initFile) {
    $m = Select-String -LiteralPath $initFile -Pattern '__version__\s*=\s*["'']([^"'']+)["'']' |
        Select-Object -First 1
    if ($m) { $want = $m.Matches[0].Groups[1].Value }
}
if (-not $want) { Write-Error "could not read __version__ from $initFile"; exit 1 }

if ($exe.Name -notmatch [regex]::Escape($want)) {
    # -f binds tighter than +, so the format must wrap the whole concatenation.
    Write-Error (("installer '{0}' is not version {1} - the build FAILED and left an " +
                  "older artifact behind; fix the build rather than shipping this") -f $exe.Name, $want)
    exit 1
}
$age = (Get-Date) - $exe.LastWriteTime
if ($age.TotalMinutes -gt 30) {
    Write-Error ("installer '{0}' was built {1:N0} minutes ago - that is not this run" -f $exe.Name, $age.TotalMinutes)
    exit 1
}

New-Item -ItemType Directory -Force -Path $dist | Out-Null
Copy-Item -Path $exe.FullName -Destination $dist -Force
$final = Join-Path (Resolve-Path $dist).Path $exe.Name
$hash = (Get-FileHash -Path $final -Algorithm SHA256).Hash
Write-Output "installer -> $final"
Write-Output "sha256    -> $hash"
