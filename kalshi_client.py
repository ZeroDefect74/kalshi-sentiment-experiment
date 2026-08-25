#!/usr/bin/env python3
"""
Kalshi Sentiment Experiment -- Kalshi API client
====================================================
Public market-data endpoints only (no auth, no trading). Confirmed live
2026-08-25: base URL, status values ("open" / query "settled" -> returned
status "finalized"), and the result field ("yes"/"no" lowercase strings).
"""

import logging
import os
import time
import re
import urllib.request
import urllib.error
import json

BASE_URL = os.getenv("KALSHI_BASE_URL", "https://api.elections.kalshi.com/trade-api/v2")
REQUEST_DELAY_S = float(os.getenv("KALSHI_REQUEST_DELAY_S", "1.2"))  # avoid the 429 we hit before

# Basket filters
EXTREME_LOW  = float(os.getenv("EXTREME_LOW", "0.10"))   # mid_price <= this
EXTREME_HIGH = float(os.getenv("EXTREME_HIGH", "0.90"))  # mid_price >= this
MIN_OPEN_INTEREST = float(os.getenv("MIN_OPEN_INTEREST", "500"))
MAX_DAYS_TO_CLOSE = int(os.getenv("MAX_DAYS_TO_CLOSE", "30"))
# Qualifying events (extreme-priced, liquid, near-term) are sparse relative
# to the full open-event universe (confirmed empirically 2026-08-25: none
# of the first 200 events in Kalshi's default ordering qualified -- lots of
# near-50/50 sports games and thin novelty markets dominate that end of the
# list). A real scan needs real depth; this is a slow one-time-per-refresh
# cost (~1-1.5s per event, thousands of events => tens of minutes), which
# is fine for a background job that only rescans once/day, not something
# to run interactively expecting a fast result.
MAX_SCAN_PAGES = int(os.getenv("MAX_SCAN_PAGES", "20"))

log = logging.getLogger("KalshiClient")

STOPWORDS = {
    "the", "a", "an", "will", "be", "is", "are", "in", "on", "at", "of", "for",
    "to", "by", "as", "and", "or", "than", "did", "does", "do", "win", "wins",
    "what", "who", "which", "before", "after", "during", "next", "this", "that",
}


def _get(path: str, params: dict | None = None) -> dict:
    qs = ""
    if params:
        qs = "?" + "&".join(f"{k}={v}" for k, v in params.items() if v is not None)
    url = f"{BASE_URL}{path}{qs}"
    req = urllib.request.Request(url, headers={"User-Agent": "kalshi-sentiment-experiment/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        time.sleep(REQUEST_DELAY_S)
        return data
    except urllib.error.HTTPError as e:
        log.warning(f"HTTP {e.code} on {path}: {e.reason}")
        time.sleep(REQUEST_DELAY_S * 2)
        return {}
    except Exception as e:
        log.warning(f"Request failed for {path}: {e}")
        time.sleep(REQUEST_DELAY_S)
        return {}


def build_search_query(title: str, yes_sub_title: str) -> str:
    """Heuristic keyword extraction for the Reddit search query. Prefers the
    bucket-specific subtitle (e.g. a named candidate) when it's distinctive;
    falls back to the event title. Not scientifically tuned -- first-pass
    heuristic, revisit if the experiment shows any signal worth refining."""
    candidate = yes_sub_title if yes_sub_title and yes_sub_title.lower() not in ("yes", "no", "") else title
    words = re.findall(r"[A-Za-z0-9']+", candidate)
    kept = [w for w in words if w.lower() not in STOPWORDS]
    query = " ".join(kept[:8])  # keep queries short
    return query or title


def _list_events(max_pages: int = 15) -> list[dict]:
    """Lightweight open-event list (category/title, no nested markets).
    Deliberately NOT using /markets' own pagination directly -- its default
    order is dominated by thousands of auto-generated "MVE shard"
    combinatorial markets (dead/unpriced, garbled multi-condition titles)
    that bury real markets before any normal page is reached. Enumerating
    clean events first and then pulling each one's markets via
    /markets?event_ticker=X avoids that flood entirely."""
    events = []
    cursor = None
    for _ in range(max_pages):
        params = {"limit": 200, "status": "open"}
        if cursor:
            params["cursor"] = cursor
        data = _get("/events", params)
        page = data.get("events", [])
        if not page:
            break
        events.extend(page)
        cursor = data.get("cursor")
        if not cursor:
            break
    return events


def scan_candidates(max_pages: int = MAX_SCAN_PAGES) -> list[dict]:
    """
    Scan open Kalshi markets for the extreme-probability / min-liquidity /
    near-term-resolution basket criteria.
    """
    events = _list_events(max_pages=max_pages)
    log.info(f"Enumerated {len(events)} open events")

    candidates = []
    now_s = time.time()
    max_close_s = now_s + MAX_DAYS_TO_CLOSE * 86400

    for ev in events:
        event_ticker = ev.get("event_ticker")
        if not event_ticker:
            continue
        data = _get("/markets", {"status": "open", "event_ticker": event_ticker, "limit": 200})
        markets = data.get("markets", [])

        for m in markets:
            ticker = m.get("ticker", "")
            if "MVE" in ticker or m.get("market_type") != "binary":
                continue

            try:
                bid = float(m.get("yes_bid_dollars") or 0)
                ask = float(m.get("yes_ask_dollars") or 0)
                oi  = float(m.get("open_interest_fp") or 0)
                close_time = m.get("close_time", "")
            except (TypeError, ValueError):
                continue

            if ask <= 0 and bid <= 0:
                continue
            mid = (bid + ask) / 2 if ask else bid

            if not (mid <= EXTREME_LOW or mid >= EXTREME_HIGH):
                continue
            if oi < MIN_OPEN_INTEREST:
                continue

            try:
                import datetime
                ct = datetime.datetime.fromisoformat(close_time.replace("Z", "+00:00"))
                if ct.timestamp() > max_close_s or ct.timestamp() < now_s:
                    continue
            except Exception:
                continue

            ev_title = ev.get("title", "")
            candidates.append({
                "ticker":        ticker,
                "event_ticker":  event_ticker,
                "category":      ev.get("category", ""),
                "title":         ev_title,
                "yes_sub_title": m.get("yes_sub_title", ""),
                "search_query":  build_search_query(ev_title, m.get("yes_sub_title", "")),
                "close_time":    close_time,
                "yes_bid":       bid,
                "yes_ask":       ask,
                "mid_price":     mid,
            })

    log.info(f"Scan found {len(candidates)} candidate markets meeting basket criteria")
    return candidates


def get_price(ticker: str) -> dict | None:
    """Current bid/ask for a single tracked market."""
    data = _get(f"/markets/{ticker}")
    m = data.get("market")
    if not m:
        return None
    try:
        bid = float(m.get("yes_bid_dollars") or 0)
        ask = float(m.get("yes_ask_dollars") or 0)
    except (TypeError, ValueError):
        return None
    return {
        "yes_bid": bid,
        "yes_ask": ask,
        "mid_price": (bid + ask) / 2 if ask else bid,
        "status": m.get("status"),
        "result": m.get("result"),
    }
