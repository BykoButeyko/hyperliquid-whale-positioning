"""Streamlit dashboard for hl_whales.db.  Run: streamlit run app.py"""
import sqlite3
import time

import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

from explain import explain
from ingest import COINS, DB, MM_TURNOVER, RELVAL_RATIO, TIER_A, WHALE_NOTIONAL

# Dark palette (blue/red diverging pair: CVD-safe, unlike green/red).
LONG, SHORT = "#3987e5", "#e66767"
PROFIT = "#e0a43a"   # profitable-wallet cohort; also dashed, so it never relies on color alone
SURFACE, GRID, AXIS, INK, INK2, MUTED = "#1a1a19", "#2c2c2a", "#383835", "#f0efec", "#c3c2b7", "#898781"
LABELS = {"directional": "Directional", "hedged_on_hl": "Hedged on HL", "relative_value": "Relative value",
          "market_maker": "Market maker", "vault": "Vault"}

st.set_page_config(page_title="HL Whale Positioning", layout="wide")
st.markdown(f"""<style>
.block-container {{padding-top: 2.6rem; max-width: 1400px;}}
.kicker {{font-size:.75rem; letter-spacing:.12em; text-transform:uppercase; color:{MUTED}; margin-bottom:6px;}}
.kicker a {{color:{MUTED}; text-decoration:none;}}
.kicker a:hover {{color:{INK};}}
.title {{font-size: 2.2rem; font-weight: 700; color: {INK}; line-height: 1.15; letter-spacing: -.02em;}}
.subtitle {{color: {MUTED}; font-size: .9rem; margin: 6px 0 14px; display:flex; align-items:center; gap:8px; flex-wrap:wrap;}}
.live {{width:8px; height:8px; border-radius:50%; background:#4fb286; box-shadow:0 0 0 3px rgba(79,178,134,.25);}}
.btns {{display:flex; gap:10px; margin:0 0 22px;}}
.btns a {{padding:6px 13px; border:1px solid {GRID}; border-radius:8px; color:{INK2}; font-size:.85rem; text-decoration:none;}}
.btns a:hover {{border-color:{MUTED}; color:{INK};}}
.card {{background:{SURFACE}; border:1px solid {GRID}; border-radius:12px; padding:14px 16px; transition:border-color .15s;}}
.card:hover {{border-color:{AXIS};}}
[data-testid="stPlotlyChart"] {{border:1px solid {GRID}; border-radius:12px; overflow:hidden;}}
.card.sel {{border-color:{INK2};}}
.card .head {{display:flex; justify-content:space-between; color:{INK2}; font-size:.85rem; letter-spacing:.06em;}}
.card .head span:last-child {{color:{MUTED}; letter-spacing:0;}}
.card .big {{color:{INK}; font-size:2.1rem; font-weight:600; margin:4px 0 0; line-height:1.2;}}
.card .big small {{font-size:.95rem; color:{INK2}; font-weight:400;}}
.card .delta {{color:{MUTED}; font-size:.8rem;}}
.split {{display:flex; gap:2px; height:6px; margin:10px 0 12px;}}
.split div {{border-radius:3px;}}
.stats {{display:grid; grid-template-columns:1fr 1fr; gap:8px 12px;}}
.stats div {{font-size:.78rem; color:{MUTED}; line-height:1.25;}}
.stats b {{display:block; font-size:.95rem; color:{INK}; font-weight:500;}}
.card .foot {{font-size:.75rem; color:{MUTED}; margin-top:10px; padding-top:8px; border-top:1px solid {GRID};}}
.section {{color:{INK}; font-weight:600; font-size:1rem; margin:8px 0 6px;}}
.method {{color:{MUTED}; font-size:.82rem; line-height:1.6; border-top:1px solid {GRID}; padding-top:14px; margin-top:28px;}}
.method ul {{margin:4px 0 0 0; padding-left:18px;}}
.method b {{color:{INK2};}}
.explain {{background:{SURFACE}; border:1px solid {GRID}; border-left:3px solid #7fb0ef; border-radius:10px; padding:14px 18px; margin-top:14px;}}
.explain b {{color:{INK}; font-size:.95rem;}}
.explain p {{color:{INK2}; font-size:.95rem; line-height:1.6; margin:6px 0 8px;}}
.explain span {{color:{MUTED}; font-size:.78rem;}}
.how {{color:{MUTED}; font-size:.8rem; line-height:1.5; margin:-6px 0 18px;}}
</style>""", unsafe_allow_html=True)


