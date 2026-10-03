param([Parameter(Mandatory=$true)][string]$Payload,[Parameter(Mandatory=$true)][string]$Installer)
$ErrorActionPreference='Stop'
$Payload=(Resolve-Path $Payload).Path
$Installer=(Resolve-Path $Installer).Path
$ReportDir=Join-Path (Split-Path $Installer) 'validation'
New-Item -ItemType Directory -Force $ReportDir | Out-Null
Start-Transcript -Path (Join-Path $ReportDir 'windows-validation.txt')
try {
    foreach($entry in @(@('runtime\python.exe','Python Software Foundation'),@('runtime\pythonw.exe','Python Software Foundation'),@('prerequisites\MicrosoftEdgeWebView2RuntimeInstallerX64.exe','Microsoft Corporation'))) {
        $signature=Get-AuthenticodeSignature (Join-Path $Payload $entry[0])
        if($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notlike ('*'+$entry[1]+'*')) { throw ('Invalid publisher signature: '+$entry[0]) }
    }
    $hashes=Get-Content (Join-Path $Payload 'payload-sha256.json') -Raw | ConvertFrom-Json
    foreach($file in $hashes.PSObject.Properties) {
        if((Get-FileHash (Join-Path $Payload $file.Name) -Algorithm SHA256).Hash.ToLower() -ne $file.Value) { throw ('Payload integrity failed: '+$file.Name) }
    }
    $status=Get-MpComputerStatus
    if(-not $status.AntivirusEnabled -or -not $status.AMServiceEnabled) { throw 'Microsoft Defender is unavailable. Release stays blocked.' }
    Update-MpSignature -UpdateSource MMPC
    $status=Get-MpComputerStatus
    if($status.AntivirusSignatureLastUpdated -lt (Get-Date).AddDays(-2)) { throw 'Defender signatures are stale. Release stays blocked.' }
    $started=Get-Date
    $scanner=Get-ChildItem "$env:ProgramData\Microsoft\Windows Defender\Platform" -Directory | Sort-Object Name -Descending | Select-Object -First 1
    $mp=Join-Path $scanner.FullName 'MpCmdRun.exe'
    foreach($target in @($Payload,$Installer)) {
        & $mp -Scan -ScanType 3 -File $target
        if($LASTEXITCODE -ne 0) { throw ('Defender scan did not pass: '+$target) }
    }
    $detections=Get-MpThreatDetection | Where-Object { $_.InitialDetectionTime -ge $started }
    if($detections) { $detections | Format-List; throw 'Threat detection recorded. Release stays blocked.' }
    $wv=Join-Path $Payload 'prerequisites\MicrosoftEdgeWebView2RuntimeInstallerX64.exe'
    $prereq=Start-Process -FilePath $wv -ArgumentList '/silent','/install' -Wait -PassThru
    $runtime=Join-Path $Payload 'runtime\python.exe'
    & $runtime -c 'import PySide6.QtWidgets, PySide6.QtMultimedia, pymupdf, webview, clr; from webview.platforms import edgechromium; print("Bundled imports passed")'
    if($LASTEXITCODE -ne 0) { throw 'Bundled runtime import check failed' }
    & $runtime (Join-Path $PSScriptRoot 'windows_smoke.py')
    if($LASTEXITCODE -ne 0) { throw 'Windows application smoke check failed' }
    $installation=Join-Path $env:LOCALAPPDATA 'ChableszShowVoiceCamera\v17'
    if(Test-Path $installation) { throw 'Validation requires a clean Windows runner; refusing to alter an existing installation.' }
    $p=Start-Process -FilePath $Installer -ArgumentList '/S' -Wait -PassThru
    if($p.ExitCode -ne 0) { throw ('Silent installer failed: '+$p.ExitCode) }
    if(-not (Test-Path (Join-Path $installation 'app\app.py'))) { throw 'Installed app is missing' }
    if(-not (Test-Path (Join-Path $env:USERPROFILE 'Desktop\ChableszShow Voice Camera.lnk'))) { throw 'Desktop shortcut missing' }
    & (Join-Path $installation 'runtime\python.exe') (Join-Path $PSScriptRoot 'windows_smoke.py')
    if($LASTEXITCODE -ne 0) { throw 'Installed runtime smoke check failed' }
    $p=Start-Process -FilePath (Join-Path $installation 'Uninstall.exe') -ArgumentList '/S' -Wait -PassThru
    if($p.ExitCode -ne 0) { throw 'Uninstall failed' }
    Start-Sleep -Seconds 2
    if(Test-Path (Join-Path $installation 'app\app.py')) { throw 'Uninstall left the application executable' }
    $report=@{status='automated_checks_passed';completed_at=(Get-Date).ToUniversalTime().ToString('o');installer_sha256=(Get-FileHash $Installer -Algorithm SHA256).Hash;antivirus='Microsoft Defender';antivirus_signature_version=$status.AntivirusSignatureVersion;limitations=@('Installer is unsigned unless a publisher certificate is separately supplied.','Manual HDMI/audio/PowerPoint/YouTube validation remains required.','A clean scan is not a guarantee against all malware.')}
    $report | ConvertTo-Json -Depth 5 | Set-Content (Join-Path $ReportDir 'release-checks.json')
} finally { Stop-Transcript }
