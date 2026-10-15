#!/bin/bash
set -e
cd "$(dirname "$0")"
sudo apt install -y xvfb
# No separate mt5.service: the bridge runs in its own Wine session and starts its own terminal on the bot's first
# mt5.initialize(), so a second terminal only burned CPU. Remove it from older installs.
if [ -f /etc/systemd/system/mt5.service ]; then
    sudo systemctl disable --now mt5 || true
    sudo rm /etc/systemd/system/mt5.service
fi
sudo cp xvfb.service mt5server.service smc-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable xvfb mt5server smc-bot
echo "Installed. Start everything with: sudo systemctl start smc-bot"
