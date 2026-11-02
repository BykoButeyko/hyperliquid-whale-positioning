import pandas as pd

from explain import BASE_FUNDING_APR, explain


def text(raw, unh, day_ago=None, window=(), funding=BASE_FUNDING_APR, hedged=0.0, span="7d"):
    ago = None if day_ago is None else {"raw_share": raw, "dir_share": day_ago}
    return " ".join(explain("HYPE", {"raw_share": raw, "dir_share": unh}, ago,
                            pd.Series(window, dtype=float), funding, hedged, span))


def test_hedges_explain_a_big_gap():
    t = text(0.51, 0.31, hedged=0.64)
    assert "isn't a bet on price" in t
    assert "64% of the big shorts are covered" in t
    assert "only 31% is a directional short" in t
    assert "lean long" in t


def test_hedge_share_left_out_when_small():
    assert "covered by spot" not in text(0.60, 0.50, hedged=0.05)


def test_short_side_grows_without_market_makers():
    t = text(0.62, 0.65)
    assert "mostly real bets" in t and "rises to 65%" in t


def test_small_gap_means_directional():
    assert "barely change the picture" in text(0.52, 0.51)


def test_lean_words():
    assert "lean short on HYPE" in text(0.60, 0.60)
    assert "sit close to even on HYPE" in text(0.50, 0.50)
    assert "lean long on HYPE" in text(0.40, 0.40)


def test_day_change():
    assert "barely moved" in text(0.60, 0.60, day_ago=0.595)
    assert "moved 4.0 points toward short" in text(0.60, 0.60, day_ago=0.56)
    assert "toward long" in text(0.60, 0.60, day_ago=0.65)


def test_extreme_needs_enough_history():
    high = [0.40] * 30
    assert "most short-leaning readings of the last 7 days" in text(0.6, 0.6, window=high)
    assert "most long-leaning" in text(0.3, 0.3, window=high)
    assert "readings" not in text(0.6, 0.6, window=[0.40] * 10)


def test_funding_measured_against_hyperliquid_default():
    assert "default rate" in text(0.5, 0.5)                     # ~11% is the baseline, not crowding
    assert "paying extra to stay long" in text(0.5, 0.5, funding=0.30)
    assert "demand for longs is weaker" in text(0.5, 0.5, funding=-0.05)
