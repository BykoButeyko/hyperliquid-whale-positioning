"""Hyperliquid whale-positioning ingest (BTC, ETH, SOL, HYPE).

One run = one snapshot. Schedule every 15 min.
  python ingest.py            # full run
  python ingest.py 200        # cap wallets polled (testing)

Discovery: HL has no "list all positions" endpoint, so candidate wallets come from
the public leaderboard (stats-data host), refreshed daily. Each run polls
clearinghouseState for tier A (account >= $1M) plus the stalest tier-B wallets.
"""
import sqlite3, sys, threading, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import requests

INFO = "https://api.hyperliquid.xyz/info"
LEADERBOARD = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"
VAULTS = "https://stats-data.hyperliquid.xyz/Mainnet/vaults"
DB = Path(__file__).with_name("hl_whales.db")
COINS = ("BTC", "ETH", "SOL", "HYPE")
SPOT_TOKEN = {"BTC": "UBTC", "ETH": "UETH", "SOL": "USOL", "HYPE": "HYPE"}
# Classification (see README). Turnover distribution has no natural break; 100x ~ top 8%.
MM_TURNOVER = 100                # 30d volume / account value
RELVAL_RATIO = 0.3               # |net| / gross across all coins below this -> relative value
HOLD_PER_RUN = 150               # wallets per run whose spot/staked holdings are refreshed
HOLD_MAX_AGE = 86_400_000        # refresh holdings at most daily
WHALE_NOTIONAL = 50_000          # USD per wallet per coin; applied at query time (views)
STORE_NOTIONAL = 10_000          # stored threshold; lower than WHALE so $50k crossings don't flicker
TIER_A, TIER_B = 1_000_000, 50_000
MAX_WALLETS = 4000               # per run, ~12 min at the rate below
REQ_PER_MIN = 330                # x weight 2 = 660/1200 -> leaves headroom for my other bots on the same IP

TABLES = """
CREATE TABLE IF NOT EXISTS market_snapshots(
  ts INTEGER, coin TEXT, mark_px REAL, oracle_px REAL, funding REAL, premium REAL,
  open_interest REAL, day_ntl_vlm REAL, PRIMARY KEY(ts, coin));
CREATE TABLE IF NOT EXISTS wallets(
  address TEXT PRIMARY KEY, account_value REAL, first_seen INTEGER, last_checked INTEGER,
  last_error INTEGER, gross_ntl REAL, net_ntl REAL, turnover REAL, is_vault INTEGER,
  holdings_checked INTEGER, source TEXT);
-- Spot + staked balance (coin units) per wallet, latest only; used to net out on-HL hedges.
CREATE TABLE IF NOT EXISTS holdings(
  address TEXT, coin TEXT, amount REAL, PRIMARY KEY(address, coin));
CREATE TABLE IF NOT EXISTS positions(
  ts INTEGER, address TEXT, coin TEXT, szi REAL, entry_px REAL, position_value REAL,
  leverage REAL, lev_type TEXT, liq_px REAL, unrealized_pnl REAL, margin_used REAL,
  acct_value REAL, PRIMARY KEY(ts, address, coin));
CREATE TABLE IF NOT EXISTS runs(
  ts INTEGER PRIMARY KEY, wallets_polled INTEGER, errors INTEGER, duration_s REAL);
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
"""

# Classification rules, shared by the classified view and the per-run stamp (aliases: p, w).
LABEL_SQL = f"""CASE WHEN w.is_vault = 1 THEN 'vault'
         WHEN w.turnover >= {MM_TURNOVER} THEN 'market_maker'
         WHEN w.gross_ntl > 0 AND ABS(w.net_ntl) / w.gross_ntl < {RELVAL_RATIO} THEN 'relative_value'
         ELSE 'directional' END"""
# Part of a short covered by spot/staked holdings of the same coin (longs: 0).
HEDGE_SQL = """CASE WHEN p.szi < 0 THEN MIN(COALESCE((SELECT amount FROM holdings h
           WHERE h.address = p.address AND h.coin = p.coin), 0), -p.szi) * p.position_value / -p.szi
         ELSE 0 END"""

