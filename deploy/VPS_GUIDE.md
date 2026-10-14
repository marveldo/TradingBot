# VPS guide

How the bot runs on the Oracle Cloud VPS, how to check on it, and how the server was set up from scratch.

Replace `<VPS_IP>` with the VPS's public IP (Oracle console → Compute → Instances → the instance). It is left out of
this file because the repo is public.

## What runs where

```
Oracle VM (Ubuntu 26.04, VM.Standard.E2.1.Micro, London, Always Free)
├── xvfb.service       invisible screen :99, so MT5 runs without a desktop
├── mt5.service        MT5 terminal under Wine 10, logged in to the account (saved password)
├── mt5server.service  mt5server.exe under Wine, the bridge between the bot and MT5 (port 18812, localhost only)
└── smc-bot.service    python management.py tradeprod $BOT_ARGS (options in deploy/bot.env)
```

All four start at boot in that order and restart themselves if they crash. Nobody needs to be logged in.

---

## Connecting

### SSH (everyday)
From PowerShell on the laptop:
```powershell
ssh -i C:\Users\utibe\.ssh\ssh-key-2026-10-13.key ubuntu@<VPS_IP>
```

### SSH with the Remote Desktop tunnel
Same, plus a tunnel for Remote Desktop. Keep this window open while using Remote Desktop:
```powershell
ssh -i C:\Users\utibe\.ssh\ssh-key-2026-10-13.key -L 3389:localhost:3389 ubuntu@<VPS_IP>
```

### Remote Desktop
1. Make sure the desktop service is on (it may have been disabled to save RAM). In SSH:
   ```bash
   sudo systemctl enable --now xrdp
   ```
2. Open the tunnel above.
3. On Windows: Start → **Remote Desktop Connection** → Computer: `localhost` → Connect.
4. Log in as `ubuntu` with the desktop password (set with `sudo passwd ubuntu`). Pick **Xorg** if asked.
5. For a faster connection: Show Options → Display → **High Color (16 bit)**, Experience → **Low-speed broadband**.
6. When done, close the window with the X (do not log out). Optionally turn the desktop off again:
   ```bash
   sudo systemctl disable --now xrdp
   ```

---

## Seeing all the logs

The bot runs in the background under systemd, so it has no terminal window. Everything it prints goes to two places:
systemd's journal (`journalctl`) and the per-symbol files in `~/smc-bot/logs/`.

Follow everything the bot prints, live (Ctrl+C stops watching, the bot keeps running):
```bash
journalctl -u smc-bot -f
```

Follow all six symbols' log files at once:
```bash
tail -f ~/smc-bot/logs/live_*.log
```

