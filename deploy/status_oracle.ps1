param(
    [string]$SshKey = "C:\Users\Will Palaia\Downloads\oracle cloud\Prediction Market Alpha Generation Ideas.key",
    [string]$OracleHost = "157.151.132.129",
    [string]$RemoteUser = "ubuntu"
)

$ErrorActionPreference = "Stop"
$sshTarget = "$RemoteUser@$OracleHost"

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "         ORACLE VM PAPER TRADING STATUS CHECK              " -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "Host: $sshTarget"

# 1. Service Status
Write-Host "`n[1/3] Checking pm-alpha-paper systemd service..." -ForegroundColor Yellow
$serviceStatus = ssh -i $SshKey -o BatchMode=yes $sshTarget "sudo systemctl is-active pm-alpha-paper"
$statusColor = if ($serviceStatus -eq "active") { "Green" } else { "Red" }
Write-Host "Service Status: $serviceStatus" -ForegroundColor $statusColor

# 2. Database Stats
Write-Host "`n[2/3] Checking remote paper database..." -ForegroundColor Yellow
$remoteScript = @'
import sqlite3
c = sqlite3.connect("/var/lib/pm-alpha/market_data.sqlite")
total_snaps = c.execute("SELECT MAX(id) FROM market_snapshots").fetchone()[0] or 0
quoted_snaps = c.execute("SELECT COUNT(1) FROM market_snapshots WHERE yes_bid IS NOT NULL OR yes_ask IS NOT NULL").fetchone()[0]
print(f"Snapshots: {total_snaps:,} total | {quoted_snaps:,} quoted")

eq = c.execute("SELECT cash, positions_value, equity, fees FROM paper_equity ORDER BY id DESC LIMIT 1").fetchone()
if eq:
    print(f"Equity: ${eq[2]:.2f} (Cash: ${eq[0]:.2f}, Positions MTM: ${eq[1]:.2f}, Fees: ${eq[3]:.4f})")
else:
    print("Equity: None recorded yet")

cols = [c[1] for c in c.execute("PRAGMA table_info(paper_orders)").fetchall()]
strat_field = "COALESCE(NULLIF(strategy, ''), signal)" if "strategy" in cols else "signal"
orders = c.execute(f"SELECT {strat_field} as strat, status, COUNT(1) FROM paper_orders GROUP BY strat, status").fetchall()
print("\nOrders:")
if not orders:
    print("  None")
for o in orders:
    print(f"  {o[0]:<25} {o[1]:<10} {o[2]:<5}")

positions = c.execute("SELECT market_id, quantity, average_cost FROM paper_positions WHERE quantity > 0").fetchall()
print(f"\nOpen Positions ({len(positions)}):")
for p in positions:
    print(f"  {p[0]:<45} Qty: {p[1]:<5.1f} AvgCost: ${p[2]:.3f}")
'@

$b64 = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($remoteScript))
ssh -i $SshKey -o BatchMode=yes $sshTarget "echo $b64 | base64 -d | python3"

# 3. Recent Logs
Write-Host "`n[3/3] Recent Journald Logs (last 10 lines):" -ForegroundColor Yellow
ssh -i $SshKey -o BatchMode=yes $sshTarget "sudo journalctl -u pm-alpha-paper -n 10 --no-pager"

Write-Host "`n============================================================" -ForegroundColor Cyan
