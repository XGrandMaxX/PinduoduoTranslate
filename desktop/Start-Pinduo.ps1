param()
$ErrorActionPreference='Stop'
$taskRoot=Split-Path $PSScriptRoot -Parent
$deps=Join-Path $taskRoot '.validation-deps'
$audit=Join-Path $taskRoot '.audit'
[void][IO.Directory]::CreateDirectory($audit)
$python=Join-Path $deps 'device-runtime\Scripts\python.exe'
$adb=Join-Path $deps 'android\platform-tools\adb.exe'
$emulator=Join-Path $deps 'android\emulator\emulator.exe'
$engine=Join-Path $deps 'translation-engine\llama-server.exe'
$model=Join-Path $deps 'models\HY-MT1.5-1.8B-Q4_K_M.gguf'
foreach($required in @($python,$adb,$emulator,$engine,$model)) {
    if(!(Test-Path -LiteralPath $required)){throw 'Portable runtime is missing; see desktop/README.md.'}
}
function Health([int]$port) {
    try { return Invoke-RestMethod -Uri ('http://127.0.0.1:'+$port+'/health') -TimeoutSec 2 }
    catch { return $null }
}
function Require-OwnListener([int]$port,[string]$exe) {
    $listener=Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if($listener) {
        $process=Get-Process -Id $listener.OwningProcess -ErrorAction Stop
        if($process.Path -ine $exe) {
            # A Windows venv python.exe may launch the base interpreter as its
            # child. Require both our exact launcher parent and app directory.
            $child=Get-CimInstance Win32_Process -Filter ('ProcessId='+$process.Id)
            $parent=Get-CimInstance Win32_Process -Filter ('ProcessId='+$child.ParentProcessId)
            $ownBridge=$port -eq 18889 -and $parent.ExecutablePath -ieq $python -and $child.CommandLine -match '-m uvicorn bridge:app' -and $child.CommandLine.Contains($PSScriptRoot)
            if(!$ownBridge){throw ('Port '+$port+' is used by another program.')}
        }
    }
}
Require-OwnListener 18889 $python
Require-OwnListener 18891 $engine
$avdName=''
$deviceList=(& $adb devices 2>$null) -join "`n"
if($deviceList -match '(?m)^emulator-5554\s+device\s*$') {
    $avdName=(& $adb -s emulator-5554 emu avd name 2>$null | Select-Object -First 1)
    if($avdName -ne 'PinduoLocal'){throw 'Port 5554 belongs to another Android emulator.'}
} elseif(Get-NetTCPConnection -LocalPort 5554 -State Listen -ErrorAction SilentlyContinue) {
    throw 'The emulator console is busy; wait for the existing emulator to finish starting or stopping.'
}
if($avdName -ne 'PinduoLocal') {
    Write-Output 'Starting the local Android environment...'
    $previousAvd=$env:ANDROID_AVD_HOME
    $previousAndroid=$env:ANDROID_USER_HOME
    try {
        $env:ANDROID_AVD_HOME=Join-Path $deps 'android-avd'
        $env:ANDROID_USER_HOME=Join-Path $deps 'android-user'
        Start-Process -FilePath $emulator -ArgumentList @('-avd','PinduoLocal','-no-window','-no-audio','-no-boot-anim','-no-snapshot','-gpu','host','-feature','-Vulkan','-port','5554') -WindowStyle Hidden -RedirectStandardOutput (Join-Path $audit 'android-emulator.log') -RedirectStandardError (Join-Path $audit 'android-emulator-error.log') | Out-Null
    } finally {$env:ANDROID_AVD_HOME=$previousAvd;$env:ANDROID_USER_HOME=$previousAndroid}
}
$deadline=[DateTime]::UtcNow.AddMinutes(2)
do {
    $boot=(& $adb -s emulator-5554 shell getprop sys.boot_completed 2>$null)
    if($boot -eq '1'){break}
    if([DateTime]::UtcNow -gt $deadline){throw 'Android did not finish booting.'}
    Start-Sleep -Seconds 1
} while($true)
# Reduce only our virtual Android display. Keep its logical dp size the same,
# so card/button layout stays proportional while rendering far fewer pixels.
$displaySize=(& $adb -s emulator-5554 shell wm size 2>$null) -join ' '
if($displaySize -notmatch 'Override size: 480x1040') {
    & $adb -s emulator-5554 shell wm size 480x1040 | Out-Null
}
$displayDensity=(& $adb -s emulator-5554 shell wm density 2>$null) -join ' '
if($displayDensity -notmatch 'Override density: 213') {
    & $adb -s emulator-5554 shell wm density 213 | Out-Null
}
if(!(Health 18891)) {
    Write-Output 'Starting local Russian translation...'
    $keyPath=Join-Path $audit 'translation-local.key'
    if(!(Test-Path -LiteralPath $keyPath)) {
        $keyBytes=New-Object byte[] 32
        $rng=[Security.Cryptography.RandomNumberGenerator]::Create()
        try {$rng.GetBytes($keyBytes)} finally {$rng.Dispose()}
        [IO.File]::WriteAllText($keyPath,[BitConverter]::ToString($keyBytes).Replace('-',''))
    }
    $engineArgs=@('-m',('"'+$model+'"'),'--host','127.0.0.1','--port','18891','-ngl','99','-c','4096','-np','1','-t','4','-tb','4','--no-webui','--no-slots','--no-agent','--api-key-file',('"'+$keyPath+'"'),'--log-disable')
    Start-Process -FilePath $engine -ArgumentList $engineArgs -WorkingDirectory (Split-Path $engine -Parent) -WindowStyle Hidden -RedirectStandardOutput (Join-Path $audit 'translation-engine.log') -RedirectStandardError (Join-Path $audit 'translation-engine-error.log') | Out-Null
    $deadline=[DateTime]::UtcNow.AddSeconds(45)
    while(!(Health 18891)) {
        if([DateTime]::UtcNow -gt $deadline){throw 'Local translation engine did not start.'}
        Start-Sleep -Seconds 1
    }
}
if(!(Health 18889)) {
    Write-Output 'Starting the translated screen...'
    Start-Process -FilePath $python -ArgumentList @('-m','uvicorn','bridge:app','--app-dir',('"'+$PSScriptRoot+'"'),'--host','127.0.0.1','--port','18889','--ws','wsproto','--no-access-log','--log-level','warning') -WorkingDirectory $taskRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $audit 'desktop-bridge.log') -RedirectStandardError (Join-Path $audit 'desktop-bridge-error.log') | Out-Null
    $deadline=[DateTime]::UtcNow.AddSeconds(30)
    while(!(Health 18889)) {
        if([DateTime]::UtcNow -gt $deadline){throw 'Screen connection did not start.'}
        Start-Sleep -Seconds 1
    }
}
Write-Output 'PC viewer: http://127.0.0.1:18889/'
