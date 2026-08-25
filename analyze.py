#!/usr/bin/env python3
"""
Kalshi Sentiment Experiment -- Analysis
===========================================
Run after several weeks of logging: python analyze.py

IMPORTANT SCOPE CORRECTION vs. the original framing: this experiment logs
Reddit MENTION VOLUME only, not sentiment polarity (positive/negative).
Volume alone has no inherent direction, so "does a mention spike precede a
move in the SAME direction" isn't actually testable with this data -- that
would need a polarity signal, which is a deliberately-deferred stage 2
(don't build the expensive part until the cheap part proves worth
extending). What IS testable, and what this script tests:

  Does a mention-volume spike precede LARGER price movement (in either
  direction) than quiet periods? I.e. does attention predict an imminent
  repricing at all, even before knowing which way?

That's a real, useful, honest question -- if it comes back positive, THAT's
the point to invest in a polarity classifier for stage 2. If it comes back
null, there's nothing to build on top of and the experiment ends here.

Pre-committed bar (decided before collecting data): don't trust this until
there are enough resolved markets and enough spike events for a real read.
"""

import sqlite3
import statistics
import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

DB_PATH = os.getenv("DB_PATH", "data/kalshi_sentiment.db")
SPIKE_RATIO = float(os.getenv("SPIKE_RATIO", "2.0"))  # mention_count > 2x rolling median = spike
MIN_BASELINE_OBS = 3  # need this many prior snapshots to call a baseline meaningful


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def load_series(conn, ticker):
    reddit = conn.execute(
        "SELECT captured_at_ts, mention_count, hit_result_cap FROM reddit_snapshots "
        "WHERE ticker=? ORDER BY captured_at_ts", (ticker,)
    ).fetchall()
    prices = conn.execute(
        "SELECT captured_at_ts, mid_price FROM price_snapshots "
        "WHERE ticker=? ORDER BY captured_at_ts", (ticker,)
    ).fetchall()
    price_by_ts = {p["captured_at_ts"]: p["mid_price"] for p in prices}
    return reddit, prices, price_by_ts


def find_next_price(prices, after_ts):
    for p in prices:
        if p["captured_at_ts"] > after_ts:
            return p["mid_price"], p["captured_at_ts"]
    return None, None


def analyze_spikes_vs_moves(conn):
    tickers = [r["ticker"] for r in conn.execute("SELECT DISTINCT ticker FROM tracked_markets").fetchall()]

    spike_moves = []
    quiet_moves = []
    capped_spike_count = 0

    for ticker in tickers:
        reddit, prices, price_by_ts = load_series(conn, ticker)
        if len(reddit) < MIN_BASELINE_OBS + 1 or len(prices) < 2:
            continue

        counts = [r["mention_count"] for r in reddit]
        for i in range(MIN_BASELINE_OBS, len(reddit)):
            baseline = statistics.median(counts[max(0, i - 6):i]) or 0.5  # avoid /0, floor at 0.5
            current = reddit[i]["mention_count"]
            ts = reddit[i]["captured_at_ts"]

            price_now = price_by_ts.get(ts)
            if price_now is None:
                continue
            price_next, _ = find_next_price(prices, ts)
            if price_next is None:
                continue
            move = abs(price_next - price_now)

            is_spike = current >= SPIKE_RATIO * baseline and current >= 3  # avoid noise on tiny counts
            if is_spike:
                spike_moves.append(move)
                if reddit[i]["hit_result_cap"]:
                    capped_spike_count += 1
            else:
                quiet_moves.append(move)

    return spike_moves, quiet_moves, capped_spike_count


def summarize_resolutions(conn):
    rows = conn.execute("""
        SELECT ticker, category, title, yes_sub_title, result, resolved_at_ts
        FROM tracked_markets WHERE resolved = 1
        ORDER BY resolved_at_ts DESC
    """).fetchall()
    return rows


def main():
    conn = get_db()

    total = conn.execute("SELECT COUNT(*) FROM tracked_markets").fetchone()[0]
    resolved = conn.execute("SELECT COUNT(*) FROM tracked_markets WHERE resolved=1").fetchone()[0]
    print(f"Markets ever tracked: {total} | Resolved: {resolved}\n")

    if resolved < 50:
        print(f"NOTE: pre-committed bar is >=50 resolved markets before trusting results.")
        print(f"Currently at {resolved}. Numbers below are directional only, not yet a real read.\n")

    spike_moves, quiet_moves, capped = analyze_spikes_vs_moves(conn)

    print("=== Mention-volume spike vs. quiet-period price movement ===")
    print(f"Spike observations: {len(spike_moves)} (capped/likely-undercounted: {capped})")
    print(f"Quiet observations: {len(quiet_moves)}")

    if len(spike_moves) >= 10 and len(quiet_moves) >= 10:
        spike_mean = statistics.mean(spike_moves)
        quiet_mean = statistics.mean(quiet_moves)
        print(f"Mean move after spike:  {spike_mean:.4f}")
        print(f"Mean move after quiet:  {quiet_mean:.4f}")
        print(f"Ratio: {spike_mean / quiet_mean:.2f}x" if quiet_mean else "N/A")

        try:
            from scipy import stats as sstats
            t, p = sstats.mannwhitneyu(spike_moves, quiet_moves, alternative="greater")
            print(f"Mann-Whitney U test (spike > quiet): U={t:.1f}, p={p:.4f}")
            if p < 0.05:
                print(">>> Statistically significant: spikes precede bigger moves. Worth a stage-2 polarity build.")
            else:
                print(">>> Not statistically significant yet.")
        except ImportError:
            print("(install scipy for a significance test: pip install scipy)")
    else:
        print("Not enough spike/quiet observations yet for a comparison.")

    print("\n=== Resolved markets so far ===")
    for r in summarize_resolutions(conn):
        label = r["yes_sub_title"] or r["title"]
        print(f"  [{r['category']:20s}] {label[:45]:45s} -> {r['result']}")


if __name__ == "__main__":
    main()
