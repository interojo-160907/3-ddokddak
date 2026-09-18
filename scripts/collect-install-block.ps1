param(
    [string]$InstallerPath,
    [string]$OutputDirectory = (Join-Path ([Environment]::GetFolderPath('Desktop')) ('똑딱이-설치진단-' + (Get-Date -Format 'yyyyMMdd-HHmmss')))
)
# Read-only diagnostics. No policy changes, installer launch, or network upload.
$ErrorActionPreference = 'Stop'
New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
$summary = [ordered]@{ collected_at = (Get-Date).ToString('o'); errors = @() }
try {
    $windows = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion'
    $summary.windows = @{ build = $windows.CurrentBuild; revision = $windows.UBR; display_version = $windows.DisplayVersion }
    $summary.smart_app_control_state = (Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\CI\Policy' -Name VerifiedAndReputablePolicyState -ErrorAction Stop).VerifiedAndReputablePolicyState
} catch { $summary.errors += 'Windows/SAC state: ' + $_.Exception.Message }
try {
    $events = @(Get-WinEvent -FilterHashtable @{
        LogName = 'Microsoft-Windows-CodeIntegrity/Operational'
        Id = @(3033, 3077, 3089, 3099)
        StartTime = (Get-Date).AddDays(-3)
    } -MaxEvents 1000 -ErrorAction Stop)
    $summary.event_count = $events.Count
    $summary.event_limit = 1000
    # XML includes the blocked path, hash, policy ID, and ActivityID for correlation.
    ('<Events>' + (($events | ForEach-Object { $_.ToXml() }) -join "`n") + '</Events>') |
        Set-Content -LiteralPath (Join-Path $OutputDirectory 'CodeIntegrity.xml') -Encoding utf8
    $events | Select-Object TimeCreated, Id, Message | Format-List | Out-String -Width 300 |
        Set-Content -LiteralPath (Join-Path $OutputDirectory '차단기록.txt') -Encoding utf8
} catch { $summary.errors += 'CodeIntegrity log: ' + $_.Exception.Message }
if ($InstallerPath) {
    try {
        $file = Get-Item -LiteralPath $InstallerPath
        $signature = Get-AuthenticodeSignature -LiteralPath $file.FullName
        $summary.installer = @{
            name = $file.Name; version = $file.VersionInfo.ProductVersion
            bytes = $file.Length; sha256 = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash
            signature_status = [string]$signature.Status
        }
    } catch { $summary.errors += 'Installer metadata: ' + $_.Exception.Message }
}
$summary | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $OutputDirectory '요약.json') -Encoding utf8
Write-Output "Diagnostics saved locally: $OutputDirectory"
Write-Output 'Review file paths and other application names before sharing. No files were uploaded.'