@st.cache_data(ttl=60)
def q(sql, params=()):
    with sqlite3.connect(f"file:{DB}?mode=ro", uri=True) as db:
        return pd.read_sql_query(sql, db, params=params)


def usd(x):
    if pd.isna(x):
        return "—"
    for div, s in ((1e9, "B"), (1e6, "M"), (1e3, "k")):
        if abs(x) >= div:
            return f"${x / div:.1f}".removesuffix(".0") + s
    return f"${x:.0f}"


def style(fig, height=300, title=None, time_axis=True):
    fig.update_layout(
        height=height, margin=dict(l=8, r=8, t=40 if title else 10, b=8),
        title=dict(text=title, font=dict(size=14, color=INK)) if title else None,
        paper_bgcolor=SURFACE, plot_bgcolor=SURFACE, font=dict(color=INK2, size=12),
        hovermode="x unified", hoverlabel=dict(bgcolor="#262624", font_color=INK),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1, font=dict(color=INK2)))
    fig.update_xaxes(gridcolor=GRID, linecolor=AXIS, tickfont=dict(color=MUTED))
    if time_axis:
        fig.update_xaxes(tickformat="%H:%M\n%b %d", hoverformat="%b %d %H:%M UTC")
    fig.update_yaxes(gridcolor=GRID, linecolor=AXIS, tickfont=dict(color=MUTED), zeroline=False)
    return fig


def chart(fig):
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})


def how(text):
    """One line under a chart that says how to read it."""
    st.markdown(f"<div class='how'>{text}</div>", unsafe_allow_html=True)


# ---------- data ----------
run = q("SELECT * FROM runs ORDER BY ts DESC LIMIT 1")
if run.empty:
    st.warning("No data yet. Run `python ingest.py` first.")
    st.stop()
run = run.iloc[0]
has_cache = not q("SELECT 1 FROM sqlite_master WHERE name = 'series_a_cache'").empty
series = q(f"SELECT * FROM {'series_a_cache' if has_cache else 'series_a'} ORDER BY ts")
series["dt"] = pd.to_datetime(series.ts, unit="ms", utc=True)
series["raw_share"] = series.short_ntl / (series.long_ntl + series.short_ntl)
series["dir_share"] = series.dir_short_ntl / (series.dir_long_ntl + series.dir_short_ntl)
if "smart_long_ntl" in series:      # absent until the next ingest run migrates the view
    smart_tot = series.smart_long_ntl + series.smart_short_ntl
    series["smart_share"] = (series.smart_short_ntl / smart_tot).where(smart_tot > 0)
else:
    series["smart_share"] = float("nan")
market = q("SELECT ts, coin, mark_px, funding, open_interest * mark_px AS oi_ntl FROM market_snapshots ORDER BY ts")
market["dt"] = pd.to_datetime(market.ts, unit="ms", utc=True)
cov = q("SELECT * FROM coverage").set_index("coin")
breadth = q("SELECT * FROM breadth_b").set_index("coin")
n_tracked = q("SELECT COUNT(*) n FROM wallets WHERE last_checked IS NOT NULL").n[0]