Follow the bot, MT5, the bridge and the screen together (useful when something won't start):
```bash
journalctl -u xvfb -u mt5 -u mt5server -u smc-bot -f
```

Everything the bot printed since the last boot, or in a time window:
```bash
journalctl -u smc-bot -b --no-pager
journalctl -u smc-bot --since "1 hour ago" --no-pager
journalctl -u smc-bot --since "2026-10-13 21:00" --until "2026-10-13 22:00" --no-pager
```

Scroll through the whole history (arrow keys / PgUp / PgDn, `/` to search, `q` to quit):
```bash
journalctl -u smc-bot
less ~/smc-bot/logs/live_EURUSD.log
```

The last 50 lines only:
```bash
journalctl -u smc-bot -n 50 --no-pager
```

---

## Is the bot running?

```bash
systemctl status xvfb mt5 mt5server smc-bot --no-pager | grep -E "●|Active"
```
All four should say **active (running)**.

Watch the bot live (Ctrl+C stops watching, the bot keeps running):
```bash
journalctl -u smc-bot -f
```

Per-symbol logs:
```bash
tail -f ~/smc-bot/logs/live_EURUSD.log
tail -n 20 ~/smc-bot/logs/live_*.log
```

What healthy looks like: one heartbeat per symbol a few seconds after every M15 close:
```
EURUSD M15 2026-10-13 21:00 closed at 1.11991 | bias None | 1 new bar(s) | 0 order(s) waiting
```
Gold (XAUUSD) has a daily break and the weekend has no forex candles, so missing heartbeats then are normal.

Can the bot place trades? MT5's **Algo Trading** button must be on, or every order is rejected (retcode 10027):
```bash
cd ~/smc-bot && source .venv/bin/activate
python - <<'PY'
from core.mt5_client import mt5, get_mt5_client
from core.config import settings
get_mt5_client(settings.ACCOUNT_SERVER)
print("terminal algo trading:", mt5.terminal_info().trade_allowed,
      "| account allowed:", mt5.account_info().trade_allowed, mt5.account_info().trade_expert)
PY
```
All three should print `True`. If the first is `False`, turn Algo Trading on (see **Using MT5 by hand**).

Orders the bot placed or had rejected:
```bash
grep -hE "placed|rejected" ~/smc-bot/logs/live_*.log | tail
```

Setups, orders and errors only:
```bash
grep -hE "SETUP|STILL OPEN|ADOPTED|placed|cancelled|error|failed" ~/smc-bot/logs/live_*.log | tail -30
journalctl -u smc-bot -b --no-pager | grep -iE "ready|error|failed|Connected"
```

Machine health:
```bash
free -h        # "available" should stay above ~150 MB
df -h /        # disk Use% under ~80%
uptime         # load average under ~1.0 once warmed up
```

Memory per service (note the numbers, compare a week later):
```bash
systemctl status smc-bot mt5 mt5server --no-pager | grep -E "●|Memory"
```
The bot grows slowly by design: each symbol's state machine keeps every order block, break and swing it has seen,
about 6.5 MB per symbol per year of candles (~40 MB a year for six symbols). A restart drops back to the warm-up size.
If `mt5` or `mt5server` keeps climbing week after week, restart them on a weekend:
`sudo systemctl restart mt5` (restarts the bridge and the bot with it).

Errors from the other services:
```bash
journalctl -u mt5server -n 30 --no-pager
journalctl -u mt5 -n 30 --no-pager
journalctl -u xvfb -n 30 --no-pager
```

---

## Everyday operations

| Task | Command |
|---|---|
| Stop the bot | `sudo systemctl stop smc-bot` |
| Start the bot (starts MT5 and the bridge too) | `sudo systemctl start smc-bot` |
| Restart the bot | `sudo systemctl restart smc-bot` |
| Restart the bridge (and the bot with it) | `sudo systemctl restart mt5server` |
| Stop everything | `sudo systemctl stop smc-bot mt5server mt5 xvfb` |
| Reboot the VPS | `sudo reboot` (everything comes back by itself in ~3–10 min) |

### Change the bot's options
Edit `deploy/bot.env` (for example `BOT_ARGS=--trade --risk 0.5 --symbol EURUSD --symbol USDCAD`, or drop `--trade`
for a dry run), then:
```bash
sudo systemctl restart smc-bot
```

### Update the code
After pushing from the laptop:
```bash
cd ~/smc-bot && git pull
source .venv/bin/activate && pip install -r requirements.txt
sudo systemctl restart smc-bot
```
If a file in `deploy/` changed, reinstall the services first:
```bash
bash ~/smc-bot/deploy/install.sh
sudo systemctl daemon-reload
sudo systemctl restart smc-bot
```

### Switch account (demo → prop)
Check the prop firm allows a UK VPS, then on the VPS edit `.env` (`ACCOUNT_LOGIN_ID`, `ACCOUNT_PASSWORD`,
`ACCOUNT_SERVER`) and log MT5 in to the new account once through Remote Desktop (see below) with **Save password**.
Do not also log in to that account from the phone or laptop (second IP).

---

## Using MT5 by hand through Remote Desktop

The services run MT5 on the invisible screen, so Remote Desktop does not show it. To look at MT5 or change a setting,
stop the services, run MT5 in the desktop, then hand it back.

1. In SSH, stop the services:
   ```bash
   sudo systemctl stop smc-bot mt5server mt5
   ```
2. Connect with Remote Desktop (see above) and open **Applications → Terminal Emulator**.
3. Start MT5:
   ```bash
   wine "$HOME/.wine/drive_c/Program Files/MetaTrader 5/terminal64.exe" &
   ```
4. Optional, to run the bot by hand: start the bridge in a tmux window and detach with **Ctrl+B then D**:
   ```bash
   tmux new -s mt5server
   wine ~/mt5server.exe
   ```
   Check it is listening, then run the bot:
   ```bash
   ss -ltnp | grep 18812
   cd ~/smc-bot && source .venv/bin/activate
   python -c "from core.mt5_client import get_mt5_client; from core.config import settings; c = get_mt5_client(settings.ACCOUNT_SERVER); print(c.account_balance(), c.symbol_info('EURUSD').digits)"
   python management.py tradeprod --symbol EURUSD
   ```
5. When done: Ctrl+C the bot, close MT5 (**File → Exit**), stop the bridge and Wine, then start the services again:
   ```bash
   pkill -f "management.py tradeprod"; pkill -f mt5server.exe; pkill -f terminal64.exe
   tmux kill-server; pkill -f wineserver
   sudo systemctl start smc-bot
   ```

Never run the services and a hand-started MT5 at the same time: two terminals on one Wine folder fight each other.

---

## Full setup from scratch

### 1. Oracle VM
- Region: **UK South (London)** (close to the broker servers; the home region can never be changed).
- Network: Networking → Virtual cloud networks → **Start VCN Wizard → Create VCN with Internet Connectivity**.
- Compute → Instances → Create instance:
  - Image: **Canonical Ubuntu 26.04** (not aarch64, not Minimal). The free micro shape was not offered with 22.04/24.04.
  - Shape: Specialty and previous generation → **VM.Standard.E2.1.Micro** (Always Free).
  - Networking: existing VCN, **public subnet**, **Automatically assign public IPv4** on.
  - SSH keys: Generate a key pair → download both keys to `C:\Users\utibe\.ssh\` (not OneDrive).
  - Advanced, security, storage: defaults.
- Lock the key down on the laptop:
  ```powershell
  icacls C:\Users\utibe\.ssh\ssh-key-2026-10-13.key /inheritance:r /grant:r "$($env:USERNAME):R"
  ```
- Billing → Budgets: a **$1** alert.

### 2. Swap and updates
```bash
sudo fallocate -l 4G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
sudo apt update && sudo apt upgrade -y
sudo systemctl disable --now fwupd fwupd-refresh.timer
sudo reboot
```

### 3. Desktop (only needed to install and log in to MT5)
```bash
sudo apt install -y --no-install-recommends xfce4 xfce4-terminal dbus-x11 xrdp xorgxrdp tmux git python3-venv
echo xfce4-session > ~/.xsession
sudo systemctl enable --now xrdp
sudo passwd ubuntu
```

### 4. Wine 10
Use Ubuntu's own Wine 10. WineHQ's Wine 11 makes the MT5 installer fail with "A debugger has been found running in
your system".
```bash
sudo dpkg --add-architecture i386
sudo apt update
sudo apt install -y wine wine64 wine32:i386
wine --version
```
In the Remote Desktop terminal: `winecfg` → install Mono/Gecko if asked → Windows Version **Windows 10** → OK.

### 5. MT5 (Remote Desktop terminal)
```bash
mkdir -p ~/Downloads && cd ~/Downloads
wget https://download.mql5.com/cdn/web/metaquotes.software.corp/mt5/mt5setup.exe
wine mt5setup.exe
```
The first start compiles MQL5 samples for 5–15 minutes and the screen may look frozen; wait it out. Then:
- File → Login to Trade Account → login, password, server `Deriv-Demo` (type it) → **Save password**.
- Tools → Options → Charts → **Max bars in chart: 50000**.
- Close all charts. Market Watch → Hide all, then add the six symbols.
- Tools → Options → Server → untick **Enable news**.
- Click **Algo Trading** in the toolbar so it turns green. A new install starts with it off, and then every order
  the bot sends is rejected.
- File → Exit.

### 6. The bridge
```bash
cd ~ && wget https://github.com/lucas-campagna/mt5linux/releases/download/server-1.1.1/mt5server.exe
```
(The `mt5linux` Python package is not used: its newest client only works with Docker. `core/mt5_remote.py` talks to
`mt5server.exe` directly.)

### 7. The bot
```bash
cd ~ && git clone https://github.com/<your-username>/<repo-name>.git smc-bot
cd smc-bot
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
mkdir -p logs
nano .env
```
`.env` holds `ACCOUNT_LOGIN_ID`, `ACCOUNT_PASSWORD`, `ACCOUNT_SERVER`, `ACCOUNT_BROKER`. It never goes in git.

### 8. Services
```bash
bash ~/smc-bot/deploy/install.sh
sudo systemctl start smc-bot
sudo reboot
```
After the reboot, check with the commands in **Is the bot running?**. Then free RAM:
```bash
sudo systemctl disable --now xrdp
```

### 9. Keep the free VM from looking idle
Oracle may reclaim an Always Free VM if, over 7 days, its CPU 95th percentile stays under 20% (and network under 20%).
The bot sits at ~4–8% CPU, so a cron job keeps the CPU busy for 6 minutes every hour (10% of the time), at the lowest
priority so the bot and MT5 always come first, starting at :02 to stay clear of the M15 closes.

When it runs: every hour from **:02 to :08**, 24 times a day (00:02–00:08, 01:02–01:08, … 23:02–23:08). The VPS
clock is UTC; Nigeria is UTC+1, so the hour differs by one but the minutes are the same.

Install it once (running it twice adds a duplicate line):
```bash
(crontab -l 2>/dev/null; echo "2 * * * * nice -n 19 timeout 360 sh -c 'while :; do :; done'") | crontab -
crontab -l
```
Check it runs (after :02 past the hour, and over a day):
```bash
journalctl -u cron --since "1 hour ago" --no-pager | grep CMD
journalctl -u cron --since "24 hours ago" --no-pager | grep -c CMD
```
After a week: Oracle console → the instance → Metrics → CPU Utilization, Statistic **P95**, 7 days: should be above 20%.

Remove it after upgrading to Pay As You Go (that account type is not reclaimed): `crontab -r`.

---

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `Connection refused` once in `journalctl -u smc-bot` after boot | The bridge was still starting; systemd retries the bot after 30 s. Only a problem if it repeats. |
| `Connection refused` over and over | `sudo systemctl restart mt5server`, then `ss -ltnp \| grep 18812` should show a line. |
| `history not ready ... retrying` | MT5 is still downloading history; the warm-up retries 5 times. |
| `check failed: ...` on every check | The bridge restarted under the bot: `sudo systemctl restart mt5server`. |
| `TimeoutError: result expired` | MT5 too slow to answer (first history download); the client waits 5 minutes. Run again. |
| `terminal algo trading: False`, or `Order rejected` with retcode 10027 | Algo Trading is off: stop the services, open MT5 by hand, click **Algo Trading** (green), **File → Exit**, start the services. |
| Remote Desktop "could not be authenticated" | Reset with `sudo passwd ubuntu`. |
| Remote Desktop "X server could not be started" | `sudo apt install -y xorgxrdp && sudo systemctl restart xrdp`. |
| Remote Desktop black screen | The VM is busy (MT5 first start); wait, or `sudo systemctl restart xrdp` and reconnect. |
| `wineserver: command not found` | Use `pkill -f wineserver`. |
| Bot running in the background with no terminal | `pkill -f "management.py tradeprod"` (or `sudo systemctl stop smc-bot` for the service). |
| `ssh: Could not resolve hostname <...>` | Leave out the `< >` around the IP. |
| Clone fails with `Permission denied (publickey)` | Clone with the `https://` address, not `git@github.com:`. |
