# launch a heavy validation job detached; output -> logs\v41_<name>.log
param([string]$Name)
$log = "C:\PrOxyTradingTerminal\logs\v41_$Name.log"
$err = "C:\PrOxyTradingTerminal\logs\v41_$Name.err.log"
Remove-Item $log,$err -ErrorAction SilentlyContinue
$p = Start-Process -FilePath "python" -ArgumentList "tools\_v41_$Name.py" -WorkingDirectory "C:\PrOxyTradingTerminal" -RedirectStandardOutput $log -RedirectStandardError $err -PassThru -WindowStyle Hidden
Set-Content "C:\PrOxyTradingTerminal\logs\v41_$Name.pid" $p.Id
Write-Output "started $Name pid=$($p.Id) log=$log"
