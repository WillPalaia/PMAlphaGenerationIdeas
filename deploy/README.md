# Oracle paper collector deployment

This deploys only the public-data collector. It does not use Kalshi
credentials and does not place orders.

## Recommended Oracle configuration

- Ubuntu 24.04 LTS
- Ampere A1 Flex: 1 OCPU and 6 GB RAM is sufficient for the collector
- 50 GB boot volume
- One public IPv4 address
- SSH key authentication; do not enable password login
- Ingress security-list rule: TCP 22 from your own IP only
- No HTTP/HTTPS ingress is required for this service

If Ampere capacity is unavailable, use an AMD micro instance with 1 GB RAM.
The collector is lightweight; do not overprovision.

## Install

SSH into the instance, then run:

```bash
sudo apt-get update
sudo apt-get install -y git
git clone https://github.com/WillPalaia/PMAlphaGenerationIdeas.git
cd PMAlphaGenerationIdeas
sudo bash deploy/install_oracle_paper.sh
```

Edit the service and replace `REPLACE_WITH_TICKER` with one or more active
Kalshi tickers. The service will not start successfully with the placeholder:

```bash
sudo nano /etc/systemd/system/pm-alpha-paper.service
sudo systemctl daemon-reload
sudo systemctl enable --now pm-alpha-paper
sudo systemctl status pm-alpha-paper --no-pager
sudo journalctl -u pm-alpha-paper -f
```

The SQLite data is stored at `/var/lib/pm-alpha/market_data.sqlite`. The
service restarts after a process or host reboot. The machine keeps running
when your laptop is turned off or disconnected.

## Verify capture

```bash
sudo systemctl is-active pm-alpha-paper
sudo ls -lh /var/lib/pm-alpha/market_data.sqlite
sudo journalctl -u pm-alpha-paper --since "10 minutes ago" --no-pager
```

## Stop or update

```bash
sudo systemctl disable --now pm-alpha-paper
cd /opt/pm-alpha
sudo git pull --ff-only
sudo /opt/pm-alpha/.venv/bin/pip install -e /opt/pm-alpha
sudo systemctl enable --now pm-alpha-paper
```

Do not copy private keys to this instance for the paper collector. Live
execution is intentionally not part of this service.
