# PrOxy Trading Terminal - provision Lightsail in Mumbai (ap-south-1)
# Prereqs: AWS CLI installed + 'aws configure' done (keys, region ap-south-1)
# Run from the repo root:  pwsh -File deploy/aws/provision-lightsail.ps1
$ErrorActionPreference = 'Stop'

$REGION   = 'ap-south-1'          # Mumbai - real Indian IP
$AZ       = 'ap-south-1a'
$INSTANCE = 'proxy-terminal'
$KEYPAIR  = 'proxy-terminal'
$STATICIP = 'proxy-terminal-ip'
$DISK     = 'proxy-data'
$BUNDLE   = 'small_2_0'           # 2 vCPU / 2 GB RAM / 60 GB SSD (~$10/mo)
$BLUEPRINT= 'ubuntu_24_04'
$REPO     = 'C:PrOxyTradingTerminal'
$SSHKEY   = Join-Path $env:USERPROFILE '.sshproxy-terminal-lightsail.pem'
$SECRETS  = Join-Path $env:TEMP 'fly_secrets_import.txt'   # staged KEY=VALUE file

Write-Host '== sanity: AWS identity =='
aws sts get-caller-identity --region $REGION
if ($LASTEXITCODE -ne 0) { throw 'AWS not configured - run: aws configure' }

Write-Host '== 1. SSH key pair =='
$kps = (aws lightsail get-key-pairs --region $REGION | ConvertFrom-Json).keyPairs
if (-not ($kps | Where-Object { $_.name -eq $KEYPAIR })) {
  $new = aws lightsail create-key-pair --key-pair-name $KEYPAIR --region $REGION | ConvertFrom-Json
  [IO.File]::WriteAllText($SSHKEY, $new.privateKeyBase64)
  icacls $SSHKEY /inheritance:r /grant:r ($env:USERNAME + ':F') | Out-Null
  Write-Host "key pair created -> $SSHKEY"
} else { Write-Host 'key pair exists' }

Write-Host '== 2. Static IP =='
$ips = (aws lightsail get-static-ips --region $REGION | ConvertFrom-Json).staticIps
$ip = $ips | Where-Object { $_.name -eq $STATICIP }
if (-not $ip) { $ip = (aws lightsail allocate-static-ip --static-ip-name $STATICIP --region $REGION | ConvertFrom-Json).staticIp }
if (-not $ip.isAttached) { aws lightsail attach-static-ip --static-ip-name $STATICIP --instance-name $INSTANCE --region $REGION | Out-Null }
$ip = (aws lightsail get-static-ips --region $REGION | ConvertFrom-Json).staticIps | Where-Object { $_.name -eq $STATICIP }
$PUBLIC_IP = $ip.ipAddress
Write-Host "public IP: $PUBLIC_IP"

Write-Host '== 3. Instance (Mumbai) =='
$inst = aws lightsail get-instance --instance-name $INSTANCE --region $REGION 2>$null | ConvertFrom-Json
if (-not $inst) {
  aws lightsail create-instances --instance-names $INSTANCE --availability-zone $AZ --blueprint-id $BLUEPRINT --bundle-id $BUNDLE --key-pair-name $KEYPAIR --user-data "file://$REPO/deploy/aws/lightsail-userdata.sh" --region $REGION | Out-Null
}
do {
  Start-Sleep 10
  $inst = (aws lightsail get-instance --instance-name $INSTANCE --region $REGION | ConvertFrom-Json)
  Write-Host ("instance state: " + $inst.instance.state.name)
} while ($inst.instance.state.name -ne 'running')
Start-Sleep 20   # let user-data bootstrap begin

Write-Host '== 4. Firewall (22 + 8080) =='
$ports = @( @{fromPort=22; toPort=22; protocol='tcp'}, @{fromPort=8080; toPort=8080; protocol='tcp'} )
aws lightsail put-instance-public-ports --instance-name $INSTANCE --region $REGION --port-info $ports | Out-Null

Write-Host '== 5. Data volume (20 GB) =='
$disk = aws lightsail get-disk --disk-name $DISK --region $REGION 2>$null | ConvertFrom-Json
if (-not $disk) { aws lightsail create-disk --disk-name $DISK --availability-zone $AZ --size-in-gb 20 --region $REGION | Out-Null }
$disk = (aws lightsail get-disk --disk-name $DISK --region $REGION | ConvertFrom-Json).disk
if (-not $disk.isAttached) {
  aws lightsail attach-disk --disk-name $DISK --instance-name $INSTANCE --disk-path /dev/xvdf --region $REGION | Out-Null
}
do {
  Start-Sleep 5
  $disk = (aws lightsail get-disk --disk-name $DISK --region $REGION | ConvertFrom-Json).disk
} while ($disk.state -ne 'in-use' -or -not $disk.isAttached)
Write-Host 'volume attached'

Write-Host '== 6. Build + ship code =='
$stage = Join-Path $env:TEMP 'proxy_stage'
if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
New-Item -ItemType Directory -Path $stage | Out-Null
robocopy $REPO $stage /E /XD .git __pycache__ .athena_ref tests logs reports .pytest_cache .venv /XF *.env .env dhan_token.txt *.pyc /NFL /NDL /NJH /NJS /NC /NS | Out-Null
if ($LASTEXITCODE -ge 8) { throw 'robocopy failed' }
$tar = Join-Path $env:TEMP 'proxy.tar.gz'
if (Test-Path $tar) { Remove-Item $tar -Force }
tar -czf $tar -C $stage .
# secrets: strip CR (systemd EnvironmentFile hates CRLF)
$envClean = Join-Path $env:TEMP 'proxy.env'
(Get-Content $SECRETS) -replace [char]13, '' | Set-Content $envClean -Encoding ascii

$REMOTE = 'ubuntu@{0}' -f $PUBLIC_IP
$SSH = "ssh -i $SSHKEY -o StrictHostKeyChecking=no -o ConnectTimeout=30 $REMOTE"
$SCP = "scp -i $SSHKEY -o StrictHostKeyChecking=no -o ConnectTimeout=30"
Invoke-Expression ('{0} {1} {2}:/home/ubuntu/proxy.tar.gz' -f $SCP, $tar, $REMOTE)
Invoke-Expression ('{0} {1} {2}:/home/ubuntu/.env' -f $SCP, $envClean, $REMOTE)
Invoke-Expression ('{0} {1} {2}:/home/ubuntu/proxy-terminal.service' -f $SCP, ($REPO + '/deploy/aws/proxy-terminal.service'), $REMOTE)
Invoke-Expression ('{0} {1} {2}:/home/ubuntu/setup-instance.sh' -f $SCP, ($REPO + '/deploy/aws/setup-instance.sh'), $REMOTE)

Write-Host '== 7. Run setup on instance (deps install takes a few minutes) =='
Invoke-Expression ("$SSH 'sudo bash /home/ubuntu/setup-instance.sh /home/ubuntu/proxy.tar.gz /home/ubuntu/.env'")

Write-Host '== 8. Verify =='
Start-Sleep 10
try {
  $h = Invoke-WebRequest -Uri ('http://{0}:8080/_stcore/health' -f $PUBLIC_IP) -UseBasicParsing -TimeoutSec 30
  Write-Host ("HEALTH: " + $h.StatusCode)
} catch { Write-Host ("HEALTH: FAILED - " + $_.Exception.Message) }
Write-Host ('Dashboard : http://{0}:8080' -f $PUBLIC_IP)
Write-Host ('SSH       : ssh -i {0} ubuntu@{1}' -f $SSHKEY, $PUBLIC_IP)
