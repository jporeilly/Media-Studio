<#
.SYNOPSIS
    Authenticode-sign one file, if a certificate is configured.

.DESCRIPTION
    Called by Tauri for every binary it bundles (bundle.windows.signCommand),
    with the file to sign as the only argument.

    NO-OPS WHEN NO CERTIFICATE IS CONFIGURED, and says so. That is deliberate:
    an unsigned build is the normal state for a developer, and a build that fails
    because a colleague has no certificate helps nobody. The install still works;
    Windows just shows SmartScreen.

    The certificate is never in this repo. It is read from the environment:

        MSE_SIGN_THUMBPRINT   SHA-1 thumbprint of a cert in the Windows store
        MSE_SIGN_TIMESTAMP    RFC-3161 timestamp URL (optional, has a default)

    A thumbprint identifies a certificate the machine already trusts; it is not a
    secret and carries no key material, so it is safe in CI variables. The
    private key stays in the certificate store or on the HSM/token backing it.

.EXAMPLE
    $env:MSE_SIGN_THUMBPRINT = "A1B2C3..."
    npm run tauri:build

.NOTES
    Windows PowerShell 5.1+. ASCII-only on purpose.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory, Position = 0)]
    [string]$Path
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$thumb = $env:MSE_SIGN_THUMBPRINT
if (-not $thumb) {
    Write-Host "  [--] not signing $(Split-Path -Leaf $Path) - MSE_SIGN_THUMBPRINT is not set"
    exit 0
}

$timestamp = $env:MSE_SIGN_TIMESTAMP
if (-not $timestamp) { $timestamp = "http://timestamp.digicert.com" }

# signtool ships with the Windows SDK and is not on PATH by default. Take the
# newest one rather than the first: an old SDK's signtool may not support the
# /fd and /td algorithms below.
$signtool = Get-Command signtool.exe -ErrorAction SilentlyContinue
if (-not $signtool) {
    $found = Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin" -Recurse `
        -Filter signtool.exe -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -match "x64" } |
        Sort-Object FullName -Descending | Select-Object -First 1
    if (-not $found) {
        throw "MSE_SIGN_THUMBPRINT is set but signtool.exe was not found - install the Windows SDK"
    }
    $signtool = $found.FullName
} else {
    $signtool = $signtool.Source
}

# /fd and /td are both SHA-256 on purpose: the file digest AND the timestamp
# digest. Leaving the timestamp at the default SHA-1 produces a signature that
# expires with the certificate instead of outliving it.
& $signtool sign /sha1 $thumb /fd SHA256 /tr $timestamp /td SHA256 /v $Path
if ($LASTEXITCODE -ne 0) {
    throw "signtool failed on $Path (exit $LASTEXITCODE)"
}
Write-Host "  [ok] signed $(Split-Path -Leaf $Path)"

exit 0