VIEWS = f"""
DROP VIEW IF EXISTS latest_positions;
DROP VIEW IF EXISTS series_a;
DROP VIEW IF EXISTS breadth_b;
DROP VIEW IF EXISTS coverage;
DROP VIEW IF EXISTS classified;
DROP VIEW IF EXISTS latest_classified;
DROP VIEW IF EXISTS breakdown;

-- A wallet's positions as of its last successful poll (absent = flat or < ${STORE_NOTIONAL}).
-- Wallets whose most recent poll failed are excluded until the next success.
CREATE VIEW latest_positions AS
  SELECT p.* FROM positions p
  JOIN wallets w ON p.address = w.address AND p.ts = w.last_checked
  WHERE w.last_error IS NULL OR w.last_error < w.last_checked;

-- Every position with a wallet label and its on-HL hedged notional.
-- label_pit / hedged_pit are stamped at the end of each run (point in time). Rows from before
-- that existed fall back to the wallet's current profile.
CREATE VIEW classified AS
  SELECT p.*,
    COALESCE(p.label_pit, {LABEL_SQL}) AS label,
    COALESCE(p.hedged_pit, {HEDGE_SQL}) AS hedged_ntl
  FROM positions p
  JOIN wallets w ON w.address = p.address;

CREATE VIEW latest_classified AS
  SELECT c.* FROM classified c
  JOIN wallets w ON c.address = w.address AND c.ts = w.last_checked
  WHERE w.last_error IS NULL OR w.last_error < w.last_checked;

-- Time series: tier A only (polled every run -> consistent 15-min data). Notional in USD.
-- raw = all positions; dir = directional wallets, shorts net of on-HL hedges.
CREATE VIEW series_a AS
  SELECT ts, coin,
    SUM(CASE WHEN szi > 0 THEN position_value ELSE 0 END) AS long_ntl,
    SUM(CASE WHEN szi < 0 THEN position_value ELSE 0 END) AS short_ntl,
    SUM(CASE WHEN szi > 0 AND label = 'directional' THEN position_value ELSE 0 END) AS dir_long_ntl,
    SUM(CASE WHEN szi < 0 AND label = 'directional' THEN position_value - hedged_ntl ELSE 0 END)
      AS dir_short_ntl,
    COUNT(*) AS n_wallets
  FROM classified
  WHERE acct_value >= {TIER_A} AND position_value >= {WHALE_NOTIONAL}
  GROUP BY ts, coin;

-- Who holds the current positions (all tracked wallets): notional by coin, label and side.
CREATE VIEW breakdown AS
  SELECT coin, label, CASE WHEN szi > 0 THEN 'long' ELSE 'short' END AS side,
         SUM(position_value - hedged_ntl) AS ntl, COUNT(*) AS n
  FROM latest_classified WHERE position_value >= {WHALE_NOTIONAL} GROUP BY 1, 2, 3
  UNION ALL
  SELECT coin, 'hedged_on_hl', 'short', SUM(hedged_ntl), COUNT(*)
  FROM latest_classified WHERE position_value >= {WHALE_NOTIONAL} AND hedged_ntl > 0 GROUP BY 1;

-- Breadth: tier B from latest_positions (rotating, up to a few hours old).
CREATE VIEW breadth_b AS
  SELECT coin,
    SUM(CASE WHEN szi > 0 THEN position_value ELSE 0 END) AS long_ntl,
    SUM(CASE WHEN szi < 0 THEN position_value ELSE 0 END) AS short_ntl,
    COUNT(*) AS n_wallets,
    ((SELECT MAX(ts) FROM runs) - MIN(ts)) / 60000.0 AS max_age_min,
    ((SELECT MAX(ts) FROM runs) - SUM(ts * position_value) / SUM(position_value)) / 60000.0
      AS ntl_weighted_age_min
  FROM latest_positions
  WHERE acct_value < {TIER_A} AND position_value >= {WHALE_NOTIONAL}
  GROUP BY coin;

-- Coverage: tracked whale notional (long+short) vs both sides of OI (OI is one-sided), plus data age.
CREATE VIEW coverage AS
  WITH m AS (SELECT * FROM market_snapshots WHERE ts = (SELECT MAX(ts) FROM market_snapshots)),
  t AS (SELECT coin, SUM(position_value) AS tracked_ntl, MIN(ts) AS oldest_ts,
               SUM(ts * position_value) / SUM(position_value) AS wavg_ts
        FROM latest_positions WHERE position_value >= {WHALE_NOTIONAL} GROUP BY coin)
  SELECT m.coin, m.open_interest * m.mark_px AS oi_ntl, t.tracked_ntl,
    t.tracked_ntl / (2 * m.open_interest * m.mark_px) AS coverage,
    (m.ts - t.oldest_ts) / 60000.0 AS max_age_min,
    (m.ts - t.wavg_ts) / 60000.0 AS ntl_weighted_age_min
  FROM m LEFT JOIN t ON t.coin = m.coin;
"""


