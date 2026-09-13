<#
.SYNOPSIS
    Stage the Python app + built React UI for bundling.

.DESCRIPTION
    Copies the repo-root app tree (main.py, api\, core\, utils\, services\,
    __init__.py) and frontend\dist\ into src-tauri\vendor\app, which
    tauri.conf.json's bundle.resources maps to "app" inside the install.

    The staged tree MIRRORS the repo's FLAT layout:

        app\main.py
        app\boot.py
        app\api\app.py
        app\frontend\dist\index.html

    That is not cosmetic. api\app.py resolves the built UI as
    APP_DIR\frontend\dist where APP_DIR is api\app.py's parent's parent - i.e.
    the app root - so the SPA must sit at app\frontend\dist for FastAPI to serve
    it.

    Deliberately EXCLUDES local state and developer debris. Shipping a
    developer's media_studio.db, config.json or .env would leak accounts, lab
    settings and provider API keys into a customer install.

.NOTES
    Windows PowerShell 5.1+. ASCII-only on purpose.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
# Without this an undefined variable expands to empty and robocopy just returns
# exit 16 - which is how a staging destination can silently become "".
Set-StrictMode -Version Latest

$desktopDir = Split-Path -Parent $PSScriptRoot
$repoRoot   = Split-Path -Parent $desktopDir
$srcUi      = Join-Path $repoRoot "frontend\dist"
$stageDir   = Join-Path $desktopDir "src-tauri\vendor\app"
$stageUi    = Join-Path $stageDir "frontend\dist"

function Ok($m)   { Write-Host "  [ok] $m" -ForegroundColor Green }
function Warn($m) { Write-Host "  [!]  $m" -ForegroundColor Yellow }

Write-Host ""
Write-Host "  Staging the app" -ForegroundColor Cyan

if (-not (Test-Path -LiteralPath (Join-Path $repoRoot "main.py"))) {
    throw "main.py not found - is $repoRoot the repo root?"
}
if (-not (Test-Path -LiteralPath (Join-Path $srcUi "index.html"))) {
    throw "frontend\dist\index.html not found - run 'npm run build' in frontend\ first"
}

if (Test-Path -LiteralPath $stageDir) { Remove-Item -LiteralPath $stageDir -Recurse -Force }
New-Item -ItemType Directory -Path $stageDir -Force | Out-Null

# State files and secrets: never shipped. The installed app starts empty and
# creates its own data\ tree under the (per-user, writable) install directory.
$excludeFiles = @(".env", "config.json", ".storage_secret", "*.db", "*.db-wal", "*.db-shm", "*.log")

# Dev debris, runtime output and the shell itself. Names are RELATIVE (bare) so
# robocopy /XD matches at ANY depth: an absolute path matches only the top
# level, which lets every subpackage's __pycache__ ship from the dev checkout
# into the install - where the uninstaller then leaves them behind.
$excludeDirs = @(
    "desktop",            # the shell itself - never recurse into it
    "frontend",           # copied separately as frontend\dist only
    "venv", ".venv",
    "__pycache__", ".pytest_cache", ".ruff_cache",
    "tests",
    "data",               # runtime state: db, projects, cache, logs, temp
    "assets",             # runtime output: finished\, temp\
    "node_modules",
    ".git", ".github", ".claude",
    "dist", "build"
)

# robocopy: mirror a clean tree, /XD and /XF do the excluding. Exit codes 0-7
# are success (8+ is a real failure) - a quirk worth pinning, because treating
# any non-zero as failure makes every build look broken.
$roboArgs = @($repoRoot, $stageDir, "/E", "/NFL", "/NDL", "/NJH", "/NJS", "/NP")
foreach ($d in $excludeDirs)  { $roboArgs += @("/XD", $d) }
foreach ($f in $excludeFiles) { $roboArgs += @("/XF", $f) }
& robocopy @roboArgs | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy failed staging the app (exit $LASTEXITCODE)" }

# The built SPA, at app\frontend\dist - the shape api\app.py expects (see the
# .DESCRIPTION note above).
New-Item -ItemType Directory -Path $stageUi -Force | Out-Null
& robocopy $srcUi $stageUi "/E" "/NFL" "/NDL" "/NJH" "/NJS" "/NP" | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy failed staging the UI (exit $LASTEXITCODE)" }

