param(
    [string]$SshKey = "C:\Users\Will Palaia\Downloads\oracle cloud\Prediction Market Alpha Generation Ideas.key",
    [string]$OracleHost = "157.151.132.129",
    [string]$RemoteUser = "ubuntu",
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$sshTarget = "$RemoteUser@$OracleHost"

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "         DEPLOYING PM-ALPHA UPDATE TO ORACLE VM            " -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan

# Step 1: Pre-flight unit tests
if (-not $SkipTests) {
    Write-Host "`n[1/5] Running local unit tests (pytest)..." -ForegroundColor Yellow
    Push-Location $repo
    try {
        python -m pytest
        if ($LASTEXITCODE -ne 0) {
            throw "Unit tests failed! Deployment aborted to prevent breaking remote paper trading."
        }
    }
    finally {
        Pop-Location
    }
    Write-Host "All tests passed successfully!" -ForegroundColor Green
} else {
    Write-Host "`n[1/5] Skipping tests as requested." -ForegroundColor Yellow
}

# Step 2: Package repo
Write-Host "`n[2/5] Creating clean deployment archive from current workspace..." -ForegroundColor Yellow
$tempArchive = Join-Path ([System.IO.Path]::GetTempPath()) "pm_alpha_deploy.tar.gz"
if (Test-Path $tempArchive) { Remove-Item $tempArchive -Force }

Push-Location $repo
try {
    tar --exclude="data" --exclude=".venv" --exclude="__pycache__" --exclude=".pytest_cache" --exclude=".git" -czf $tempArchive *
}
finally {
    Pop-Location
}
Write-Host "Archive created: $((Get-Item $tempArchive).Length / 1KB) KB" -ForegroundColor Green

# Step 3: Upload to Oracle VM
Write-Host "`n[3/5] Uploading package to Oracle VM ($sshTarget)..." -ForegroundColor Yellow
scp -i $SshKey -o BatchMode=yes $tempArchive "$sshTarget`:/tmp/pm_alpha_deploy.tar.gz"
if ($LASTEXITCODE -ne 0) {
    throw "SCP upload to Oracle VM failed."
}
Remove-Item $tempArchive -Force

# Step 4: Extract and Install on Oracle VM
Write-Host "`n[4/5] Extracting code and installing on Oracle VM..." -ForegroundColor Yellow
$remoteCommands = @'
set -e
sudo tar -xzf /tmp/pm_alpha_deploy.tar.gz -C /opt/pm-alpha
rm -f /tmp/pm_alpha_deploy.tar.gz
sudo chown -R pmalpha:pmalpha /opt/pm-alpha
sudo cp /opt/pm-alpha/deploy/pm-alpha-paper.service /etc/systemd/system/pm-alpha-paper.service
sudo systemctl daemon-reload
sudo /opt/pm-alpha/.venv/bin/pip install -e /opt/pm-alpha --no-deps
sudo systemctl restart pm-alpha-paper
for i in $(seq 1 10); do
    if sudo systemctl is-active --quiet pm-alpha-paper; then
        break
    fi
    sleep 1
done
sudo systemctl is-active pm-alpha-paper
'@

$b64 = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($remoteCommands))
ssh -i $SshKey -o BatchMode=yes $sshTarget "echo $b64 | base64 -d | bash"
if ($LASTEXITCODE -ne 0) {
    throw "Remote deployment command failed."
}

# Step 5: Verify status and logs
Write-Host "`n[5/5] Deployment complete! Checking service logs..." -ForegroundColor Yellow
ssh -i $SshKey -o BatchMode=yes $sshTarget "sudo journalctl -u pm-alpha-paper -n 12 --no-pager"

Write-Host "`n============================================================" -ForegroundColor Cyan
Write-Host "Service pm-alpha-paper is active and running on Oracle VM!" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Cyan