def init_db(db):
    db.execute("PRAGMA journal_mode=WAL")  # readers (dashboard, backup) don't block the writer
    db.executescript(TABLES)
    # Migrate DBs created before these columns existed.
    for table, col in (("wallets", "last_error INTEGER"), ("positions", "acct_value REAL"),
                       ("wallets", "gross_ntl REAL"), ("wallets", "net_ntl REAL"),
                       ("wallets", "turnover REAL"), ("wallets", "is_vault INTEGER"),
                       ("wallets", "holdings_checked INTEGER"), ("wallets", "source TEXT"),
                       ("positions", "label_pit TEXT"), ("positions", "hedged_pit REAL")):
        if col.split()[0] not in {r[1] for r in db.execute(f"PRAGMA table_info({table})")}:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {col}")
    db.executescript(VIEWS)

session = requests.Session()
_lock, _next = threading.Lock(), [0.0]


def info(body):
    """POST /info with a global pacing limiter and retry on 429/5xx."""
    for attempt in range(4):
        with _lock:
            wait = _next[0] - time.monotonic()
            _next[0] = max(_next[0], time.monotonic()) + 60 / REQ_PER_MIN
        if wait > 0:
            time.sleep(wait)
        r = session.post(INFO, json=body, timeout=15)
        if r.status_code == 200:
            return r.json()
        time.sleep(2 ** attempt * 5)
    r.raise_for_status()


def refresh_wallets(db, now):
    last = db.execute("SELECT v FROM meta WHERE k='lb_refresh'").fetchone()
    if last and now - int(last[0]) < 86_400_000:
        return
    rows = session.get(LEADERBOARD, timeout=60).json()["leaderboardRows"]
    db.executemany(
        "INSERT INTO wallets(address, account_value, first_seen) VALUES(?,?,?) "
        # Leaderboard value overstates perp equity; keep our polled value once we have one.
        "ON CONFLICT(address) DO UPDATE SET account_value=excluded.account_value "
        "WHERE wallets.last_checked IS NULL",
        [(r["ethAddress"], float(r["accountValue"]), now)
         for r in rows if float(r["accountValue"]) >= TIER_B])
    db.execute("INSERT OR REPLACE INTO meta VALUES('lb_refresh', ?)", (str(now),))
    db.commit()


def refresh_profiles(db, now):
    """Daily: 30d turnover (leaderboard volume / account value) and vault flag for every wallet."""
    last = db.execute("SELECT v FROM meta WHERE k='profile_refresh'").fetchone()
    if last and now - int(last[0]) < 86_400_000:
        return
    rows = session.get(LEADERBOARD, timeout=60).json()["leaderboardRows"]
    db.executemany("UPDATE wallets SET turnover=? WHERE address=?",
                   [(float(dict(r["windowPerformances"])["month"]["vlm"]) / max(float(r["accountValue"]), 1),
                     r["ethAddress"]) for r in rows])
    vaults = {v["summary"]["vaultAddress"].lower() for v in session.get(VAULTS, timeout=60).json()}
    db.execute("UPDATE wallets SET is_vault=0")
    db.executemany("UPDATE wallets SET is_vault=1 WHERE lower(address)=?", [(v,) for v in vaults])
    db.execute("INSERT OR REPLACE INTO meta VALUES('profile_refresh', ?)", (str(now),))
    db.commit()


def fetch_holdings(addr):
    """Spot balance of each tracked coin's spot token, plus staked/unstaking HYPE."""
    try:
        spot = info({"type": "spotClearinghouseState", "user": addr})
        bal = {b["coin"]: float(b["total"]) for b in spot.get("balances", [])}
        held = {c: bal.get(t, 0.0) for c, t in SPOT_TOKEN.items()}
        st = info({"type": "delegatorSummary", "user": addr})
        held["HYPE"] += sum(float(st.get(k, 0)) for k in ("delegated", "undelegated", "totalPendingWithdrawal"))
        return addr, held
    except Exception:
        return addr, None


def refresh_holdings(db, now, pool):
    """Wallets with a whale-size short: refresh holdings (stalest first, at most daily)."""
    addrs = [r[0] for r in db.execute(
        "SELECT DISTINCT w.address FROM latest_positions p JOIN wallets w ON w.address = p.address "
        "WHERE p.szi < 0 AND p.position_value >= ? AND COALESCE(w.holdings_checked, 0) < ? "
        "ORDER BY COALESCE(w.holdings_checked, 0) LIMIT ?", (WHALE_NOTIONAL, now - HOLD_MAX_AGE, HOLD_PER_RUN))]
    done = 0
    for addr, held in pool.map(fetch_holdings, addrs):
        if held is None:
            continue
        db.executemany("INSERT OR REPLACE INTO holdings VALUES(?,?,?)", [(addr, c, a) for c, a in held.items()])
        db.execute("UPDATE wallets SET holdings_checked=? WHERE address=?", (now, addr))
        done += 1
    db.commit()
    return done


