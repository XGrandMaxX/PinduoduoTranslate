# Allow one paired USB tunnel peer to deliver video to our fixed UDP port.
$ErrorActionPreference='Stop'
$taskRoot=Split-Path $PSScriptRoot -Parent
$route=Get-Content -LiteralPath (Join-Path $taskRoot '.audit\iphone-media-route.json') -Raw | ConvertFrom-Json
$local=[Net.IPAddress]::Parse($route.local)
$remote=[Net.IPAddress]::Parse($route.remote)
if($local.AddressFamily -ne 'InterNetworkV6' -or $remote.AddressFamily -ne 'InterNetworkV6' -or $route.port -ne 18899){throw 'Invalid media route'}
$a=$local.GetAddressBytes();$b=$remote.GetAddressBytes()
if(($a[0] -band 254) -ne 252 -or ($b[0] -band 254) -ne 252){throw 'Only a local USB tunnel address is allowed'}
for($i=0;$i -lt 8;$i++){if($a[$i] -ne $b[$i]){throw 'The peer must be on the same USB tunnel'}}
$adapter=Get-NetIPAddress -IPAddress $route.local -ErrorAction Stop
if($adapter.InterfaceAlias -notlike 'pymobiledevice3-tunnel-*'){throw 'Not our Apple USB tunnel'}
$python=Join-Path $taskRoot '.validation-deps\device-runtime\Scripts\python.exe'
$program=(& $python -c 'import sys; print(sys._base_executable)').Trim()
if(!(Test-Path -LiteralPath $program)){throw 'Python runtime not found'}
$existing=Get-NetFirewallRule -Name 'Pinduo-iPhone-Video' -ErrorAction SilentlyContinue
if($existing){Remove-NetFirewallRule -Name 'Pinduo-iPhone-Video'}
New-NetFirewallRule -Name 'Pinduo-iPhone-Video' -DisplayName 'Pinduo iPhone video (USB peer only)' -Direction Inbound -Action Allow -Protocol UDP -LocalPort 18899 -LocalAddress $route.local -RemoteAddress $route.remote -Program $program -Profile Any -InterfaceAlias $adapter.InterfaceAlias | Out-Null
