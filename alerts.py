"""Research alerts after each ingest run, pushed to a phone through ntfy.sh:

- the unhedged short share of $1M+ accounts moves SHIFT_PP or more within an hour
- a $1M+ account adds or cuts BIG_MOVE or more in one coin between two snapshots

Sends to NTFY_RESEARCH_TOPIC (without it, only prints). Runs after ingest (ExecStartPost); each snapshot is checked once. Every crossing also goes
into the signals table for event_study.py.
  python alerts.py
"""
import json, os, sqlite3
from pathlib import Path

import requests

from ingest import DB, TIER_A

SHIFT_PP = 3.0
BIG_MOVE = 10_000_000
MAX_LISTED = 5
GROUP_CHANGE = 0.10              # skip shifts when the $1M+ group grew or shrank more than this
COOLDOWN = 2 * 3_600_000
STATE = Path(__file__).with_name(".alerts_state")
LINK = "https://hypurrscan.io/address/"


def usd(x):
    return f"${x / 1e6:.0f}M" if abs(x) >= 1e6 else f"${x / 1e3:.0f}k"


def shifts(db, now, sent, log):
    """sent: {coin: [ts, share]} of earlier shift alerts; updated in place.
    log: gets (coin, kind, value, address, sent) for every crossing, for the event study."""
    hour_ago = db.execute("SELECT MAX(ts) FROM series_a WHERE ts <= ?", (now - 3_600_000 + 120_000,)).fetchone()[0]
    if not hour_ago:
        return []
    q = "SELECT coin, dir_short_ntl / (dir_long_ntl + dir_short_ntl), n_wallets FROM series_a WHERE ts = ?"
    then = {c: (v, n) for c, v, n in db.execute(q, (hour_ago,))}
    out = []
    for coin, v, n in db.execute(q, (now,)):
        if coin not in then or abs(v - then[coin][0]) * 100 < SHIFT_PP:
            continue
        # A jump in group size means new wallets came in (discovery), not that anyone traded.
        if abs(n - then[coin][1]) > GROUP_CHANGE * then[coin][1]:
            continue
        # One move shows up in four hourly windows; alert again only if it kept going.
        last = sent.get(coin)
        fresh = not (last and now - last[0] < COOLDOWN and abs(v - last[1]) * 100 < SHIFT_PP)
        log.append((coin, "shift", (v - then[coin][0]) * 100, None, int(fresh)))
        if not fresh:
            continue
        sent[coin] = [now, v]
        out.append(f"{coin}: $1M+ accounts went from {then[coin][0]:.0%} to {v:.0%} short (unhedged) "
                   f"in the last hour.")
    return out


def big_moves(db, now, log):
    prev = db.execute("SELECT MAX(ts) FROM runs WHERE ts < ?", (now,)).fetchone()[0]
    if not prev:
        return []
    rows = db.execute("""
        WITH cur AS (SELECT address, coin, SIGN(szi) * position_value v, label, leverage FROM classified
                     WHERE ts = :now AND acct_value >= :tier),
             old AS (SELECT address, coin, SIGN(szi) * position_value v FROM positions WHERE ts = :prev)
        SELECT a.address, a.coin, COALESCE(cur.v, 0) - COALESCE(old.v, 0) d, cur.v, cur.label, cur.leverage
        FROM (SELECT address, coin FROM cur UNION SELECT address, coin FROM old) a
        LEFT JOIN cur USING(address, coin) LEFT JOIN old USING(address, coin)
        JOIN wallets w ON w.address = a.address
        -- only wallets polled in both runs: a book we didn't read is not a closed or a new one
        WHERE w.last_checked = :now AND w.prev_checked = :prev AND w.account_value >= :tier
        ORDER BY ABS(d) DESC""", {"now": now, "prev": prev, "tier": TIER_A}).fetchall()
    out = []
    for addr, coin, d, v, label, lev in rows:
        if abs(d) < BIG_MOVE:
            break
        log.append((coin, "big_trade", d, addr, 1))
        side = "short" if d < 0 else "long"
        what = f"added {usd(abs(d))} {side}" if (v or 0) * d > 0 else f"cut {usd(abs(d))} of a position"
        now_txt = f"now {usd(abs(v))} {'short' if v < 0 else 'long'}, {label}, {lev:.0f}x" if v else "now flat"
        out.append(f"{coin}: {addr[:8]}… {what} ({now_txt}). {LINK}{addr}")
    return out[:MAX_LISTED] + ([f"…and {len(out) - MAX_LISTED} more"] if len(out) > MAX_LISTED else [])


def main():
    topic = os.environ.get("NTFY_RESEARCH_TOPIC")     # unset: print only
    db = sqlite3.connect(DB, timeout=30)
    now = db.execute("SELECT MAX(ts) FROM runs").fetchone()[0]
    state = json.loads(STATE.read_text()) if STATE.exists() else {"ts": 0, "sent": {}}
    if not now or state["ts"] == now:
        return
    log = []
    for title, lines in (("Positioning shift", shifts(db, now, state["sent"], log)),
                         ("Large whale trades", big_moves(db, now, log))):
        if lines and topic:
            requests.post(f"https://ntfy.sh/{topic}", data="\n\n".join(lines).encode(),
                          headers={"Title": title, "Tags": "whale"}, timeout=10)
        if lines:
            print(title, *lines, sep="\n  ")
    px = dict(db.execute("SELECT coin, mark_px FROM market_snapshots WHERE ts = ?", (now,)).fetchall())
    db.executemany("INSERT INTO signals VALUES(?,?,?,?,?,?,?)",
                   [(now, coin, kind, value, addr, px.get(coin), sent) for coin, kind, value, addr, sent in log])
    db.commit()
    state["ts"] = now
    STATE.write_text(json.dumps(state))


if __name__ == "__main__":
    main()
