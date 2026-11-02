"""Plain-English reading of the numbers for one coin. Rule-based on purpose: every sentence
comes straight from a number on the page, so it can't say something the data doesn't."""

BASE_FUNDING_APR = 0.0001 / 8 * 24 * 365   # 0.01% per 8 hours
SPAN_WORDS ={"24h": "24 hours", "7d": "7 days", "30d": "30 days", "All": "the full history"}


def lean(share):
    if share >= 0.55:
        return "lean short"
    if share <= 0.45:
        return "lean long"
    return "sit close to even"


def explain(coin, last, day_ago, window, funding_apr, hedged_share, span):
    """last / day_ago: rows with raw_share and dir_share (day_ago may be None).
    window: dir_share values over the selected range. hedged_share: share of whale shorts
    covered by spot or staked coins on Hyperliquid. Returns a list of sentences."""
    raw, unh = last["raw_share"], last["dir_share"]
    out = [f"Accounts above $1M {lean(raw)} on {coin}: {raw:.0%} of their position value is short."]

    gap = raw - unh
    if gap >= 0.05:
        why = (f" {hedged_share:.0%} of the big shorts are covered by spot or staked {coin} in the same wallet."
               if hedged_share >= 0.2 else "")
        out.append(f"Much of that short side isn't a bet on price.{why} Take out hedges, market makers and "
                   f"vaults and only {unh:.0%} is a directional short, so the traders betting on price {lean(unh)}.")
    elif gap <= -0.02:
        out.append(f"The short side is mostly real bets: after removing market makers, vaults and hedges it rises "
                   f"to {unh:.0%}, because the market makers here hold more long than short.")
    else:
        out.append(f"Market makers, vaults and hedges barely change the picture ({unh:.0%} after removing them), "
                   f"so most of what you see is directional.")

    if day_ago is not None:
        d = (unh - day_ago["dir_share"]) * 100
        out.append("That barely moved over the last 24 hours." if abs(d) < 1 else
                   f"Over the last 24 hours it moved {abs(d):.1f} points toward {'short' if d > 0 else 'long'}.")

    if len(window) >= 20:
        rank = (window < unh).mean()
        if rank >= 0.9:
            out.append(f"This is among the most short-leaning readings of the last {SPAN_WORDS[span]}.")
        elif rank <= 0.1:
            out.append(f"This is among the most long-leaning readings of the last {SPAN_WORDS[span]}.")

    # HL funding has a fixed interest part of 0.01% per 8h (~11% a year). Only the distance
    # from that baseline says which side the wider market crowds into.
    extra = funding_apr - BASE_FUNDING_APR
    if abs(extra) < 0.03:
        out.append(f"Funding sits at Hyperliquid's default rate ({funding_apr:.0%} a year), "
                   f"so the wider market isn't crowded on either side.")
    elif extra > 0:
        out.append(f"Funding runs above Hyperliquid's default ({funding_apr:.0%} a year vs about 11%): "
                   f"the wider market is paying extra to stay long.")
    else:
        out.append(f"Funding runs below Hyperliquid's default ({funding_apr:.0%} a year vs about 11%): "
                   f"demand for longs is weaker than usual across the market.")
    return out
