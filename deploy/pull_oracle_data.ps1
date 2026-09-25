param(
    [Parameter(Mandatory = $true)]
    [string]$SshKey,
    [Parameter(Mandatory = $true)]
    [string]$OracleHost,
    [string]$RemoteUser = "ubuntu",
    [string]$Output = "data\oracle_market_data.sqlite",
    [switch]$RunAnalysis
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$outputPath = Join-Path $repo $Output
$outputDirectory = Split-Path -Parent $outputPath
New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null

$remoteCopy = "/home/$RemoteUser/market_data_sync.sqlite"
$sshTarget = "$RemoteUser@$OracleHost"

Write-Host "Creating a consistent SQLite copy on the Oracle VM..."
ssh -i $SshKey -o BatchMode=yes $sshTarget `
    "sudo systemctl stop pm-alpha-paper && sudo cp /var/lib/pm-alpha/market_data.sqlite $remoteCopy && sudo chown $RemoteUser`:$RemoteUser $remoteCopy && sudo systemctl start pm-alpha-paper"
if ($LASTEXITCODE -ne 0) {
    throw "Oracle snapshot failed. The service may need to be checked manually."
}

Write-Host "Downloading $remoteCopy..."
scp -i $SshKey -o BatchMode=yes "$sshTarget`:$remoteCopy" $outputPath
if ($LASTEXITCODE -ne 0) {
    throw "Database download failed."
}

ssh -i $SshKey -o BatchMode=yes $sshTarget "rm -f $remoteCopy"
if ($LASTEXITCODE -ne 0) {
    Write-Warning "Downloaded successfully, but the temporary remote copy could not be removed."
}

Write-Host "Saved database to $outputPath"
if ($RunAnalysis) {
    Push-Location $repo
    try {
        python inspect_market_data.py $Output
        if ($LASTEXITCODE -ne 0) { throw "Market-data inspection failed." }
        $sweepOutput = [System.IO.Path]::ChangeExtension($Output, ".sweep.csv")
        python run_sweep.py --sqlite $Output --output $sweepOutput
        if ($LASTEXITCODE -ne 0) { throw "Strategy sweep failed." }
        Write-Host "Sweep saved to $sweepOutput"
    }
    finally {
        Pop-Location
    }
}
