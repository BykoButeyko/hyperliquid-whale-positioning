"""Push a phone alert through ntfy.sh when the pipeline stops, and another when it recovers.

Run every 10 minutes (systemd timer). Needs NTFY_TOPIC in the environment; subscribe to the
same topic in the ntfy app. Only state changes send a message, so a long outage alerts once.
  python healthcheck.py
"""
import json, os, sqlite3, subprocess, time
from pathlib import Path

import requests

from ingest import DB

STATE = Path(__file__).with_name(".health_state")
MAX_RUN_AGE_MIN = 35             # timer runs every 15 min and a run takes ~13
MAX_ERROR_SHARE = 0.10


def problems():
    found = []
    db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    ts, polled, errors = db.execute("SELECT ts, wallets_polled, errors FROM runs ORDER BY ts DESC LIMIT 1").fetchone()
    age = (time.time() * 1000 - ts) / 60000
    if age > MAX_RUN_AGE_MIN:
        found.append(f"last ingest run {age:.0f} min ago")
    if polled + errors and errors / (polled + errors) > MAX_ERROR_SHARE:
        found.append(f"last run: {errors} errors of {polled + errors} wallets")
    for unit in ("hl-whales-collector", "hl-whales-dashboard"):
        state = subprocess.run(["systemctl", "is-active", unit], capture_output=True, text=True).stdout.strip()
        if state != "active":
            found.append(f"{unit} is {state}")
    try:
        if requests.get("http://127.0.0.1:8510/_stcore/health", timeout=10).text != "ok":
            found.append("dashboard health check failed")
    except requests.RequestException:
        found.append("dashboard not responding")
    return found


def main():
    now = problems()
    before = json.loads(STATE.read_text()) if STATE.exists() else []
    if bool(now) != bool(before):
        title, body = ("hl_whales DOWN", "\n".join(now)) if now else ("hl_whales recovered", "all checks pass")
        requests.post(f"https://ntfy.sh/{os.environ['NTFY_TOPIC']}", data=body.encode(),
                      headers={"Title": title, "Priority": "high" if now else "default"}, timeout=10)
    STATE.write_text(json.dumps(now))
    print(now or "ok")


if __name__ == "__main__":
    main()