# ---------- header + controls ----------
age_min = (time.time() * 1000 - run.ts) / 60000
updated = pd.to_datetime(run.ts, unit="ms", utc=True).strftime("%b %d, %H:%M UTC")
st.markdown(f"<div class='kicker'><a href='https://bykobuteyko.com'>Byko Buteyko</a></div>"
            f"<div class='title'>Hyperliquid Whale Positioning</div>"
            f"<div class='subtitle'><span class='live'></span>BTC, ETH, SOL and HYPE perps · positions ≥ {usd(WHALE_NOTIONAL)} · "
            f"{n_tracked:,} wallets tracked · updated {updated} ({age_min:.0f} min ago)</div>"
            "<div class='btns'><a href='https://bykobuteyko.com/research/hyperliquid-whales/'>Read the research note</a>"
            "<a href='https://github.com/BykoButeyko/hyperliquid-whale-positioning'>Code and method</a></div>",
            unsafe_allow_html=True)

c1, c2, c3, _ = st.columns([2, 2, 2, 1])
with c1:
    mode = st.segmented_control(
        "Measure", ["Unhedged", "Raw"], default="Unhedged",
        help="Unhedged: excludes market makers, vaults and relative-value books, and nets shorts against "
             "spot and staked holdings of the same coin on Hyperliquid. Raw: every tracked position.") or "Unhedged"
with c2:
    coin = st.segmented_control("Coin", COINS, default=COINS[0]) or COINS[0]
with c3:
    RANGES = {"24h": 1, "7d": 7, "30d": 30, "All": None}
    span = st.segmented_control("Range", list(RANGES), default="7d",
                                help="Time window for the charts. Cards always show the latest snapshot.") or "7d"
DIR = mode == "Unhedged"
L, S, SHARE, OTHER = (("dir_long_ntl", "dir_short_ntl", "dir_share", "raw_share") if DIR
                      else ("long_ntl", "short_ntl", "raw_share", "dir_share"))
other_name = "Raw" if DIR else "Unhedged"

# ---------- coin cards ----------
for col, c in zip(st.columns(len(COINS)), COINS):
    s, m = series[series.coin == c], market[market.coin == c]
    if s.empty or m.empty:
        col.markdown(f"<div class='card'><div class='head'><span>{c}</span></div>no data</div>",
                     unsafe_allow_html=True)
        continue
    last, mk = s.iloc[-1], m.iloc[-1]
    ago = s[s.ts <= last.ts - 86_400_000]
    delta = (f"{(last[SHARE] - ago.iloc[-1][SHARE]) * 100:+.1f} pp vs 24h ago" if not ago.empty
             else "24h change after 1 day of data")
    cv = cov.loc[c] if c in cov.index else None
    b = breadth.loc[c] if c in breadth.index else None
    b_share = f"{b.short_ntl / (b.long_ntl + b.short_ntl):.0%}" if b is not None else "—"
    long_pct = (1 - last[SHARE]) * 100
    col.markdown(f"""<div class='card{" sel" if c == coin else ""}'>
      <div class='head'><span>{c}</span><span>${mk.mark_px:,.2f}</span></div>
      <div class='big'>{last[SHARE]:.0%} <small>short</small></div>
      <div class='delta'>{delta}</div>
      <div class='split'><div style='width:{long_pct}%;background:{LONG}'></div>
        <div style='width:{100 - long_pct}%;background:{SHORT}'></div></div>
      <div class='stats'>
        <div><b>{last[OTHER]:.0%}</b>{other_name.lower()} short</div>
        <div><b>{b_share}</b>short, wallets &lt; {usd(TIER_A)}</div>
        <div><b>{mk.funding * 24 * 365:.1%}</b>funding APR</div>
        <div><b>{cv.coverage:.0%}</b>of open interest tracked</div>
      </div>
      <div class='foot'>Accounts ≥ {usd(TIER_A)}: {usd(last[L])} long · {usd(last[S])} short</div>
    </div>""" if cv is not None else "", unsafe_allow_html=True)

s, m = series[series.coin == coin], market[market.coin == coin]
if RANGES[span]:
    since = run.ts - RANGES[span] * 86_400_000
    s, m = s[s.ts >= since], m[m.ts >= since]
