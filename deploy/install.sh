#!/bin/bash
set -e
cd "$(dirname "$0")"
sudo apt install -y xvfb
sudo cp xvfb.service mt5.service mt5server.service smc-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable xvfb mt5 mt5server smc-bot
echo "Installed. Start everything with: sudo systemctl start smc-bot"
