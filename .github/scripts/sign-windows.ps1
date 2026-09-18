param([Parameter(Mandatory=$true)][string]$Path)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if (-not $env:DDOKDDAK_SIGNTOOL -or -not (Test-Path -LiteralPath $env:DDOKDDAK_SIGNTOOL -PathType Leaf)) {
    throw 'DDOKDDAK_SIGNTOOL must identify the Windows SDK SignTool. Unsigned fallback is prohibited.'
}
$signingArgs = @('sign', '/fd', 'SHA256', '/tr', 'http://timestamp.acs.microsoft.com', '/td', 'SHA256')
if ($env:DDOKDDAK_CERT_THUMBPRINT) {
    if ($env:DDOKDDAK_CERT_THUMBPRINT -notmatch '^[A-Fa-f0-9]{40}$') { throw 'Invalid certificate thumbprint.' }
    $signingArgs += @('/sha1', $env:DDOKDDAK_CERT_THUMBPRINT, '/s', 'My')
} elseif ($env:DDOKDDAK_SIGNING_DLIB -and $env:DDOKDDAK_SIGNING_METADATA) {
    foreach ($required in @($env:DDOKDDAK_SIGNING_DLIB, $env:DDOKDDAK_SIGNING_METADATA)) {
        if (-not (Test-Path -LiteralPath $required -PathType Leaf)) { throw "Signing configuration missing: $required" }
    }
    $signingArgs += @('/dlib', $env:DDOKDDAK_SIGNING_DLIB, '/dmdf', $env:DDOKDDAK_SIGNING_METADATA)
} else {
    throw 'A trusted certificate or Artifact Signing configuration is required. Unsigned fallback is prohibited.'
}
$target = Get-Item -LiteralPath $Path
$files = @(if ($target.PSIsContainer) {
    Get-ChildItem -LiteralPath $target.FullName -File -Recurse | Where-Object {
        $_.Extension.ToLowerInvariant() -in @('.exe', '.dll', '.pyd')
    }
} else { $target }) # Inno also calls this with a temporary self-copy.
if ($files.Count -eq 0) { throw "No executable files found: $Path" }
foreach ($file in $files) {
    $signature = Get-AuthenticodeSignature -LiteralPath $file.FullName
    if ($signature.Status -eq 'Valid' -and $signature.SignatureType -eq 'Authenticode') {
        # Retain upstream Python/Qt signatures; never replace valid vendor identities.
        & "$PSScriptRoot/verify-windows-signatures.ps1" -Path $file.FullName
        continue
    }
    if ($signature.Status -ne 'NotSigned' -and -not ($signature.Status -eq 'Valid' -and $signature.SignatureType -eq 'Catalog')) {
        throw "Refusing to cover an invalid signature ($($signature.Status)): $($file.FullName)"
    }
    # A build PC's catalog is not shipped with this package; add a portable signature.
    & $env:DDOKDDAK_SIGNTOOL @signingArgs $file.FullName
    if ($LASTEXITCODE -ne 0) { throw "Code signing failed: $($file.FullName)" }
    & "$PSScriptRoot/verify-windows-signatures.ps1" -Path $file.FullName -RequireTimestamp
}
