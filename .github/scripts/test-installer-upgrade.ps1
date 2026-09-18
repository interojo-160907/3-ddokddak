param(
    [Parameter(Mandatory=$true)][string]$PriorInstaller,
    [Parameter(Mandatory=$true)][string]$Installer,
    [Parameter(Mandatory=$true)][string]$ExpectedVersion,
    [string]$PriorVersion = '2.6.4'
)
$ErrorActionPreference = 'Stop'
if ($env:GITHUB_ACTIONS -ne 'true') { throw 'Installer upgrade test runs only on a disposable GitHub runner.' }
$testApp = Join-Path $env:RUNNER_TEMP ('Production3UpgradeTest-' + $PriorVersion)
$registry = 'HKCU:\Software\Interojo\DdokddakProduction3'
$testData = Join-Path $env:RUNNER_TEMP ('Production3ExistingData-' + $PriorVersion)
New-Item -Path $registry -Force | Out-Null
Set-ItemProperty -LiteralPath $registry -Name DataRoot -Value $testData
function Install-TestVersion([string]$SetupPath) {
    $process = Start-Process -FilePath $SetupPath -ArgumentList @(
        '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/TASKS=""', ('/DIR="' + $testApp + '"')
    ) -WindowStyle Hidden -PassThru
    if (-not $process.WaitForExit(180000)) { Stop-Process -Id $process.Id -Force; throw 'Installer timed out.' }
    if ($process.ExitCode -ne 0) { throw "Installer failed: $($process.ExitCode)" }
}
Install-TestVersion $PriorInstaller
$oldExe = Join-Path $testApp 'gui_app_pyside6.exe'
if ((Get-Item -LiteralPath $oldExe).VersionInfo.ProductVersion -ne $PriorVersion) { throw 'Incorrect legacy installer.' }
$beforeRoot = (Get-ItemProperty -LiteralPath $registry).DataRoot
if (-not $beforeRoot) { throw 'Legacy data path was not registered.' }
$settings = Join-Path $beforeRoot 'settings'
New-Item -ItemType Directory -Path $settings -Force | Out-Null
$sentinel = Join-Path $settings 'upgrade-test-user-settings.txt'
'existing-user-data-must-remain' | Set-Content -LiteralPath $sentinel -Encoding utf8
$preserved = @{}
foreach ($relative in @('inventory-status\hydration_instructions.json', 'inventory-status\snapshots\prior-stock.json', 'inventory-status\view-snapshots\prior-view.json', 'settings\inventory_bootstrap.json', 'settings\collection_schedule.json', '리드지 PDF 백업\keep.pdf', '리드지 수동 등록\keep.txt', 'live-production-need\current_production_need.sqlite')) {
    $path = Join-Path $beforeRoot $relative
    New-Item -ItemType Directory -Path (Split-Path $path -Parent) -Force | Out-Null
    "existing-$PriorVersion-data-$relative" | Set-Content -LiteralPath $path -Encoding utf8
    $preserved[$path] = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash
}
Install-TestVersion $Installer
$afterRoot = (Get-ItemProperty -LiteralPath $registry).DataRoot
if ($beforeRoot -ne $afterRoot) { throw 'Upgrade changed the data root.' }
if ((Get-Content -LiteralPath $sentinel -Raw).Trim() -ne 'existing-user-data-must-remain') { throw 'Upgrade lost user data.' }
foreach ($directory in @('live-production-need\snapshot','bom\snapshot','process-status\snapshot','production-performance\snapshot','settings','안전모드')) {
    if (-not (Test-Path -LiteralPath (Join-Path $afterRoot $directory))) { throw "Missing data directory: $directory" }
}
if ((Get-Item -LiteralPath $oldExe).VersionInfo.ProductVersion -ne $ExpectedVersion) { throw 'Installed executable version mismatch.' }
$report = Join-Path $env:RUNNER_TEMP ('installed-upgrade-smoke-' + $PriorVersion + '.json')
$env:QT_QPA_PLATFORM = 'offscreen'
$smoke = Start-Process -FilePath $oldExe -ArgumentList '--package-smoke-test', ('"' + $report + '"') -WindowStyle Hidden -PassThru
if (-not $smoke.WaitForExit(60000)) { Stop-Process -Id $smoke.Id -Force; throw 'Installed executable simulation timed out.' }
if ($smoke.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $report)) { throw 'Installed executable simulation failed.' }
$result = Get-Content -LiteralPath $report -Raw | ConvertFrom-Json
if (-not $result.ok -or $result.app_version -ne $ExpectedVersion -or $result.restored_from -ne '2.6.4' -or $result.rollback_checks.Count -ne 6) { throw 'Incomplete installed recovery verification.' }
foreach ($path in $preserved.Keys) {
    if (-not (Test-Path -LiteralPath $path) -or (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -ne $preserved[$path]) { throw "Existing data changed: $path" }
}
Get-Content -LiteralPath $report
Write-Output "PASS: installed v$PriorVersion -> v$ExpectedVersion; data root and 9 existing files preserved; v2.6.4 runtime recovery verified."
