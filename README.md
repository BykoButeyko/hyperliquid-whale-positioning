# Hyperliquid Whale Positioning

I wanted to know how the large wallets on Hyperliquid sit in BTC, ETH, SOL and HYPE perps, and how much of that is a bet on direction. A wallet that shorts $50M of HYPE while staking $50M of HYPE has no view on price, yet raw long/short numbers still count it as a short.

This repo polls about 4,000 large wallets every 15 minutes, stores every position worth $10k or more, and labels each wallet. The dashboard then splits positions into unhedged directional exposure, market makers, vaults, relative-value books and shorts hedged on Hyperliquid.

## What the first 36 hours show

Data from 2026-11-01 01:44 UTC to 2026-11-02 12:30 UTC, 143 snapshots.

**A few accounts drive the "whales are short" story.** My first test run covered the 200 largest accounts and found BTC 76% short. Widen it to the ~275 accounts above $1M and the figure falls to 59%. Across every tracked wallet with a $50k+ position it is 52%. On the full sample ETH (49%), HYPE (45%) and SOL (51%) sit close to even.

**Two thirds of the big HYPE shorts are hedges.** Wallets hold spot or staked HYPE against $470M of the $705M in whale-size HYPE shorts. They collect staking yield and carry no price risk. Net those hedges out and the $1M+ group goes from 50% short to 26% short, so the traders with real exposure lean long.

**Market makers don't explain the BTC short skew.** I removed market makers, vaults, relative-value books and on-chain hedges, and the $1M+ group moved from 59% to 62% short. Market makers hold more BTC long than short, so dropping them pushes the share up. Most of the largest BTC shorts trade 1 to 4 times their account value per month, run net short across all their positions and hold no BTC on Hyperliquid. They could be betting on a drop, or hedging spot they hold on an exchange or in an ETF. On-chain data can't tell the two apart.

**Positioning held steady.** For each coin the short share stayed inside a 2-point band over 36 hours (BTC 58.7% to 60.7%). I expect changes in notional, and behaviour around volatile days, to say more than the level.

All of this comes from a day and a half of a quiet market. Read it as a description of the sample, and don't trade on it.

## Dashboard

- **Coin cards:** short share (unhedged or raw), 24h change, funding, open interest coverage, data age
- **What this means:** a few plain sentences on the selected coin, built by fixed rules from the numbers on the page (`explain.py`), plus a line under each chart on how to read it
- **Tier-A time series:** long and short notional, short share
- **Open interest and funding**, each on its own chart
- **Who holds the positions:** notional by wallet label, shorts to the left, longs to the right
- **Liquidation map:** notional at liquidation price within ±30% of mark
- **Tier-B breadth** and the largest positions, with links to hypurrscan

## Method

### Finding wallets

Hyperliquid has no endpoint that returns all positions. I take candidate addresses from the public leaderboard (`stats-data.hyperliquid.xyz/Mainnet/leaderboard`, about 48k addresses, refreshed daily) and track every wallet it puts at $50k or more, about 16.6k.

The leaderboard misses wallets, so a second process (`collector.py`) listens to the BTC, ETH, SOL and HYPE trade stream. Every trade names both counterparties, and any address that trades $50k or more in a UTC day joins the tracked set. In a three-minute test, 23 new addresses came in. Four of them were $1M+ accounts, holding $82M of BTC positions the leaderboard never showed me.

Each coin reports coverage so you can see how much is still missing: tracked whale notional (long + short) over both sides of open interest. Before the trade stream it sat between 39% and 45%.

### Polling

| Tier | Definition | Polled | Wallets |
|---|---|---|---|
| A | perp account value ≥ $1M | every run (15 min) | ~275 |
| B | other wallets with $50k+ equity or an open position | stalest first, up to 4,000 requests per run | about every run |
| idle | accounts under $50k with no position | once a day | ~12.5k |

The time series uses tier A only, so each point compares the same group at the same freshness. Tier B gets its own breadth panel with its data age.

Most leaderboard wallets turn out smaller than listed and hold nothing. Polling those once a day instead of every hour cut tier B from 16.4k to 3.8k wallets, so tier B data went from up to an hour old to about 15 minutes.

A run sends about 4,000 `clearinghouseState` requests at 330 a minute. That uses 55% of the 1,200 weight-per-minute limit per IP and leaves room for my other bots on the same server. The limiter counts weight, not requests, so the heavier calls (holdings, volume, market data cost 20 each) slow the run down instead of pushing past the limit. A run takes about 13 minutes.

Wallets found through trades aren't on the leaderboard, so they have no volume figure there. Each run reads the 30-day volume of up to 40 of them from the `portfolio` endpoint, largest accounts first, so they can get the market-maker label too. On the first pass 27 of the 40 largest turned out to be market makers.

The leaderboard overstates account value. My guess is that it counts spot balances. Of the wallets it lists above $1M, about one in ten still clears $1M once I read the perp account. After the first poll I use my own value.