# boot.py puts the app root on sys.path before importing it. The embeddable
# runtime's ._pth replaces sys.path outright, so without this the server cannot
# import api.app whatever working directory it is given. See desktop\boot.py.
Copy-Item -LiteralPath (Join-Path $desktopDir "boot.py") -Destination (Join-Path $stageDir "boot.py") -Force

# Belt and braces: prove nothing sensitive slipped through. A rename or a new
# state file would otherwise be caught only by a customer.
$leakedNames = @(".env", "config.json", ".storage_secret")
$leaked = Get-ChildItem -LiteralPath $stageDir -Recurse -File |
    Where-Object { ($leakedNames -contains $_.Name) -or ($_.Extension -eq ".db") }
if ($leaked) {
    $leaked | ForEach-Object { Warn ("leaked: " + $_.FullName) }
    throw "state or secret files reached the staging tree - fix the exclude list"
}

# Same guard for dev virtualenvs. The staged tree runs on the VENDORED runtime,
# so a bundled venv is a second, wrong Python.
$venvs = Get-ChildItem -LiteralPath $stageDir -Recurse -Directory |
    Where-Object { $_.Name -eq ".venv" -or $_.Name -eq "venv" }
if ($venvs) {
    $venvs | ForEach-Object { Warn ("venv: " + $_.FullName) }
    throw "a dev virtualenv reached the staging tree - fix the exclude list"
}

# The paths the shell and the server actually depend on. Assert them here, where
# the fix is obvious, rather than at first launch on an attendee's laptop.
foreach ($must in @((Join-Path $stageDir "main.py"),
                    (Join-Path $stageDir "boot.py"),
                    (Join-Path $stageDir "__init__.py"),
                    (Join-Path $stageDir "api\app.py"),
                    (Join-Path $stageUi  "index.html"))) {
    if (-not (Test-Path -LiteralPath $must)) { throw "staging incomplete: $must is missing" }
}

# Prove the staged tree can be imported, using the runtime that will ship with
# it. File-existence checks cannot catch a module excluded by mistake; this can.
$vendorPy = Join-Path $desktopDir "src-tauri\vendor\python\python.exe"
if (Test-Path -LiteralPath $vendorPy) {
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    # HARD gate: the api package + the root __init__ (version source) must import.
    # -B: never write bytecode into the tree robocopy just excluded it from.
    $probe = "import sys; sys.path.insert(0, sys.argv[1]); import api; print('api', api.__version__)"
    $out = & $vendorPy -B -c $probe $stageDir 2>&1
    $code = $LASTEXITCODE
    if ($code -ne 0) {
        $out | ForEach-Object { Warn $_ }
        $ErrorActionPreference = $prevEap
        throw "the staged tree cannot import 'api' - a module is missing from the stage"
    }
    Ok "staged tree imports 'api' cleanly ($out)"

    # SOFT check: the full app graph (routers -> core -> services). A failure
    # here is usually an optional media/COM dependency, not a broken stage, so it
    # warns rather than blocking the build.
    $probe2 = "import sys; sys.path.insert(0, sys.argv[1]); import api.app; print('api.app ok')"
    $out2 = & $vendorPy -B -c $probe2 $stageDir 2>&1
    if ($LASTEXITCODE -ne 0) {
        Warn "the full app graph (api.app) did not import on the vendored runtime:"
        $out2 | ForEach-Object { Warn $_ }
        Warn "the app may still boot and serve the UI; check the affected feature's dependency"
    } else {
        Ok "staged tree imports 'api.app' cleanly"
    }
    $ErrorActionPreference = $prevEap

    # Belt and braces: remove any __pycache__ a stray run left behind. A shipped
    # .pyc is invisible until someone lists the installer.
    Get-ChildItem -LiteralPath $stageDir -Recurse -Directory -Filter "__pycache__" |
        ForEach-Object { Remove-Item -LiteralPath $_.FullName -Recurse -Force }
} else {
    Warn "no vendored runtime yet - skipping the import check (run fetch:python first)"
}

$count = (Get-ChildItem -LiteralPath $stageDir -Recurse -File).Count
Ok "staged $count file(s) to src-tauri\vendor\app"
Write-Host ""

# robocopy returns 1 for "files were copied"; PowerShell surfaces the LAST
# native exit code as the script's, so a successful run would look like a failure
# to npm and abort the tauri build.
exit 0
