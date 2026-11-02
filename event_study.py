"""Does price follow the whales? Forward returns after positioning signals, against chance.

Signals (tier A, from the stored history, so thresholds can be changed after the fact):
  shift     unhedged short share moves >= --shift points within an hour
  extreme   unhedged short share hits its trailing 7-day high or low (needs 3+ days of history)
  smart     same as shift, for the profitable-wallet cohort (data from 2026-11-03)
  big_trade a $1M+ account adds or cuts $10M+ in one coin (signals table, written by alerts.py)

Direction is the whales' side: more short = expects a fall. "Aligned return" is the price move in
that direction, so a positive mean means price followed the whales. Each mean is compared with the
same number of random timestamps given the same directions (permutation test).

  python event_study.py [--shift 3] [--gap 4] [--perm 2000]
"""
import argparse, random, sqlite3
from collections import defaultdict

import pandas as pd

from ingest import DB

HORIZONS_H = (1, 4, 24)
GROUP_CHANGE = 0.10


def load(db):
    s = pd.read_sql_query("SELECT * FROM series_a ORDER BY ts", db)
    s["unh"] = s.dir_short_ntl / (s.dir_long_ntl + s.dir_short_ntl)
    sm = s.smart_long_ntl + s.smart_short_ntl
    s["smart"] = (s.smart_short_ntl / sm).where(sm > 0)
    px = pd.read_sql_query("SELECT ts, coin, mark_px FROM market_snapshots ORDER BY ts", db)
    return s, {c: g.set_index("ts").mark_px for c, g in px.groupby("coin")}


def fwd_return(prices, ts, hours):
    """Price change from ts to the first snapshot at least `hours` later (within 20 min)."""
    idx, px = prices.index.values, prices.values
    i, j = idx.searchsorted(ts), idx.searchsorted(ts + hours * 3_600_000)
    if i >= len(idx) or idx[i] != ts or j >= len(idx) or idx[j] > ts + hours * 3_600_000 + 1_200_000:
        return None
    return px[j] / px[i] - 1


def declutter(events, gap_h):
    """Keep the first event per coin and kind within each gap, so one move counts once."""
    out, last = [], {}
    for e in sorted(events, key=lambda e: e["ts"]):
        k = (e["coin"], e["kind"])
        if k not in last or e["ts"] - last[k] >= gap_h * 3_600_000:
            out.append(e)
            last[k] = e["ts"]
    return out


def shift_events(s, col, kind, pp):
    out = []
    for coin, g in s.groupby("coin"):
        g = g.set_index("ts")
        for ts, row in g.iterrows():
            then = g[(g.index <= ts - 3_600_000 + 120_000)]
            if then.empty or pd.isna(row[col]) or pd.isna(then.iloc[-1][col]):
                continue
            prev = then.iloc[-1]
            d = (row[col] - prev[col]) * 100
            if abs(d) >= pp and abs(row.n_wallets - prev.n_wallets) <= GROUP_CHANGE * prev.n_wallets:
                out.append(dict(ts=ts, coin=coin, kind=kind, direction=1 if d > 0 else -1))
    return out


def extreme_events(s):
    out = []
    for coin, g in s.groupby("coin"):
        g = g.set_index("ts")
        start = g.index.min() + 3 * 86_400_000
        for ts, row in g[g.index >= start].iterrows():
            past = g[(g.index >= ts - 7 * 86_400_000) & (g.index < ts)].unh
            if row.unh > past.max():
                out.append(dict(ts=ts, coin=coin, kind="extreme", direction=1))
            elif row.unh < past.min():
                out.append(dict(ts=ts, coin=coin, kind="extreme", direction=-1))
    return out


def trade_events(db):
    rows = db.execute("SELECT ts, coin, value FROM signals WHERE kind = 'big_trade'").fetchall()
    # value is the signed USD change: short added (< 0) means the wallet expects a fall
    return [dict(ts=ts, coin=coin, kind="big_trade", direction=1 if v < 0 else -1) for ts, coin, v in rows]


def aligned(events, prices, h):
    vals = []
    for e in events:
        r = fwd_return(prices[e["coin"]], e["ts"], h)
        if r is not None:
            vals.append(-e["direction"] * r)
    return vals


def permutation_p(events, prices, h, observed, n_perm):
    """Share of random-timestamp draws (same coins, same directions) with a mean >= observed."""
    pool = {c: list(p.index[:-1]) for c, p in prices.items()}
    hits = 0
    for _ in range(n_perm):
        fake = [dict(e, ts=random.choice(pool[e["coin"]])) for e in events]
        v = aligned(fake, prices, h)
        if v and sum(v) / len(v) >= observed:
            hits += 1
    return hits / n_perm


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shift", type=float, default=3.0)
    ap.add_argument("--gap", type=float, default=4.0)
    ap.add_argument("--perm", type=int, default=2000)
    a = ap.parse_args()

    db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    s, prices = load(db)
    events = (shift_events(s, "unh", "shift", a.shift) + extreme_events(s)
              + shift_events(s, "smart", "smart", a.shift) + trade_events(db))
    events = declutter(events, a.gap)
    days = (s.ts.max() - s.ts.min()) / 86_400_000
    print(f"{days:.1f} days of data, {len(events)} events after a {a.gap:g}h gap per coin and kind\n")

    by_kind = defaultdict(list)
    for e in events:
        by_kind[e["kind"]].append(e)
    print(f"{'signal':<10} {'h':>3} {'n':>4} {'mean bps':>9} {'hit':>5} {'p':>6}")
    for kind, ev in by_kind.items():
        for h in HORIZONS_H:
            v = aligned(ev, prices, h)
            if not v:
                continue
            mean = sum(v) / len(v)
            p = permutation_p(ev, prices, h, mean, a.perm)
            flag = "  (n < 30: too few to read)" if len(v) < 30 else ""
            print(f"{kind:<10} {h:>3} {len(v):>4} {mean * 1e4:>9.1f} {sum(x > 0 for x in v) / len(v):>5.0%} "
                  f"{p:>6.2f}{flag}")
    print("\nmean bps > 0: price moved the way the whales leaned. p: chance of a mean this high "
          "from random timestamps. Many signals x horizons are tested, so expect some p < 0.05 by luck.")


if __name__ == "__main__":
    main()
