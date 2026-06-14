#!/bin/bash
set -euo pipefail

ROOT="/root/Visio_Gemini"
cd "$ROOT"

cp deploy/visio-gemini-btc-scan.service /etc/systemd/system/
cp deploy/visio-gemini-btc-scan.timer /etc/systemd/system/

systemctl daemon-reload
systemctl enable --now visio-gemini-btc-scan.timer
systemctl list-timers visio-gemini-btc-scan.timer
