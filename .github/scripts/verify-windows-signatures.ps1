param(
    [Parameter(Mandatory=$true)][string]$Path,
    [switch]$RequireTimestamp
)
$ErrorActionPreference = 'Stop'
$target = Get-Item -LiteralPath $Path
$files = @(if ($target.PSIsContainer) {
    Get-ChildItem -LiteralPath $target.FullName -File -Recurse | Where-Object {
        $_.Extension.ToLowerInvariant() -in @('.exe', '.dll', '.pyd')
    }
} else { $target })
if ($files.Count -eq 0) { throw "No executable files found: $Path" }
foreach ($file in $files) {
    $signature = Get-AuthenticodeSignature -LiteralPath $file.FullName
    if ($signature.Status -ne 'Valid') {
        throw "Untrusted executable ($($signature.Status)): $($file.FullName)"
    }
    if ($signature.SignatureType -ne 'Authenticode') {
        throw "Portable embedded Authenticode signature required: $($file.FullName)"
    }
    # Smart App Control currently supports RSA code-signing certificates.
    if ($signature.SignerCertificate.PublicKey.Oid.Value -ne '1.2.840.113549.1.1.1') {
        throw "RSA code-signing certificate required: $($file.FullName)"
    }
    if ($RequireTimestamp -and $null -eq $signature.TimeStamperCertificate) {
        throw "Signing timestamp missing: $($file.FullName)"
    }
}
Write-Output "PASS: $($files.Count) executable signatures verified. This does not certify an employee PC's App Control policy."
