"""Wallet discovery from the live trade stream.

The leaderboard misses wallets, so this listens to BTC/ETH/SOL/HYPE trades and adds
any address that trades at least DISCOVER_NOTIONAL in a UTC day to the wallets table.
ingest.py polls new wallets first. Runs forever; reconnects on errors.

  python collector.py
"""
import asyncio, json, sqlite3, time

import websockets

from ingest import COINS, DB

WS = "wss://api.hyperliquid.xyz/ws"
DISCOVER_NOTIONAL = 50_000       # USD traded per wallet per UTC day


def open_db():
    db = sqlite3.connect(DB, timeout=1)  # never block the trade stream on a busy database
    known = {r[0] for r in db.execute("SELECT address FROM wallets")}
    return db, known


async def run(db, known, stats, pending):
    day, traded = time.gmtime().tm_yday, {}
    async with websockets.connect(WS, ping_interval=None, max_size=2**22) as ws:
        for c in COINS:
            await ws.send(json.dumps({"method": "subscribe", "subscription": {"type": "trades", "coin": c}}))
        last_ping = time.time()
        while True:
            if time.time() - last_ping > 30:           # HL drops idle connections after 60s
                await ws.send(json.dumps({"method": "ping"}))
                last_ping = time.time()
            try:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=30))
            except asyncio.TimeoutError:
                continue
            if msg.get("channel") != "trades":
                continue
            if time.gmtime().tm_yday != day:
                day, traded = time.gmtime().tm_yday, {}
            now = int(time.time() * 1000)
            for t in msg["data"]:
                v = float(t["px"]) * float(t["sz"])
                stats["trades"] += 1
                for addr in t.get("users", []):
                    if addr in known:
                        continue
                    traded[addr] = traded.get(addr, 0.0) + v
                    if traded[addr] >= DISCOVER_NOTIONAL:
                        pending.append((addr, now))
                        known.add(addr)
            if pending:
                # account_value 0 until the first poll fills in the real value. If ingest holds
                # the write lock, keep the batch and try again on the next message.
                try:
                    db.executemany("INSERT OR IGNORE INTO wallets(address, account_value, first_seen, source) "
                                   "VALUES(?, 0, ?, 'trades')", pending)
                    db.commit()
                    stats["new"] += len(pending)
                    pending.clear()
                except sqlite3.OperationalError:
                    db.rollback()


async def main():
    db, known = open_db()
    stats, last_log, backoff, pending = {"trades": 0, "new": 0}, time.time(), 5, []
    print(f"collector start: {len(known)} known wallets", flush=True)
    while True:
        task, started = asyncio.create_task(run(db, known, stats, pending)), time.time()
        try:
            while not task.done():
                await asyncio.sleep(5)
                if time.time() - last_log > 3600:
                    print(f"last hour: {stats['trades']} trades, {stats['new']} new wallets", flush=True)
                    stats.update(trades=0, new=0)
                    last_log = time.time()
            task.result()
        except Exception as e:
            if time.time() - started > 300:     # it ran fine for a while: start backoff over
                backoff = 5
            print(f"disconnected: {e!r}; retry in {backoff}s", flush=True)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 300)


if __name__ == "__main__":
    asyncio.run(main())
