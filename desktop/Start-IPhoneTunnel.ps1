# A dedicated USB-only Apple developer tunnel for this project.
# Run elevated. No existing application service or network profile is changed.
$ErrorActionPreference='Stop'
$taskRoot=Split-Path $PSScriptRoot -Parent
$python=Join-Path $taskRoot '.validation-deps\device-runtime\Scripts\python.exe'
$audit=Join-Path $taskRoot '.audit'
$principal=[Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
if(!$principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){throw 'This USB tunnel requires administrator approval.'}
$listener=Get-NetTCPConnection -LocalPort 18897 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if($listener){
    $child=Get-CimInstance Win32_Process -Filter ('ProcessId='+$listener.OwningProcess)
    $parent=Get-CimInstance Win32_Process -Filter ('ProcessId='+$child.ParentProcessId)
    if($parent.ExecutablePath -ine $python -or $child.CommandLine -notmatch 'pymobiledevice3 remote tunneld' -or $child.CommandLine -notmatch '18897'){throw 'Port 18897 belongs to another process.'}
    exit 0
}
$env:PYTHONUTF8='1'
$env:NO_COLOR='1'
Start-Process -FilePath $python -ArgumentList @('-m','pymobiledevice3','remote','tunneld','--host','127.0.0.1','--port','18897','--protocol','tcp','--no-usb','--usbmux','--no-wifi','--no-mobdev2') -WorkingDirectory $taskRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $audit 'iphone-tunnel.log') -RedirectStandardError (Join-Path $audit 'iphone-tunnel-error.log') | Out-Null