few = len(s) < 2
mk_mode = "lines+markers" if few else "lines"

# ---------- what this means ----------
full = series[series.coin == coin]
bd = q("SELECT * FROM breakdown WHERE coin = ?", (coin,))
if not full.empty and not m.empty:
    last = full.iloc[-1]
    ago = full[full.ts <= last.ts - 86_400_000]
    shorts = bd[bd.side == "short"].ntl.sum()
    hedged = bd[bd.label == "hedged_on_hl"].ntl.sum()
    lines = explain(coin, last, ago.iloc[-1] if not ago.empty else None, s.dir_share,
                    m.iloc[-1].funding * 24 * 365, hedged / shorts if shorts else 0, span)
    st.markdown(f"<div class='explain'><b>What this means for {coin}</b><p>{' '.join(lines)}</p>"
                "<span>A description of current positions, not a forecast. Hedges held off Hyperliquid "
                "don't show up.</span></div>", unsafe_allow_html=True)
st.write("")

# ---------- time series ----------
left, right = st.columns([3, 2])
with left:
    fig = go.Figure()
    for name, ycol, color in (("Long", L, LONG), ("Short", S, SHORT)):
        fig.add_scatter(x=s.dt, y=s[ycol], name=name, line=dict(color=color, width=2), mode=mk_mode,
                        marker=dict(size=8), hovertemplate="%{y:$.3s}<extra>" + name + "</extra>")
    chart(style(fig, 340, f"{coin} notional, accounts ≥ {usd(TIER_A)} ({mode.lower()})"))
    how("Total value of long and short positions held by accounts above $1M. The gap between the lines is their net lean.")
with right:
    fig = go.Figure()
    fig.add_scatter(x=s.dt, y=s[OTHER], name=other_name, mode=mk_mode, marker=dict(size=8),
                    line=dict(color=MUTED, width=1.5, dash="dot"),
                    hovertemplate="%{y:.1%}<extra>" + other_name.lower() + "</extra>")
    fig.add_scatter(x=s.dt, y=s[SHARE], name=mode, mode=mk_mode, marker=dict(size=8),
                    line=dict(color=INK, width=2), hovertemplate="%{y:.1%}<extra>" + mode.lower() + "</extra>")
    cols = [SHARE, OTHER]
    if s.smart_share.notna().any():
        fig.add_scatter(x=s.dt, y=s.smart_share, name="Profitable", mode=mk_mode, marker=dict(size=8),
                        line=dict(color=PROFIT, width=1.5, dash="dash"), connectgaps=False,
                        hovertemplate="%{y:.1%}<extra>profitable wallets</extra>")
        cols.append("smart_share")
    lo, hi = s[cols].min().min(), s[cols].max().max()
    lo, hi = max(lo - 0.05, 0), min(hi + 0.05, 1)
    if lo < 0.5 < hi:
        fig.add_hline(y=0.5, line=dict(color=AXIS, width=1))
    fig.update_yaxes(tickformat=".0%", range=[lo, hi])
    chart(style(fig, 340, f"{coin} short share"))
    how("Share of that value that is short; above 50% means more short than long. The dotted line shows the other "
        "measure. Profitable: directional accounts that made $1M+ before the last 30 days, unhedged.")

if few:
    st.caption("Time series need at least two snapshots. History builds up every 15 minutes.")

left, right = st.columns(2)
with left:
    fig = go.Figure(go.Scatter(x=m.dt, y=m.oi_ntl, line=dict(color=INK2, width=2), mode=mk_mode,
                               marker=dict(size=8), hovertemplate="%{y:$.3s}<extra>OI</extra>"))
    chart(style(fig, 260, f"{coin} open interest (USD)"))
    how("Value of all open positions in this coin on Hyperliquid, from every trader, not only the large ones.")
