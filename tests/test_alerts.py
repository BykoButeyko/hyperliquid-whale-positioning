"""alerts.py against a small in-memory database built with the real schema and views."""
import sqlite3

import pytest

import alerts
from ingest import init_db

H = 3_600_000
T0 = 1_800_000_000_000


@pytest.fixture
def db():
    d = sqlite3.connect(":memory:")
    init_db(d)
    return d


def snapshot(db, ts, books, prev=None):
    """books: {address: [(coin, signed_usd), ...]}, all $5M accounts with directional labels.
    Every wallet counts as polled at ts, and at prev if given."""
    db.execute("INSERT INTO runs VALUES(?, ?, 0, 1)", (ts, len(books)))
    db.execute("INSERT INTO market_snapshots VALUES(?, 'BTC', 100, 100, 0, 0, 1, 1)", (ts,))
    for addr, book in books.items():
        db.execute("INSERT INTO wallets(address, account_value, first_seen) VALUES(?, 5e6, 0) "
                   "ON CONFLICT(address) DO NOTHING", (addr,))
        db.execute("UPDATE wallets SET last_checked = ?, prev_checked = ? WHERE address = ?", (ts, prev, addr))
        for coin, v in book:
            db.execute("INSERT INTO positions(ts, address, coin, szi, entry_px, position_value, leverage, lev_type, "
                       "liq_px, unrealized_pnl, margin_used, acct_value, label_pit, hedged_pit) "
                       "VALUES(?,?,?,?,100,?,3,'cross',NULL,0,0,5e6,'directional',0)",
                       (ts, addr, coin, v / 100, abs(v)))


def wallets(n_long, n_short, size=1e6):
    """n wallets of `size` each; returns books for a BTC short share of n_short / (n_long + n_short)."""
    books = {f"0xl{i}": [("BTC", size)] for i in range(n_long)}
    books.update({f"0xs{i}": [("BTC", -size)] for i in range(n_short)})
    return books


def test_shift_alerts_once_then_waits(db):
    snapshot(db, T0, wallets(50, 50))                  # 50% short
    snapshot(db, T0 + H, wallets(46, 54))              # 54% short, same group size
    sent, log = {}, []
    assert alerts.shifts(db, T0 + H, sent, log) == [
        "BTC: $1M+ accounts went from 50% to 54% short (unhedged) in the last hour."]
    snapshot(db, T0 + H + 900_000, wallets(46, 54))    # same level 15 min later: still >= 3 pp vs an hour ago
    log2 = []
    assert alerts.shifts(db, T0 + H + 900_000, sent, log2) == []
    assert log2[0][4] == 0                             # still logged for the event study, marked not sent


def test_shift_ignored_when_group_changes(db):
    snapshot(db, T0, wallets(50, 50))
    snapshot(db, T0 + H, wallets(50, 62))              # 12 new short wallets: discovery, not trading
    assert alerts.shifts(db, T0 + H, {}, []) == []


def test_small_shift_is_quiet(db):
    snapshot(db, T0, wallets(50, 50))
    snapshot(db, T0 + H, wallets(49, 51))
    assert alerts.shifts(db, T0 + H, {}, []) == []


def test_big_trade_reported(db):
    snapshot(db, T0, {"0xwhale": [("BTC", -2e6)]})
    snapshot(db, T0 + 900_000, {"0xwhale": [("BTC", -14e6)]}, prev=T0)
    log = []
    out = alerts.big_moves(db, T0 + 900_000, log)
    assert out[0].startswith("BTC: 0xwhale… added $12M short (now $14M short, directional, 3x)")
    assert log == [("BTC", "big_trade", pytest.approx(-12e6), "0xwhale", 1)]


def test_big_trade_needs_both_polls(db):
    snapshot(db, T0, {"0xwhale": [("BTC", -2e6)]})
    # not polled in the previous run: the jump could be a book we never read
    snapshot(db, T0 + 900_000, {"0xwhale": [("BTC", -14e6)]}, prev=T0 - 900_000)
    assert alerts.big_moves(db, T0 + 900_000, []) == []


def test_closing_reads_as_a_cut(db):
    snapshot(db, T0, {"0xwhale": [("BTC", 20e6)], "0xother": [("ETH", 1e6)]})
    snapshot(db, T0 + 900_000, {"0xwhale": [], "0xother": [("ETH", 1e6)]}, prev=T0)
    assert "cut $20M of a position (now flat)" in alerts.big_moves(db, T0 + 900_000, [])[0]
