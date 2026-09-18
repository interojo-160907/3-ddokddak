$ErrorActionPreference = 'Stop'
foreach ($name in @('ARTIFACT_SIGNING_ENDPOINT', 'ARTIFACT_SIGNING_ACCOUNT', 'ARTIFACT_SIGNING_PROFILE')) {
    if (-not [Environment]::GetEnvironmentVariable($name)) { throw "Signing configuration missing: $name" }
}
if ($env:ARTIFACT_SIGNING_ENDPOINT -notmatch '^https://[a-z0-9-]+\.codesigning\.azure\.net/?$') {
    throw 'An official regional Artifact Signing endpoint is required.'
}
$root = Join-Path $env:RUNNER_TEMP 'ddokddak-signing-tools'
New-Item -ItemType Directory -Path $root -Force | Out-Null
foreach ($package in @(
    @('Microsoft.Windows.SDK.BuildTools', '10.0.26100.4188'),
    @('Microsoft.ArtifactSigning.Client', '1.0.128')
)) {
    & nuget install $package[0] -Version $package[1] -Source 'https://api.nuget.org/v3/index.json' -OutputDirectory $root -NonInteractive -DirectDownload
    if ($LASTEXITCODE -ne 0) { throw "Signing tools download failed: $($package[0])" }
}
$signTool = @(Get-ChildItem -LiteralPath $root -Recurse -Filter signtool.exe | Where-Object { $_.Directory.Name -eq 'x64' })
$dlib = @(Get-ChildItem -LiteralPath $root -Recurse -Filter Azure.CodeSigning.Dlib.dll | Where-Object { $_.Directory.Name -eq 'x64' })
if ($signTool.Count -ne 1 -or $dlib.Count -ne 1) { throw 'Expected exactly one x64 SignTool and Artifact Signing client.' }
$metadata = Join-Path $root 'metadata.json'
@{
    Endpoint = $env:ARTIFACT_SIGNING_ENDPOINT
    CodeSigningAccountName = $env:ARTIFACT_SIGNING_ACCOUNT
    CertificateProfileName = $env:ARTIFACT_SIGNING_PROFILE
    # azure/login supplies the Azure CLI identity, including for Inno subprocesses.
    ExcludeCredentials = @('EnvironmentCredential', 'WorkloadIdentityCredential', 'ManagedIdentityCredential', 'SharedTokenCacheCredential', 'VisualStudioCredential', 'VisualStudioCodeCredential', 'AzurePowerShellCredential', 'AzureDeveloperCliCredential', 'InteractiveBrowserCredential')
} | ConvertTo-Json | Set-Content -LiteralPath $metadata -Encoding utf8
@(
    "DDOKDDAK_SIGNTOOL=$($signTool[0].FullName)",
    "DDOKDDAK_SIGNING_DLIB=$($dlib[0].FullName)",
    "DDOKDDAK_SIGNING_METADATA=$metadata"
) | Add-Content -LiteralPath $env:GITHUB_ENV -Encoding utf8
