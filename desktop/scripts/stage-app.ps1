<#
.SYNOPSIS
    Stage the Python app + built React UI for bundling.

.DESCRIPTION
    Stages the app into src-tauri\vendor\app (which tauri.conf.json's
    bundle.resources maps to "app" inside the install) as a GIT CHECKOUT of the
    committed tree - so the installed app can self-update with `git pull` - then
    overlays boot.py and a vendored ffmpeg.exe. The built UI (frontend\dist) is
    COMMITTED and arrives with the clone, so a pull updates the UI too.

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
param(
    # Build even when the tree is dirty or HEAD is not on a pushed branch. The
    # installer then ships something self-update cannot fast-forward from.
    [switch]$Force
)

$ErrorActionPreference = "Stop"
# Without this an undefined variable expands to empty and robocopy just returns
# exit 16 - which is how a staging destination can silently become "".
Set-StrictMode -Version Latest

$desktopDir = Split-Path -Parent $PSScriptRoot
$repoRoot   = Split-Path -Parent $desktopDir
$stageDir   = Join-Path $desktopDir "src-tauri\vendor\app"
$stageUi    = Join-Path $stageDir "frontend\dist"

function Ok($m)   { Write-Host "  [ok] $m" -ForegroundColor Green }
function Warn($m) { Write-Host "  [!]  $m" -ForegroundColor Yellow }

Write-Host ""
Write-Host "  Staging the app" -ForegroundColor Cyan

if (-not (Test-Path -LiteralPath (Join-Path $repoRoot "main.py"))) {
    throw "main.py not found - is $repoRoot the repo root?"
}

# The install can only fast-forward from what it ships when the staged commit is
# already on its pushed upstream branch. A build from an unpushed commit or a
# side branch ships something the remote does not have; a dirty tree ships HEAD
# while the developer believes the working copy went out. Checked BEFORE the
# previous stage is removed, so a refused build has no side effects.
$prevEapGate = $ErrorActionPreference
$ErrorActionPreference = "Continue"
$branch  = (& git -C $repoRoot rev-parse --abbrev-ref HEAD 2>&1 | Out-String).Trim()
# Content changes only. `git status --porcelain` also reports a file whose line
# endings were rewritten with no change to its content - the Tauri CLI does
# exactly that to Cargo.toml while building, and its own beforeBuildCommand then
# re-runs this script, so a status check refuses the build the CLI just started.
# `git diff` applies the repo's autocrlf normalisation and sees through it.
& git -C $repoRoot diff --quiet 2>&1 | Out-Null
$dirtyUnstaged = ($LASTEXITCODE -ne 0)
& git -C $repoRoot diff --cached --quiet 2>&1 | Out-Null
$dirtyStaged = ($LASTEXITCODE -ne 0)
$untracked = (& git -C $repoRoot ls-files --others --exclude-standard 2>&1 | Out-String).Trim()
$dirty = ""
if ($dirtyUnstaged -or $dirtyStaged) {
    $dirty = (& git -C $repoRoot status --porcelain --untracked-files=no 2>&1 | Out-String).Trim()
}
if ($untracked) { $dirty = ($dirty, $untracked -ne "" -join "`n").Trim() }
$ahead   = (& git -C $repoRoot rev-list --count "@{u}..HEAD" 2>&1 | Out-String).Trim()
$aheadOk = ($LASTEXITCODE -eq 0)
$ErrorActionPreference = $prevEapGate
if ($dirty) {
    $dirty -split "`n" | ForEach-Object { Warn ("uncommitted: " + $_.Trim()) }
    if (-not $Force) { throw "the working tree is dirty - the clone ships HEAD, not these changes; commit or stash them (or -Force to build anyway)" }
    Warn "-Force: building from a dirty tree (the installer ships HEAD only)"
}
if (-not $aheadOk) {
    if (-not $Force) { throw "branch '$branch' has no upstream - push it first (self-update pulls from origin/$branch)" }
    Warn "-Force: branch '$branch' has no upstream - the install will not be able to self-update"
} elseif ([int]$ahead -gt 0) {
    if (-not $Force) { throw "HEAD is $ahead commit(s) ahead of its upstream - push first, or the install can never fast-forward" }
    Warn "-Force: HEAD is $ahead commit(s) ahead of its upstream"
} else {
    Ok "HEAD is on '$branch', pushed, tree clean"
}

