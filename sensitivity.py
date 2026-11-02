"""How much do the headline numbers depend on the classification thresholds?

Recomputes the tier-A unhedged short share per coin for a grid of market-maker
turnover cuts and relative-value ratios, at the latest snapshot and averaged
over all snapshots.

  python sensitivity.py
"""
import sqlite3

import pandas as pd

from ingest import COINS, DB, TIER_A, WHALE_NOTIONAL

MM_CUTS = [30, 50, 100, 200, 500, None]      # None = no market-maker filter
RV_CUTS = [0.2, 0.3, 0.4, None]              # None = no relative-value filter

db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
pos = pd.read_sql_query(f"""
  SELECT p.ts, p.coin, p.szi, p.position_value, w.turnover, w.is_vault, w.gross_ntl, w.net_ntl,
         COALESCE(h.amount, 0) AS held
  FROM positions p JOIN wallets w ON w.address = p.address
  LEFT JOIN holdings h ON h.address = p.address AND h.coin = p.coin
  WHERE p.acct_value >= {TIER_A} AND p.position_value >= {WHALE_NOTIONAL}""", db)
pos["short"] = pos.szi < 0
pos["hedged"] = (pos.held.clip(upper=-pos.szi) * pos.position_value / -pos.szi).where(pos.short, 0.0)
pos["rv_ratio"] = (pos.net_ntl.abs() / pos.gross_ntl).fillna(1)
latest = pos.ts.max()


def short_share(df):
    s = (df.position_value - df.hedged)[df.short].sum()
    lg = df.position_value[~df.short].sum()
    return s / (s + lg)


rows = []
for mm in MM_CUTS:
    for rv in RV_CUTS:
        keep = pos.is_vault.fillna(0) == 0
        if mm is not None:
            keep &= ~(pos.turnover >= mm)
        if rv is not None:
            keep &= pos.rv_ratio >= rv
        d = pos[keep]
        for coin in COINS:
            dc = d[d.coin == coin]
            per_ts = dc.groupby("ts").apply(short_share, include_groups=False)
            rows.append(dict(mm=mm or "off", rv=rv or "off", coin=coin,
                             latest=short_share(dc[dc.ts == latest]), mean=per_ts.mean()))

res = pd.DataFrame(rows)
raw = {c: short_share(pos[(pos.coin == c) & (pos.ts == latest)].assign(hedged=0.0)) for c in COINS}
print(f"snapshots: {pos.ts.nunique()}   raw tier-A short share (latest): "
      + "  ".join(f"{c} {v:.1%}" for c, v in raw.items()))
print("\nUnhedged tier-A short share, latest snapshot (rows: MM turnover cut, cols: coin), RV cut 0.3:")
print(res[res.rv == 0.3].pivot(index="mm", columns="coin", values="latest")[list(COINS)]
      .map("{:.1%}".format).to_string())
print("\nSame, varying RV cut with MM cut 100:")
print(res[res.mm == 100].pivot(index="rv", columns="coin", values="latest")[list(COINS)]
      .map("{:.1%}".format).to_string())
spread = res.groupby("coin").latest.agg(["min", "max"])
print("\nRange across the whole grid (latest):")
print((spread * 100).round(1).astype(str).add("%").to_string())
