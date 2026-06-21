#!/bin/bash
set -euo pipefail

ROOT="/root/Visio_Gemini"
cd "$ROOT"

cp deploy/visio-gemini-btc-regime.service /etc/systemd/system/
cp deploy/visio-gemini-btc-regime.timer /etc/systemd/system/

systemctl daemon-reload
systemctl enable --now visio-gemini-btc-regime.timer
systemctl list-timers visio-gemini-btc-regime.timer