if (Test-Path -LiteralPath $stageDir) { Remove-Item -LiteralPath $stageDir -Recurse -Force }
New-Item -ItemType Directory -Path (Split-Path -Parent $stageDir) -Force | Out-Null

# The staged app is a GIT CHECKOUT, not a file copy, so the installed app can
# self-update in place with `git pull` (Settings > Updates). Cloning the local
# repo ships exactly the committed tree - uncommitted work and every ignored
# file (venv, data\, .env, *.db, node_modules, dist\) never reach an installer -
# and leaves the working tree pristine, which a fast-forward pull requires: a
# partial file copy would read as local deletions and block every update.
$prevEap = $ErrorActionPreference
$ErrorActionPreference = "Continue"
$originUrl = (& git -C $repoRoot remote get-url origin 2>&1 | Out-String).Trim()
$originOk  = ($LASTEXITCODE -eq 0)
$ErrorActionPreference = $prevEap
if (-not $originOk -or -not $originUrl -or $originUrl -notmatch '^(https?://|git@)') {
    throw "the repo has no 'origin' remote - push it to GitHub first (self-update pulls from origin)"
}

$ErrorActionPreference = "Continue"
& git clone --quiet $repoRoot $stageDir 2>&1 | Out-Null
$cloneOk = ($LASTEXITCODE -eq 0)
if ($cloneOk) {
    # Point the install at the real remote, not the developer's local path.
    & git -C $stageDir remote set-url origin $originUrl 2>&1 | Out-Null
    $cloneOk = ($LASTEXITCODE -eq 0)
}
$stagedRev = (& git -C $stageDir rev-parse --short HEAD 2>&1 | Out-String).Trim()
$ErrorActionPreference = $prevEap
if (-not $cloneOk) { throw "git clone of the repo into the staging tree failed" }
Ok "staged a git checkout of $stagedRev tracking $originUrl"

# The built SPA is committed, so the clone already has it at app\frontend\dist -
# the shape api\app.py expects (see the .DESCRIPTION note). Its absence means a
# source-only commit: tests/test_frontend_dist.py would have failed too.
if (-not (Test-Path -LiteralPath (Join-Path $stageUi "index.html"))) {
    throw "the committed tree has no frontend\dist\index.html - run 'npm run build' in frontend\ and COMMIT frontend\dist (it ships with the checkout so git pull updates the UI)"
}

# boot.py puts the app root on sys.path before importing it. The embeddable
# runtime's ._pth replaces sys.path outright, so without this the server cannot
# import api.app whatever working directory it is given. See desktop\boot.py.
Copy-Item -LiteralPath (Join-Path $desktopDir "boot.py") -Destination (Join-Path $stageDir "boot.py") -Force

# ffmpeg: the engine invokes it BY NAME and a customer machine has none on PATH.
# moviepy's imageio-ffmpeg dependency vendors the binary (under a versioned
# name) into the runtime's site-packages; ship it as app\bin\ffmpeg.exe and
# boot.py puts app\bin on PATH. ffprobe is not bundled - the engine degrades
# gracefully where it is optional (e.g. the re-voice pad falls back to apad).
$vendorPy = Join-Path $desktopDir "src-tauri\vendor\python\python.exe"
if (Test-Path -LiteralPath $vendorPy) {
    $ErrorActionPreference = "Continue"
    $ffSrc = (& $vendorPy -B -c "import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())" 2>&1 | Out-String).Trim()
    $ffOk  = ($LASTEXITCODE -eq 0)
    $ErrorActionPreference = $prevEap
    if ($ffOk -and $ffSrc -and (Test-Path -LiteralPath $ffSrc)) {
        $binDir = Join-Path $stageDir "bin"
        New-Item -ItemType Directory -Path $binDir -Force | Out-Null
        Copy-Item -LiteralPath $ffSrc -Destination (Join-Path $binDir "ffmpeg.exe") -Force
        Ok ("vendored ffmpeg -> app\bin\ffmpeg.exe (" + [math]::Round((Get-Item -LiteralPath $ffSrc).Length / 1MB) + " MB)")
    } else {
        Warn "imageio-ffmpeg not found in the vendored runtime - the install will need ffmpeg on PATH"
    }
}

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

# git and python leave their own exit codes behind; PowerShell surfaces the LAST
# native exit code as the script's, so a successful run could look like a failure
# to npm and abort the tauri build.
exit 0