### Thresholds

- A whale position is $50k notional per wallet per coin. I apply that cut in the queries and store positions from $10k, so a position hovering around $50k doesn't flicker in and out of the data.
- A wallet with no row in a snapshot is flat or below $10k. If a wallet's latest poll failed, the views drop it until the next good poll, so old data never shows up as current.

### Labels

Each wallet gets the first label that fits:

| Label | Rule | Source |
|---|---|---|
| Vault | address is a Hyperliquid vault | stats-data vault list, daily |
| Market maker | 30-day volume ≥ 100× account value | leaderboard volume, daily; the `portfolio` endpoint for wallets the leaderboard doesn't list |
| Relative value | \|net\| / gross exposure across all coins < 0.3 | every poll |
| Directional | everything else | |

**Hedges on Hyperliquid.** For each short I count the wallet's spot balance in the same asset (UBTC, UETH, USOL, HYPE), plus staked and unstaking HYPE. The covered part shows up as *hedged on HL* and comes off the directional short. Holdings only offset shorts. I don't add them to longs, because I'm measuring perp positioning. Holdings refresh once a day for wallets with a whale-size short.

I also checked two places a hedge could hide. HYPE liquid staking tokens (KHYPE, STHYPE and others) add nothing: the wallets with HYPE shorts hold none. Subaccounts whose master wallet holds the spot add $0.2M against $1.35B of BTC shorts. Neither changes a number, so the code leaves them out.

**Labels are stamped at the time of each snapshot.** At the end of every run the ingest writes each position's label and hedged notional into the row, so a wallet that starts market making next month doesn't rewrite last month's chart. Snapshots taken before 2026-11-02 13:45 UTC didn't have this yet and use the labels from that day.

**I picked the 100× market-maker cut by hand.** Turnover among wallets with $50k+ positions follows one smooth, log-normal curve, with no gap between market makers and active traders. 100× catches the top 8% or so. I chose the 0.3 relative-value cut the same way.

To check whether those choices drive the results, `sensitivity.py` reruns the tier-A numbers over a grid of six market-maker cuts (30× to no filter) and four relative-value cuts (0.2 to no filter). On the 2026-11-02 snapshot:

| Coin | Raw short share | Unhedged, across all 24 combinations |
|---|---|---|
| BTC | 59.2% | 57.6% to 62.0% |
| ETH | 49.0% | 47.3% to 50.2% |
| SOL | 54.3% | 48.5% to 54.6% |
| HYPE | 50.2% | 22.1% to 30.3% |

No combination flips a conclusion. BTC stays short-heavy, ETH stays even, and HYPE stays far below its raw figure, because hedges drive that gap and the thresholds move it by a few points at most.

### Limitations

- Hedges held off Hyperliquid don't show up. "Unhedged" means unhedged on Hyperliquid.
- The trade stream only finds wallets that trade after the collector started. A whale that opened a position months ago and hasn't touched it since stays invisible unless it sits on the leaderboard.
- Most discovery still depends on an unofficial endpoint. If the leaderboard changes or goes away, only the trade stream keeps adding wallets.
- Cross-margin liquidation prices depend on the whole account, so treat the liquidation map as approximate. The chart leaves out low-leverage positions with no liquidation price within ±30% and counts them in a note below it.
- Subaccounts on the leaderboard get polled as their own addresses. I don't merge them with the master account.

## Running it

```bash
pip install -r requirements.txt
python ingest.py          # one snapshot; schedule it every 15 minutes
python ingest.py 200      # cap the wallets polled, for testing
python collector.py       # trade-stream wallet discovery; keep it running
python backup.py          # gzip snapshot to backups/, keeps 14
python sensitivity.py     # threshold sensitivity grid
python healthcheck.py     # phone alert via ntfy.sh when the pipeline stops (needs NTFY_TOPIC)
python alerts.py          # research alerts after each run: positioning shifts, $10M+ whale trades
streamlit run app.py      # dashboard
```

Everything lives in `hl_whales.db` (SQLite in WAL mode). Each ingest run creates or migrates the schema.

| Object | Contents |
|---|---|
| `positions` | every position ≥ $10k, per snapshot |
| `wallets` | account value, turnover, vault flag, net/gross exposure, poll times |
| `holdings` | latest spot and staked balances per wallet and coin |
| `market_snapshots` | mark, oracle, funding, premium, open interest and volume per snapshot |
| `series_a` | tier-A long/short notional per snapshot, raw and unhedged |
| `breakdown` | current notional by coin, label and side |
| `coverage` | tracked notional over open interest, data age |
| `breadth_b` | tier-B long/short, data age |

I run it on a small VPS with systemd: a 15-minute timer for ingest, a long-running collector service, a daily timer for backups, and Streamlit on loopback.

## License

MIT. See [LICENSE](LICENSE).
