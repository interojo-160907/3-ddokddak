param(
    [Parameter(Mandatory=$true)][string]$PriorInstaller,
    [Parameter(Mandatory=$true)][string]$Installer,
    [Parameter(Mandatory=$true)][string]$ExpectedVersion
)
$ErrorActionPreference = 'Stop'
if ($env:GITHUB_ACTIONS -ne 'true') { throw 'Installer upgrade test runs only on a disposable GitHub runner.' }
$testApp = Join-Path $env:RUNNER_TEMP 'Production3UpgradeTest'
$registry = 'HKCU:\Software\Interojo\DdokddakProduction3'
function Install-TestVersion([string]$SetupPath) {
    $process = Start-Process -FilePath $SetupPath -ArgumentList @(
        '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/TASKS=""', ('/DIR="' + $testApp + '"')
    ) -WindowStyle Hidden -PassThru
    if (-not $process.WaitForExit(180000)) { Stop-Process -Id $process.Id -Force; throw 'Installer timed out.' }
    if ($process.ExitCode -ne 0) { throw "Installer failed: $($process.ExitCode)" }
}
Install-TestVersion $PriorInstaller
$oldExe = Join-Path $testApp 'gui_app_pyside6.exe'
if ((Get-Item -LiteralPath $oldExe).VersionInfo.ProductVersion -ne '2.6.4') { throw 'Incorrect legacy installer.' }
$beforeRoot = (Get-ItemProperty -LiteralPath $registry).DataRoot
if (-not $beforeRoot) { throw 'Legacy data path was not registered.' }
$settings = Join-Path $beforeRoot 'settings'
New-Item -ItemType Directory -Path $settings -Force | Out-Null
$sentinel = Join-Path $settings 'upgrade-test-user-settings.txt'
'existing-user-data-must-remain' | Set-Content -LiteralPath $sentinel -Encoding utf8
Install-TestVersion $Installer
$afterRoot = (Get-ItemProperty -LiteralPath $registry).DataRoot
if ($beforeRoot -ne $afterRoot) { throw 'Upgrade changed the data root.' }
if ((Get-Content -LiteralPath $sentinel -Raw).Trim() -ne 'existing-user-data-must-remain') { throw 'Upgrade lost user data.' }
foreach ($directory in @('inventory-status\snapshots','inventory-status\view-snapshots','live-production-need\snapshot','bom\snapshot')) {
    if (-not (Test-Path -LiteralPath (Join-Path $afterRoot $directory))) { throw "Missing data directory: $directory" }
}
if ((Get-Item -LiteralPath $oldExe).VersionInfo.ProductVersion -ne $ExpectedVersion) { throw 'Installed executable version mismatch.' }
$report = Join-Path $env:RUNNER_TEMP 'installed-upgrade-smoke.json'
$env:QT_QPA_PLATFORM = 'offscreen'
$smoke = Start-Process -FilePath $oldExe -ArgumentList '--package-smoke-test', ('"' + $report + '"') -WindowStyle Hidden -PassThru
if (-not $smoke.WaitForExit(60000)) { Stop-Process -Id $smoke.Id -Force; throw 'Installed executable simulation timed out.' }
if ($smoke.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $report)) { throw 'Installed executable simulation failed.' }
$result = Get-Content -LiteralPath $report -Raw | ConvertFrom-Json
if (-not $result.ok -or $result.inventory_simulation.checks.Count -lt 13) { throw 'Incomplete installed simulation.' }
Get-Content -LiteralPath $report
Write-Output "PASS: installed v2.6.4 -> v$ExpectedVersion; data root and user settings preserved; inventory simulation completed."