with right:
    fig = go.Figure(go.Scatter(x=m.dt, y=m.funding * 24 * 365, line=dict(color=INK2, width=2), mode=mk_mode,
                               marker=dict(size=8), hovertemplate="%{y:.1%}<extra>funding APR</extra>"))
    fig.add_hline(y=0, line=dict(color=AXIS))
    fig.update_yaxes(tickformat=".0%")
    chart(style(fig, 260, f"{coin} funding (annualized)"))
    how("What longs pay shorts per year to hold positions (negative: shorts pay longs). Hyperliquid's default is about 11%; above it, the market crowds into longs.")

# ---------- who holds the positions ----------
BD_NAMES = {"directional": "Directional (unhedged)", "hedged_on_hl": "Hedged on HL (spot/staked)",
            "relative_value": "Relative value (net-flat book)",
            "market_maker": f"Market maker (≥{MM_TURNOVER}x turnover)", "vault": "Vault"}
if not bd.empty:
    piv = bd.pivot_table(index="label", columns="side", values="ntl", aggfunc="sum").reindex(
        list(BD_NAMES)[::-1]).fillna(0)
    for side in ("long", "short"):
        if side not in piv:
            piv[side] = 0.0
    names = [BD_NAMES[i] for i in piv.index]
    fig = go.Figure()
    fig.add_bar(y=names, x=-piv.short, orientation="h", name="Short", marker_color=SHORT,
                customdata=piv.short, hovertemplate="%{y}<br>%{customdata:$.3s} short<extra></extra>")
    fig.add_bar(y=names, x=piv.long, orientation="h", name="Long", marker_color=LONG,
                hovertemplate="%{y}<br>%{x:$.3s} long<extra></extra>")
    fig.update_layout(barmode="relative", bargap=0.35, hovermode="closest")
    fig.add_vline(x=0, line=dict(color=AXIS))
    m_ = max(piv.long.max(), piv.short.max()) * 1.1
    fig.update_xaxes(range=[-m_, m_], tickvals=[-m_ * 2 / 3, -m_ / 3, 0, m_ / 3, m_ * 2 / 3],
                     ticktext=[usd(m_ * 2 / 3), usd(m_ / 3), "0", usd(m_ / 3), usd(m_ * 2 / 3)])
    chart(style(fig, 280, f"Who holds the {coin} positions (all tracked wallets, short ← → long)",
                time_axis=False))
    how("Only the Directional row is a bet on price. The other rows are market making, vault strategies, net-flat books and shorts backed by spot or staked coins.")

# ---------- liquidation map ----------
pos = q("SELECT * FROM latest_classified WHERE coin = ? AND position_value >= ?", (coin, WHALE_NOTIONAL))
mark = m.iloc[-1].mark_px if not m.empty else None
if mark and not pos.empty:
    liq = pos[pos.liq_px.between(mark * 0.7, mark * 1.3)]
    step = mark * 0.01
    edges = np.arange(mark * 0.7, mark * 1.3 + step, step)
    centers = (edges[:-1] + edges[1:]) / 2
    fig = make_subplots(rows=1, cols=2, shared_yaxes=True, column_widths=[0.62, 0.38], horizontal_spacing=0.02)
    fig.add_scatter(x=m.dt, y=m.mark_px, name="Price", mode=mk_mode, line=dict(color=INK, width=1.5),
                    marker=dict(size=6), hovertemplate="%{y:,.2f}<extra>price</extra>", row=1, col=1)
    for name, side, color in (("Long liquidations", liq.szi > 0, LONG), ("Short liquidations", liq.szi < 0, SHORT)):
        ntl = np.histogram(liq[side].liq_px, bins=edges, weights=liq[side].position_value)[0]
        fig.add_bar(y=centers, x=ntl, orientation="h", width=step * 0.9, name=name, marker_color=color,
                    hovertemplate="%{y:,.2f}<br>%{x:$.3s}<extra>" + name + "</extra>", row=1, col=2)
    fig.add_hline(y=mark, line=dict(color=INK2, dash="dot", width=1))
    fig.add_annotation(text=f"mark ${mark:,.2f}", y=mark, x=1, xref="paper", xanchor="right", yanchor="bottom",
                       showarrow=False, font=dict(color=INK2, size=11))
    # Show the price history plus every 1% bucket that holds liquidations, nothing emptier.
    held = np.concatenate([centers[np.histogram(liq.liq_px, bins=edges)[0] > 0], m.mark_px.values, [mark]])
    pad = step * 2
    fig.update_layout(barmode="overlay", hovermode="closest", bargap=0)
    style(fig, 400, f"{coin} price and liquidation levels")
    fig.update_yaxes(range=[held.min() - pad, held.max() + pad], tickformat=",.0f" if mark > 100 else ",.2f")
    fig.update_xaxes(tickformat="$.2s", hoverformat="$.3s", row=1, col=2)   # style() set dates on both
    chart(fig)
    how("Left: price over the selected range. Right: value of positions that get force-closed if price reaches that "
        "level, in 1% buckets within ±30% of the price; longs sit below it, shorts above. Large clusters can speed a "
        "move up as the forced orders hit the book.")
    st.caption(f"Not shown: {len(pos) - len(liq)} positions with no liquidation price within ±30% "
               "(low leverage or cross margin).")

