#!/usr/bin/env python3
"""
Kalshi Sentiment Experiment -- Logger
=========================================
Pure observation. No trading, no Kalshi auth, no capital at risk. Every
SNAPSHOT_INTERVAL_HOURS:
  1. Refresh the tracked basket: check existing non-resolved markets for
     resolution (record final outcome), scan for newly-qualifying markets
     to fill any open basket slots.
  2. Snapshot price + Reddit mention volume for every actively tracked,
     unresolved market.

Run: python logger.py
Analyze after several weeks: python analyze.py

Pre-committed success bar (decided before collecting data, see project
scope): >=50 resolved market-observations with a directional hit rate
whose 95% confidence interval clears chance. Don't eyeball early results.
"""

import logging
import os
import time
from datetime import datetime, timezone

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import db
import kalshi_client
import reddit_client

SNAPSHOT_INTERVAL_HOURS = float(os.getenv("SNAPSHOT_INTERVAL_HOURS", "6"))
BASKET_SIZE = int(os.getenv("BASKET_SIZE", "40"))
RESCAN_EVERY_N_CYCLES = int(os.getenv("RESCAN_EVERY_N_CYCLES", "4"))  # rescan for new candidates once/day at 6h cadence

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("Logger")


def refresh_resolutions(conn):
    """Check active tracked markets for resolution; record final outcome."""
    rows = conn.execute(
        "SELECT ticker FROM tracked_markets WHERE resolved = 0"
    ).fetchall()
    resolved_count = 0
    for r in rows:
        info = kalshi_client.get_price(r["ticker"])
        if not info:
            continue
        if info["status"] in ("finalized", "settled") and info.get("result") in ("yes", "no"):
            now_ts = int(time.time())
            conn.execute(
                "UPDATE tracked_markets SET resolved=1, result=?, resolved_at_ts=? WHERE ticker=?",
                (info["result"], now_ts, r["ticker"]),
            )
            resolved_count += 1
    if resolved_count:
        conn.commit()
        log.info(f"Recorded {resolved_count} newly-resolved market outcomes")
    return resolved_count


def fill_basket(conn):
    """Top up the basket with newly-qualifying candidates up to BASKET_SIZE."""
    active_count = conn.execute(
        "SELECT COUNT(*) FROM tracked_markets WHERE resolved = 0"
    ).fetchone()[0]
    slots = BASKET_SIZE - active_count
    if slots <= 0:
        log.info(f"Basket full ({active_count}/{BASKET_SIZE}), skipping scan")
        return

    candidates = kalshi_client.scan_candidates()
    existing = {r["ticker"] for r in conn.execute("SELECT ticker FROM tracked_markets").fetchall()}
    added = 0
    now_ts = int(time.time())
    for c in candidates:
        if added >= slots:
            break
        if c["ticker"] in existing:
            continue
        conn.execute("""
            INSERT INTO tracked_markets
            (ticker, event_ticker, category, title, yes_sub_title, search_query, close_time, added_at_ts)
            VALUES (?,?,?,?,?,?,?,?)
        """, (
            c["ticker"], c["event_ticker"], c["category"], c["title"],
            c["yes_sub_title"], c["search_query"], c["close_time"], now_ts,
        ))
        added += 1
    conn.commit()
    log.info(f"Added {added} new markets to basket ({active_count + added}/{BASKET_SIZE} active)")


def snapshot_all(conn):
    rows = conn.execute(
        "SELECT ticker, search_query FROM tracked_markets WHERE resolved = 0"
    ).fetchall()
    now_ts = int(time.time())
    window_start = now_ts - int(SNAPSHOT_INTERVAL_HOURS * 3600)

    price_ok = reddit_ok = 0
    for r in rows:
        price = kalshi_client.get_price(r["ticker"])
        if price:
            conn.execute("""
                INSERT INTO price_snapshots (ticker, captured_at_ts, yes_bid, yes_ask, mid_price)
                VALUES (?,?,?,?,?)
            """, (r["ticker"], now_ts, price["yes_bid"], price["yes_ask"], price["mid_price"]))
            price_ok += 1

        mentions = reddit_client.count_mentions(r["search_query"], window_start, now_ts)
        conn.execute("""
            INSERT INTO reddit_snapshots
            (ticker, captured_at_ts, window_start_ts, window_end_ts, mention_count, hit_result_cap)
            VALUES (?,?,?,?,?,?)
        """, (r["ticker"], now_ts, window_start, now_ts, mentions["count"], int(mentions["hit_cap"])))
        reddit_ok += 1

    conn.commit()
    log.info(f"Snapshot cycle: {price_ok} prices, {reddit_ok} reddit counts logged for {len(rows)} tracked markets")


def cycle(conn, cycle_num):
    log.info(f"=== Cycle {cycle_num} starting ===")
    refresh_resolutions(conn)
    if cycle_num % RESCAN_EVERY_N_CYCLES == 0:
        fill_basket(conn)
    snapshot_all(conn)

    total = conn.execute("SELECT COUNT(*) FROM tracked_markets").fetchone()[0]
    resolved = conn.execute("SELECT COUNT(*) FROM tracked_markets WHERE resolved=1").fetchone()[0]
    log.info(f"=== Cycle {cycle_num} done | {total} markets ever tracked, {resolved} resolved so far ===")


def main():
    log.info("Kalshi Sentiment Experiment -- Logger starting (observation only, no trading)")
    conn = db.get_db()
    db.init_db(conn)

    cycle_num = 0
    while True:
        try:
            cycle_num += 1
            cycle(conn, cycle_num)
        except Exception:
            log.exception("Cycle failed, will retry next interval")
        time.sleep(SNAPSHOT_INTERVAL_HOURS * 3600)


if __name__ == "__main__":
    main()