def snapshot_market(db, now):
    meta, ctxs = info({"type": "metaAndAssetCtxs"})
    for asset, c in zip(meta["universe"], ctxs):
        if asset["name"] in COINS:
            db.execute("INSERT OR REPLACE INTO market_snapshots VALUES(?,?,?,?,?,?,?,?)",
                       (now, asset["name"], float(c["markPx"]), float(c["oraclePx"]),
                        float(c["funding"]), float(c["premium"]),
                        float(c["openInterest"]), float(c["dayNtlVlm"])))


def pick_wallets(db, cap, now):
    # Tier A every run, then stalest tier B (never-checked first). Accounts under TIER_B that held
    # no stored position at their last poll are only rechecked daily, to keep the rotation short.
    a = [r[0] for r in db.execute(
        "SELECT address FROM wallets WHERE account_value >= ? ORDER BY account_value DESC", (TIER_A,))]
    b = [r[0] for r in db.execute(
        "SELECT address FROM wallets WHERE account_value < ? AND (account_value >= ? OR last_checked IS NULL "
        "OR last_checked < ? OR address IN (SELECT address FROM latest_positions)) "
        "ORDER BY COALESCE(last_checked, 0), account_value DESC LIMIT ?",
        (TIER_A, TIER_B, now - 86_400_000, max(cap - len(a), 0)))]
    return (a + b)[:cap]


def fetch(addr):
    try:
        return addr, info({"type": "clearinghouseState", "user": addr})
    except Exception:
        return addr, None


def main():
    cap = int(sys.argv[1]) if len(sys.argv) > 1 else MAX_WALLETS
    t0, now = time.time(), int(time.time() * 1000)
    db = sqlite3.connect(DB)
    init_db(db)
    refresh_wallets(db, now)
    refresh_profiles(db, now)
    snapshot_market(db, now)

    errors, polled = 0, 0
    with ThreadPoolExecutor(8) as pool:
        for addr, state in pool.map(fetch, pick_wallets(db, cap, now)):
            if state is None:
                errors += 1
                db.execute("UPDATE wallets SET last_error=? WHERE address=?", (now, addr))
                continue
            polled += 1
            acct = float(state["marginSummary"]["accountValue"])
            gross = net = 0.0  # across ALL coins, for the relative-value label
            for ap in state["assetPositions"]:
                p = ap["position"]
                v = float(p["positionValue"])
                gross += v
                net += v if float(p["szi"]) > 0 else -v
                if p["coin"] in COINS and float(p["positionValue"]) >= STORE_NOTIONAL:
                    db.execute("INSERT OR REPLACE INTO positions(ts, address, coin, szi, entry_px, "
                               "position_value, leverage, lev_type, liq_px, unrealized_pnl, margin_used, "
                               "acct_value) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                               (now, addr, p["coin"], float(p["szi"]), float(p["entryPx"]),
                                float(p["positionValue"]), p["leverage"]["value"],
                                p["leverage"]["type"],
                                float(p["liquidationPx"]) if p["liquidationPx"] else None,
                                float(p["unrealizedPnl"]), float(p["marginUsed"]), acct))
            db.execute("UPDATE wallets SET last_checked=?, account_value=?, gross_ntl=?, net_ntl=? "
                       "WHERE address=?", (now, acct, gross, net, addr))
            if polled % 50 == 0:  # short write transactions so the collector can insert
                db.commit()
            if polled % 500 == 0:
                print(f"{polled} polled, {time.time() - t0:.0f}s", flush=True)
        db.commit()
        print(f"holdings refreshed: {refresh_holdings(db, now, pool)} wallets", flush=True)

    # Stamp this run's labels and hedges so history keeps what was known at the time.
    db.execute(f"UPDATE positions AS p SET label_pit = {LABEL_SQL}, hedged_pit = {HEDGE_SQL} "
               "FROM wallets w WHERE w.address = p.address AND p.ts = ?", (now,))

    db.execute("INSERT INTO runs VALUES(?,?,?,?)", (now, polled, errors, round(time.time() - t0, 1)))
    db.commit()
    print(f"done: {polled} wallets, {errors} errors, {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