# ---------- largest positions ----------
if not pos.empty:
    st.markdown(f"<div class='section'>Largest {coin} positions</div>", unsafe_allow_html=True)
    how("Click a wallet to open it on hypurrscan. Hedged shows how much of a short is covered by spot or staked coins in the same wallet.")
    top = pos.sort_values("position_value", ascending=False).head(25)
    st.dataframe(pd.DataFrame({
        "wallet": "https://hypurrscan.io/address/" + top.address,
        "side": top.szi.map(lambda v: "Long" if v > 0 else "Short"),
        "wallet type": top.label.map(LABELS),
        "notional": top.position_value / 1e6,
        "hedged": (100 * top.hedged_ntl / top.position_value).where(top.szi < 0),
        "entry": top.entry_px, "liq": top.liq_px,
        "lev": top.leverage.round().astype(int).astype(str) + "x " + top.lev_type,
        "uPnL": top.unrealized_pnl / 1e6, "account": top.acct_value / 1e6,
    }), hide_index=True, use_container_width=True, height=360, column_config={
        "wallet": st.column_config.LinkColumn(display_text=r"address/(0x.{6})"),
        "notional": st.column_config.NumberColumn(format="$%.1fM"),
        "hedged": st.column_config.ProgressColumn(format="%.0f%%", min_value=0, max_value=100,
                                                  help="Share of the short covered by spot/staked holdings on HL"),
        "entry": st.column_config.NumberColumn(format="%.2f"),
        "liq": st.column_config.NumberColumn(format="%.2f"),
        "uPnL": st.column_config.NumberColumn(format="$%.2fM"),
        "account": st.column_config.NumberColumn(format="$%.1fM"),
    })

st.markdown(f"""<div class='method'><b>Method</b><ul>
<li>Wallets come from the public Hyperliquid leaderboard, plus any address that trades $50k or more in a day on the live
trade stream. "Tracked" shows the share of open interest this sample covers (long + short over both sides of OI).</li>
<li>Accounts ≥ {usd(TIER_A)} are polled every 15 minutes and drive the time series. Smaller accounts rotate and are
usually one run behind.</li>
<li><b>Unhedged</b> drops vaults, market makers (30-day volume ≥ {MM_TURNOVER}× account value) and relative-value books
(|net| / gross exposure across all coins &lt; {RELVAL_RATIO}), and nets shorts against spot and staked holdings of the same
coin on Hyperliquid. The turnover distribution has no natural break, so the market-maker cut is a judgment call.</li>
<li>Labels and hedges are stamped at each snapshot. Results hold across market-maker cuts from 30× to none (see README).
Hedges held off Hyperliquid (exchange spot, ETFs, other chains) don't show up, so "unhedged" means unhedged on
Hyperliquid.</li>
</ul></div>""", unsafe_allow_html=True)
