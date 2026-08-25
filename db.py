#!/usr/bin/env python3
"""
Kalshi Sentiment Experiment -- Database
==========================================
Pure observation, no trading. Schema tracks a rolling basket of extreme-
probability Kalshi markets, periodic price snapshots, periodic Reddit
mention-volume snapshots, and final resolution outcomes.
"""

import sqlite3
import os

DB_PATH = os.getenv("DB_PATH", "data/kalshi_sentiment.db")


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tracked_markets (
            ticker          TEXT PRIMARY KEY,
            event_ticker    TEXT NOT NULL,
            category        TEXT,
            title           TEXT,
            yes_sub_title   TEXT,
            search_query    TEXT NOT NULL,
            close_time      TEXT,
            added_at_ts     INTEGER NOT NULL,
            resolved        INTEGER DEFAULT 0,
            result          TEXT,          -- 'yes' | 'no' | NULL (pending)
            resolved_at_ts  INTEGER
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS price_snapshots (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker         TEXT NOT NULL,
            captured_at_ts INTEGER NOT NULL,
            yes_bid        REAL,
            yes_ask        REAL,
            mid_price      REAL
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_price_ticker
        ON price_snapshots(ticker, captured_at_ts)
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS reddit_snapshots (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker            TEXT NOT NULL,
            captured_at_ts    INTEGER NOT NULL,
            window_start_ts   INTEGER NOT NULL,
            window_end_ts     INTEGER NOT NULL,
            mention_count     INTEGER NOT NULL,
            hit_result_cap    INTEGER DEFAULT 0   -- 1 if the 100-result search
                                                    -- page was full (likely undercount)
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_reddit_ticker
        ON reddit_snapshots(ticker, captured_at_ts)
    """)

    conn.commit()
