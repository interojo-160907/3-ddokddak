param([Parameter(Mandatory=$true)][string]$Installer)
$ErrorActionPreference = 'Stop'
if ($env:GITHUB_ACTIONS -ne 'true') { throw 'Run only on a disposable GitHub runner.' }
$testApp = Join-Path $env:RUNNER_TEMP 'Production3FreshReinstall'
$testData = Join-Path $env:RUNNER_TEMP 'Production3FreshReinstallData'
$registry = 'HKCU:\Software\Interojo\DdokddakProduction3'
New-Item -Path $registry -Force | Out-Null
Set-ItemProperty -LiteralPath $registry -Name DataRoot -Value $testData
function Install-Current {
    $process = Start-Process -FilePath $Installer -ArgumentList @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/TASKS=""',('/DIR="' + $testApp + '"')) -WindowStyle Hidden -PassThru
    if (-not $process.WaitForExit(180000) -or $process.ExitCode -ne 0) { throw 'Install failed.' }
}
Install-Current
$settings = Join-Path $testData 'backward-v1\settings'
if (-not (Test-Path -LiteralPath $settings)) { throw 'Fresh install omitted Backward directories.' }
$keep = Join-Path $testData 'settings\reinstall-sentinel.txt'
'user-settings' | Set-Content -LiteralPath $keep
$marker = Join-Path $settings 'version_collection_bootstrap.json'
'{"completed_version":"2.8.4"}' | Set-Content -LiteralPath $marker
Install-Current
if (Test-Path -LiteralPath $marker) { throw 'Reinstall did not request full collection.' }
$uninstaller = (Get-ChildItem -LiteralPath $testApp -Filter 'unins*.exe' | Select-Object -First 1).FullName
$process = Start-Process -FilePath $uninstaller -ArgumentList '/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART' -WindowStyle Hidden -PassThru
if (-not $process.WaitForExit(180000) -or $process.ExitCode -ne 0) { throw 'Uninstall failed.' }
if ((Get-Content -LiteralPath $keep -Raw).Trim() -ne 'user-settings') { throw 'Uninstall removed user data.' }
if ((Get-ItemProperty -LiteralPath $registry).DataRoot -ne $testData) { throw 'Uninstall lost custom data root.' }
Install-Current
if ((Get-Content -LiteralPath $keep -Raw).Trim() -ne 'user-settings') { throw 'Reinstall lost settings.' }
if (Test-Path -LiteralPath $marker) { throw 'Reinstall did not request full collection.' }
Write-Output 'PASS: fresh install, reinstall, uninstall preservation, reinstall after uninstall.'
