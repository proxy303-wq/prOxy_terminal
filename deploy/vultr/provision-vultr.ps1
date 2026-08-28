# PrOxy Trading Terminal - provision Vultr VPS in Bangalore (Indian IP)
# Usage: pwsh -File deploy/vultr/provision-vultr.ps1 -ApiKey <VULTR_API_TOKEN>
param([Parameter(Mandatory = $true)][string]$ApiKey)
$ErrorActionPreference = 'Stop'

$headers = @{ Authorization = "Bearer $ApiKey" }
$region  = 'blr'            # Bangalore, India (also: del = Delhi NCR)
$plan    = 'vc2-1c-2gb'     # 1 vCPU / 2 GB RAM / 55 GB SSD
$label   = 'proxy-terminal'
$sshName = 'proxy-terminal'
$REPO    = 'C:PrOxyTradingTerminal'
$keyPath = Join-Path $env:USERPROFILE '.sshultr_proxy'
$SECRETS = Join-Path $env:TEMP 'fly_secrets_import.txt'

Write-Host '== 1. SSH key ================'
if (-not (Test-Path "$keyPath.pub")) {
  ssh-keygen -t ed25519 -N '""' -f $keyPath | Out-Null
  Write-Host "generated $keyPath"
}
$pub  = (Get-Content "$keyPath.pub" -Raw).Trim()
$keys = (Invoke-RestMethod -Uri 'https://api.vultr.com/v2/ssh-keys' -Headers $headers).ssh_keys
$keyId = ($keys | Where-Object { $_.name -eq $sshName }).id
if (-not $keyId) {
  $body = @{ name = $sshName; ssh_key = $pub } | ConvertTo-Json
  $keyId = (Invoke-RestMethod -Method Post -Uri 'https://api.vultr.com/v2/ssh-keys' -Headers $headers -Body $body -ContentType 'application/json').ssh_key.id
  Write-Host "ssh key registered: $keyId"
} else { Write-Host "ssh key exists: $keyId" }

Write-Host '== 2. Ubuntu 24.04 image ====='
$osId = ((Invoke-RestMethod -Uri 'https://api.vultr.com/v2/os' -Headers $headers).os | Where-Object { $_.name -like 'Ubuntu 24.04 LTS x64*' } | Select-Object -First 1).id
if (-not $osId) { throw 'Ubuntu 24.04 LTS x64 not found in Vultr OS list' }

Write-Host '== 3. Deploy instance (Bangalore) =='
$body = @{ region = $region; plan = $plan; os_id = $osId; label = $label; hostname = $label; sshkey_id = @($keyId) } | ConvertTo-Json -Depth 4
$inst = (Invoke-RestMethod -Method Post -Uri 'https://api.vultr.com/v2/instances' -Headers $headers -Body $body -ContentType 'application/json').instance
Write-Host ("instance id: " + $inst.id)

do {
  Start-Sleep 10
  $inst = (Invoke-RestMethod -Uri ("https://api.vultr.com/v2/instances/" + $inst.id) -Headers $headers).instance
  Write-Host ("status: " + $inst.status + " | power: " + $inst.power_status + " | ip: " + $inst.main_ip)
} while ($inst.status -ne 'active' -or $inst.power_status -ne 'running' -or -not $inst.main_ip)
$PUBLIC_IP = $inst.main_ip
Write-Host ("PUBLIC IP: " + $PUBLIC_IP)
Start-Sleep 30   # let the OS finish booting

Write-Host '== 4. Build + ship code ======'
$stage = Join-Path $env:TEMP 'proxy_stage'
if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
New-Item -ItemType Directory -Path $stage | Out-Null
robocopy $REPO $stage /E /XD .git __pycache__ .athena_ref tests logs reports .pytest_cache .venv /XF *.env .env dhan_token.txt *.pyc /NFL /NDL /NJH /NJS /NC /NS | Out-Null
if ($LASTEXITCODE -ge 8) { throw 'robocopy failed' }
$tar = Join-Path $env:TEMP 'proxy.tar.gz'
if (Test-Path $tar) { Remove-Item $tar -Force }
tar -czf $tar -C $stage .
$envClean = Join-Path $env:TEMP 'proxy.env'
(Get-Content $SECRETS) -replace [char]13, '' | Set-Content $envClean -Encoding ascii

$REMOTE = 'root@' + $PUBLIC_IP
$SSH = "ssh -i $keyPath -o StrictHostKeyChecking=no -o ConnectTimeout=30 $REMOTE"
$SCP = "scp -i $keyPath -o StrictHostKeyChecking=no -o ConnectTimeout=30"
Invoke-Expression ('{0} {1} {2}:/root/proxy.tar.gz' -f $SCP, $tar, $REMOTE)
Invoke-Expression ('{0} {1} {2}:/root/.env' -f $SCP, $envClean, $REMOTE)
Invoke-Expression ('{0} {1} {2}:/root/proxy-terminal.service' -f $SCP, ($REPO + '/deploy/aws/proxy-terminal.service'), $REMOTE)
Invoke-Expression ('{0} {1} {2}:/root/setup-instance.sh' -f $SCP, ($REPO + '/deploy/vultr/setup-instance.sh'), $REMOTE)

Write-Host '== 5. Setup on instance (deps install takes a few minutes) =='
Invoke-Expression ("$SSH 'bash /root/setup-instance.sh /root/proxy.tar.gz /root/.env /root/proxy-terminal.service'")

Write-Host '== 6. Verify ================'
Start-Sleep 10
try {
  $h = Invoke-WebRequest -Uri ('http://{0}:8080/_stcore/health' -f $PUBLIC_IP) -UseBasicParsing -TimeoutSec 30
  Write-Host ("HEALTH: " + $h.StatusCode)
} catch { Write-Host ("HEALTH: FAILED - " + $_.Exception.Message) }
Write-Host ('Dashboard : http://{0}:8080' -f $PUBLIC_IP)
Write-Host ('SSH       : ssh -i {0} root@{1}' -f $keyPath, $PUBLIC_IP)
