#!/bin/sh
# Pull the latest main on the VPS and restart the long-running services.
# The ingest timer picks up new code on its next run and migrates the schema itself.
#   ssh root@<vps> /home/hlwhales/hl_whales/deploy.sh
set -e
cd /home/hlwhales/hl_whales
before=$(sudo -u hlwhales git rev-parse HEAD)
sudo -u hlwhales git pull --ff-only -q
if ! sudo -u hlwhales git diff --quiet "$before" HEAD -- requirements.txt; then
  sudo -u hlwhales .venv/bin/pip install -q -r requirements.txt
fi
systemctl restart hl-whales-collector hl-whales-dashboard
echo "deployed $(sudo -u hlwhales git log --oneline -1)"
