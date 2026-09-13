<#
    Shared helpers for the desktop scripts.

    Exists so check-environment.ps1 (and anything added later) answers the same
    two questions - "which Python do I run" and "where does the app root / data
    live" - through ONE set of rules, in both the layouts these scripts live in:

        installed:  $INSTDIR\provisioning\  ->  $INSTDIR\python , $INSTDIR\app
        checkout:   desktop\scripts\        ->  desktop\src-tauri\vendor\python ,
                                                the repo root

    Dot-source it:  . (Join-Path $PSScriptRoot "lib\common.ps1")

    ASCII-only on purpose (PowerShell 5.1).
#>

function Get-RepoRoot {
    <# The media_studio_enterprise checkout root, from a script in desktop\scripts. #>
    param([Parameter(Mandatory)][string] $ScriptRoot)
    return (Split-Path -Parent (Split-Path -Parent $ScriptRoot))
}

function Get-DesktopDir {
    param([Parameter(Mandatory)][string] $ScriptRoot)
    return (Split-Path -Parent $ScriptRoot)
}

function Resolve-PyExe {
    <#
        The interpreter to run. Candidates cover BOTH layouts, because the
        installer bundles copies of these scripts under provisioning\:

          installed:  $INSTDIR\provisioning\  ->  $INSTDIR\python\python.exe
          checkout:   desktop\scripts\        ->  desktop\src-tauri\vendor\python\

        Falls back to Python 3.11+ on PATH. Returns $null when there is none.
    #>
    param([Parameter(Mandatory)][string] $ScriptRoot)

    $here = Split-Path -Parent $ScriptRoot     # provisioning\.. or scripts\..
    foreach ($rel in @("python\python.exe", "src-tauri\vendor\python\python.exe")) {
        $c = Join-Path $here $rel
        if (Test-Path -LiteralPath $c) { return $c }
    }
    foreach ($cand in @("python", "py")) {
        try {
            & $cand -c "import sys; sys.exit(0 if sys.version_info[:2] >= (3, 11) else 1)" 2>$null
            if ($LASTEXITCODE -eq 0) { return $cand }
        } catch {}
    }
    return $null
}

function Resolve-AppRoot {
    <#
        The app root: the directory holding main.py (and api\, core\, ...).

        Probed on main.py, the one file whose position is fixed (boot.py and the
        shell both key off it).

        CHECKOUT preferred over the staged copy among the dev candidates:
        vendor\app is a build artifact that goes stale the moment the source
        changes, and on a dev machine both exist. In an installed layout only
        $INSTDIR\app is present, so it wins there by being the only one.
    #>
    param([Parameter(Mandatory)][string] $ScriptRoot)

    $here = Split-Path -Parent $ScriptRoot
    $candidates = @(
        (Join-Path $here "app"),                            # installed
        (Get-RepoRoot $ScriptRoot),                         # checkout
        (Join-Path $here "src-tauri\vendor\app")            # staged
    )
    foreach ($c in $candidates) {
        if (Test-Path -LiteralPath (Join-Path $c "main.py")) { return $c }
    }
    return $null
}

function Resolve-DataDir {
    <#
        Where the app writes its data. Media Studio has NO state-dir env override
        (utils\config.py and api\store.py resolve it as <app root>\data), so the
        data directory is simply the app root's data\ subfolder - which is why
        the installer is per-user/writable. Returns @{ Path; Why }.
    #>
    param([Parameter(Mandatory)][string] $ScriptRoot)
    $appRoot = Resolve-AppRoot $ScriptRoot
    if (-not $appRoot) {
        return @{ Path = $null; Why = "app root not found" }
    }
    return @{ Path = (Join-Path $appRoot "data"); Why = "<app root>\data" }
}
