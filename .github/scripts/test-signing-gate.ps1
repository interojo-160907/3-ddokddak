$ErrorActionPreference = 'Stop'
$fixture = Join-Path ([IO.Path]::GetTempPath()) ('ddokddak-signature-test-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $fixture | Out-Null
$unsigned = Join-Path $fixture 'unsigned.exe'
[IO.File]::WriteAllBytes($unsigned, [byte[]](77, 90, 0, 0))
$rejected = $false
try { & "$PSScriptRoot/verify-windows-signatures.ps1" -Path $unsigned } catch {
    if ($_.Exception.Message -notlike 'Untrusted executable*') { throw }
    $rejected = $true
}
if (-not $rejected) { throw 'Unsigned executable passed the release gate.' }
$signed = (Get-Command python).Source
& "$PSScriptRoot/verify-windows-signatures.ps1" -Path $signed
# A valid file in the same folder must not hide an unsigned dependency.
Copy-Item -LiteralPath $signed -Destination (Join-Path $fixture 'vendor.exe')
$rejected = $false
try { & "$PSScriptRoot/verify-windows-signatures.ps1" -Path $fixture } catch {
    if ($_.Exception.Message -notlike 'Untrusted executable*') { throw }
    $rejected = $true
}
if (-not $rejected) { throw 'Unsigned dependency passed the directory gate.' }
$savedSignTool = $env:DDOKDDAK_SIGNTOOL
$savedThumbprint = $env:DDOKDDAK_CERT_THUMBPRINT
try {
    $env:DDOKDDAK_SIGNTOOL = ''
    $rejected = $false
    try { & "$PSScriptRoot/sign-windows.ps1" -Path $unsigned } catch {
        if ($_.Exception.Message -notlike 'DDOKDDAK_SIGNTOOL*') { throw }
        $rejected = $true
    }
    if (-not $rejected) { throw 'Missing signing credentials did not stop signing.' }
    # With an unused test identity, a valid vendor file must be preserved byte-for-byte.
    # No signing command should be invoked for this path.
    $env:DDOKDDAK_SIGNTOOL = $signed
    $env:DDOKDDAK_CERT_THUMBPRINT = '0000000000000000000000000000000000000000'
    $vendor = Join-Path $fixture 'vendor.exe'
    $before = (Get-FileHash -LiteralPath $vendor).Hash
    & "$PSScriptRoot/sign-windows.ps1" -Path $vendor
    if ((Get-FileHash -LiteralPath $vendor).Hash -ne $before) { throw 'Vendor signature was overwritten.' }
    $tampered = Join-Path $fixture 'tampered.exe'
    $bytes = [IO.File]::ReadAllBytes($vendor)
    $bytes[4096] = $bytes[4096] -bxor 1
    [IO.File]::WriteAllBytes($tampered, $bytes)
    $rejected = $false
    try { & "$PSScriptRoot/sign-windows.ps1" -Path $tampered } catch {
        if ($_.Exception.Message -notlike 'Refusing to cover an invalid signature*') { throw }
        $rejected = $true
    }
    if (-not $rejected) { throw 'A modified signed binary was accepted for re-signing.' }
} finally {
    $env:DDOKDDAK_SIGNTOOL = $savedSignTool
    $env:DDOKDDAK_CERT_THUMBPRINT = $savedThumbprint
}
Write-Output 'PASS: unsigned file/dependency rejected; valid vendor signature preserved; tampered signature and missing signing configuration rejected.'
