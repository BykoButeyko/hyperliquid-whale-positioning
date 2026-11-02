"""Streamlit dashboard for hl_whales.db.  Run: streamlit run app.py"""
import sqlite3
import time

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from ingest import COINS, DB, TIER_A, WHALE_NOTIONAL

# Dark palette (blue/red diverging pair: CVD-safe, unlike green/red).
LONG, SHORT = "#3987e5", "#e66767"
SURFACE, GRID, AXIS, INK, INK2, MUTED = "#1a1a19", "#2c2c2a", "#383835", "#f0efec", "#c3c2b7", "#898781"

st.set_page_config(page_title="HL Whale Positioning", layout="wide")
st.markdown(f"""<style>
.block-container {{padding-top: 2rem; max-width: 1400px;}}
.card {{background:{SURFACE}; border:1px solid rgba(255,255,255,.08); border-radius:10px; padding:16px 18px;}}
.card .coin {{color:{INK2}; font-size:.85rem; letter-spacing:.06em;}}
.card .px {{color:{MUTED}; font-size:.85rem; float:right;}}
.card .big {{color:{INK}; font-size:2rem; font-weight:600; margin:6px 0 2px;}}
.card .sub {{color:{MUTED}; font-size:.8rem; line-height:1.5;}}
.split {{display:flex; gap:2px; height:6px; margin:8px 0 10px;}}
.split div {{border-radius:3px;}}
.foot {{color:{MUTED}; font-size:.8rem; line-height:1.6; border-top:1px solid {GRID}; padding-top:12px; margin-top:24px;}}
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
            return f"${x / div:.1f}{s}"
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


# ---------- data ----------
run = q("SELECT * FROM runs ORDER BY ts DESC LIMIT 1")
if run.empty:
    st.warning("No data yet — run `python ingest.py` first.")
    st.stop()
run = run.iloc[0]
series = q("SELECT * FROM series_a ORDER BY ts")
series["dt"] = pd.to_datetime(series.ts, unit="ms", utc=True)
series["raw_share"] = series.short_ntl / (series.long_ntl + series.short_ntl)
series["dir_share"] = series.dir_short_ntl / (series.dir_long_ntl + series.dir_short_ntl)
market = q("SELECT ts, coin, mark_px, funding, open_interest * mark_px AS oi_ntl FROM market_snapshots ORDER BY ts")
market["dt"] = pd.to_datetime(market.ts, unit="ms", utc=True)
cov = q("SELECT * FROM coverage").set_index("coin")
breadth = q("SELECT * FROM breadth_b").set_index("coin")
n_tracked = q("SELECT COUNT(*) n FROM wallets WHERE last_checked IS NOT NULL").n[0]

# ---------- header ----------
age_min = (time.time() * 1000 - run.ts) / 60000
st.markdown(f"<h2 style='margin-bottom:0'>Hyperliquid Whale Positioning</h2>"
            f"<div style='color:{MUTED}'>Wallets with ≥{usd(WHALE_NOTIONAL)} notional per coin · "
            f"last run {age_min:.0f} min ago · {int(run.wallets_polled):,} wallets polled "
            f"({int(run.errors)} errors) · {n_tracked:,} tracked in total</div>",
            unsafe_allow_html=True)
st.write("")
mode = st.radio("Measure", ["Unhedged directional", "Raw (all wallets)"], horizontal=True,
                help="Unhedged directional excludes market makers, vaults and relative-value books, "
                     "and nets shorts against spot/staked holdings on Hyperliquid. See method below.")
DIR = mode.startswith("Unhedged")
L, S, SHARE, OTHER = (("dir_long_ntl", "dir_short_ntl", "dir_share", "raw_share") if DIR
                      else ("long_ntl", "short_ntl", "raw_share", "dir_share"))
other_name = "raw" if DIR else "unhedged"
series["short_share"] = series[SHARE]

# ---------- KPI cards ----------
for col, coin in zip(st.columns(len(COINS)), COINS):
    s = series[series.coin == coin]
    m = market[market.coin == coin]
    if s.empty or m.empty:
        col.markdown(f"<div class='card'><div class='coin'>{coin}</div><div class='sub'>no data</div></div>",
                     unsafe_allow_html=True)
        continue
    last, mk = s.iloc[-1], m.iloc[-1]
    ago = s[s.ts <= last.ts - 86_400_000]
    delta = (f"{(last.short_share - ago.iloc[-1].short_share) * 100:+.1f} pp vs 24h"
             if not ago.empty else "24h change: collecting history")
    c = cov.loc[coin] if coin in cov.index else None
    cov_txt = (f"coverage {c.coverage:.0%} of OI · data age ≤{c.max_age_min:.0f} min"
               if c is not None and pd.notna(c.coverage) else "coverage —")
    long_pct = (1 - last.short_share) * 100
    col.markdown(f"""<div class='card'>
      <span class='coin'>{coin}</span><span class='px'>${mk.mark_px:,.2f}</span>
      <div class='big'>{last.short_share:.0%} <span style='font-size:1rem;color:{INK2};font-weight:400'>short</span></div>
      <div class='split'><div style='width:{long_pct}%;background:{LONG}'></div>
        <div style='width:{100 - long_pct}%;background:{SHORT}'></div></div>
      <div class='sub'>Tier A: {usd(last[L])} long / {usd(last[S])} short · {other_name}: {last[OTHER]:.0%} short<br>
        {delta}<br>
        funding {mk.funding * 100:.4f}%/h · {mk.funding * 24 * 365:.1%} APR<br>{cov_txt}</div>
    </div>""", unsafe_allow_html=True)

st.write("")
coin = st.radio("Coin", COINS, horizontal=True, label_visibility="collapsed")
s = series[series.coin == coin]
m = market[market.coin == coin]
few = len(s) < 2

# ---------- time series ----------
left, right = st.columns([3, 2])
with left:
    fig = go.Figure()
    for name, ycol, color in (("Long", L, LONG), ("Short", S, SHORT)):
        fig.add_scatter(x=s.dt, y=s[ycol], name=name, line=dict(color=color, width=2),
                        mode="lines+markers" if few else "lines", marker=dict(size=8),
                        hovertemplate="%{y:$.3s}<extra>" + name + "</extra>")
    st.plotly_chart(style(fig, 340, f"{coin} · Tier-A notional, {mode.lower()} (accounts ≥ {usd(TIER_A)})"),
                    use_container_width=True)
with right:
    fig = go.Figure(go.Scatter(x=s.dt, y=s.short_share, line=dict(color=INK2, width=2),
                               mode="lines+markers" if few else "lines", marker=dict(size=8),
                               hovertemplate="%{y:.1%}<extra>short share</extra>"))
    fig.add_hline(y=0.5, line=dict(color=AXIS, dash="dot"))
    fig.update_yaxes(tickformat=".0%", range=[0, 1])
    st.plotly_chart(style(fig, 340, "Tier-A short share"), use_container_width=True)

if few:
    st.caption("Time series need ≥2 snapshots — history accumulates every 15 minutes.")

left, right = st.columns(2)
with left:
    fig = go.Figure(go.Scatter(x=m.dt, y=m.oi_ntl, line=dict(color=INK2, width=2),
                               mode="lines+markers" if len(m) < 2 else "lines", marker=dict(size=8),
                               hovertemplate="%{y:$.3s}<extra>OI</extra>"))
    st.plotly_chart(style(fig, 260, "Open interest (USD)"), use_container_width=True)
with right:
    fig = go.Figure(go.Scatter(x=m.dt, y=m.funding * 24 * 365, line=dict(color=INK2, width=2),
                               mode="lines+markers" if len(m) < 2 else "lines", marker=dict(size=8),
                               hovertemplate="%{y:.1%}<extra>funding APR</extra>"))
    fig.add_hline(y=0, line=dict(color=AXIS))
    fig.update_yaxes(tickformat=".0%")
    st.plotly_chart(style(fig, 260, "Funding (annualized)"), use_container_width=True)

# ---------- who holds the positions ----------
LABELS = {"directional": "Directional (unhedged)", "hedged_on_hl": "Hedged on HL (spot/staked)",
          "relative_value": "Relative value (net-flat book)", "market_maker": "Market maker (≥100x turnover)",
          "vault": "Vault"}
bd = q("SELECT * FROM breakdown WHERE coin = ?", (coin,))
if not bd.empty:
    piv = bd.pivot_table(index="label", columns="side", values="ntl", aggfunc="sum").reindex(
        list(LABELS)[::-1]).fillna(0)
    for side in ("long", "short"):
        if side not in piv:
            piv[side] = 0.0
    names = [LABELS[i] for i in piv.index]
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
    st.plotly_chart(style(fig, 280, f"{coin} · Who holds the positions (all tracked wallets, short ← → long)",
                          time_axis=False), use_container_width=True)

# ---------- liquidation map ----------
pos = q("SELECT * FROM latest_positions WHERE coin = ? AND position_value >= ?", (coin, WHALE_NOTIONAL))
mark = m.iloc[-1].mark_px if not m.empty else None
if mark and not pos.empty:
    liq = pos[pos.liq_px.between(mark * 0.7, mark * 1.3)]
    fig = go.Figure()
    for name, side, color in (("Long liquidations", liq.szi > 0, LONG), ("Short liquidations", liq.szi < 0, SHORT)):
        fig.add_histogram(x=liq[side].liq_px, y=liq[side].position_value, histfunc="sum", name=name,
                          xbins=dict(start=mark * 0.7, end=mark * 1.3, size=mark * 0.01),
                          marker=dict(color=color, line=dict(color=SURFACE, width=1)),
                          hovertemplate="%{x}<br>%{y:$.3s}<extra>" + name + "</extra>")
    fig.add_vline(x=mark, line=dict(color=INK, dash="dot"),
                  annotation=dict(text=f"mark ${mark:,.2f}", font=dict(color=INK2)))
    fig.update_layout(barmode="overlay", bargap=0.05, hovermode="closest")
    st.plotly_chart(style(fig, 320, f"{coin} · Liquidation map (notional at liq price, ±30% of mark, all tracked wallets)",
                          time_axis=False),
                    use_container_width=True)
    st.caption(f"{len(pos) - len(liq)} positions with liq price outside ±30% or none (low leverage / cross margin) not shown.")

# ---------- breadth + top positions ----------
left, right = st.columns([1, 2])
with left:
    st.markdown(f"<div style='color:{INK};font-weight:600;margin-bottom:6px'>Tier B breadth (accounts &lt; {usd(TIER_A)})</div>",
                unsafe_allow_html=True)
    if coin in breadth.index:
        b = breadth.loc[coin]
        share = b.short_ntl / (b.long_ntl + b.short_ntl)
        st.markdown(f"""<div class='card'><div class='big'>{share:.0%} <span style='font-size:1rem;color:{INK2};font-weight:400'>short</span></div>
          <div class='split'><div style='width:{(1 - share) * 100}%;background:{LONG}'></div>
            <div style='width:{share * 100}%;background:{SHORT}'></div></div>
          <div class='sub'>{usd(b.long_ntl)} long / {usd(b.short_ntl)} short · {int(b.n_wallets)} wallets<br>
          rotating sample: data up to {b.max_age_min:.0f} min old (notional-weighted {b.ntl_weighted_age_min:.0f} min)</div></div>""",
                    unsafe_allow_html=True)
    else:
        st.caption("No tier-B data yet.")
with right:
    st.markdown(f"<div style='color:{INK};font-weight:600;margin-bottom:6px'>Largest {coin} positions</div>",
                unsafe_allow_html=True)
    top = pos.sort_values("position_value", ascending=False).head(25)
    st.dataframe(pd.DataFrame({
        "wallet": "https://hypurrscan.io/address/" + top.address,
        "side": top.szi.map(lambda v: "Long" if v > 0 else "Short"),
        "notional": top.position_value / 1e6, "entry": top.entry_px, "liq": top.liq_px,
        "lev": top.leverage.round().astype(int).astype(str) + "x " + top.lev_type,
        "uPnL": top.unrealized_pnl / 1e6, "account": top.acct_value / 1e6,
        "age (min)": ((run.ts - top.ts) / 60000).round(0),
    }), hide_index=True, use_container_width=True, height=320, column_config={
        "wallet": st.column_config.LinkColumn(display_text=r"address/(0x.{6})"),
        "notional": st.column_config.NumberColumn(format="$%.2fM"),
        "entry": st.column_config.NumberColumn(format="%.2f"),
        "liq": st.column_config.NumberColumn(format="%.2f"),
        "uPnL": st.column_config.NumberColumn(format="$%.2fM"),
        "account": st.column_config.NumberColumn(format="$%.2fM"),
    })

st.markdown(f"""<div class='foot'><b>Method.</b> Wallets are discovered via the public Hyperliquid leaderboard
(wallets not on it are invisible) and polled via <code>clearinghouseState</code>. Tier A (account ≥ {usd(TIER_A)}) is polled
every 15 min and drives the time series; tier B rotates and may be hours old. Coverage = tracked whale notional (long + short) / both sides of open interest.
<b>Unhedged directional</b> excludes vaults, market makers (30-day volume ≥ 100× account value; the distribution has no
natural break, so this cut is a judgment call) and relative-value books (|net| / gross exposure across all coins &lt; 0.3),
and nets shorts against spot and staked holdings of the same coin on Hyperliquid. Labels use each wallet's current profile.
<b>Caveat:</b> hedges held off Hyperliquid (CEX spot, ETFs, other chains) are invisible — "unhedged" means unhedged on
Hyperliquid, not necessarily a directional bet.</div>""", unsafe_allow_html=True)
