#!/usr/bin/env python3
"""
Kalshi Sentiment Experiment -- Reddit client
================================================
App-only (read-only) OAuth -- no user login needed, just a "script" type
app's client_id/client_secret from https://www.reddit.com/prefs/apps.
Free tier, no billing. Counts submission volume matching a market's derived
keywords within a time window -- volume/velocity only, no sentiment
polarity classification (see project scope: keep the first-pass signal
simple and robust).

Limitation, documented rather than hidden: Reddit's official search API
doesn't support precise before/after timestamp filtering, so this fetches
up to 100 newest results and filters client-side by created_utc. A topic
with >100 posts in one snapshot window would be undercounted -- flagged
via reddit_snapshots.hit_result_cap so it's visible in analysis, not a
silent gap.
"""

import base64
import logging
import os
import time
import urllib.request
import urllib.parse
import json

CLIENT_ID     = os.getenv("REDDIT_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("REDDIT_CLIENT_SECRET", "")
USER_AGENT    = os.getenv("REDDIT_USER_AGENT", "kalshi-sentiment-experiment/1.0")

TOKEN_URL  = "https://www.reddit.com/api/v1/access_token"
SEARCH_URL = "https://oauth.reddit.com/search"

log = logging.getLogger("RedditClient")

_token = None
_token_expires_at = 0


def _get_token() -> str | None:
    global _token, _token_expires_at
    if _token and time.time() < _token_expires_at - 60:
        return _token

    if not CLIENT_ID or not CLIENT_SECRET:
        log.error("REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET not set in .env")
        return None

    auth = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    body = urllib.parse.urlencode({"grant_type": "client_credentials"}).encode()
    req = urllib.request.Request(
        TOKEN_URL, data=body,
        headers={
            "Authorization": f"Basic {auth}",
            "User-Agent": USER_AGENT,
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        _token = data["access_token"]
        _token_expires_at = time.time() + data.get("expires_in", 3600)
        return _token
    except Exception as e:
        log.error(f"Reddit auth failed: {e}")
        return None


def count_mentions(query: str, window_start_ts: int, window_end_ts: int) -> dict:
    """
    Count Reddit submissions matching `query` with created_utc in
    [window_start_ts, window_end_ts]. Returns {'count': int, 'hit_cap': bool}.
    """
    token = _get_token()
    if not token:
        return {"count": 0, "hit_cap": False}

    params = urllib.parse.urlencode({
        "q": query,
        "sort": "new",
        "limit": 100,
        "type": "link",
    })
    req = urllib.request.Request(
        f"{SEARCH_URL}?{params}",
        headers={"Authorization": f"Bearer {token}", "User-Agent": USER_AGENT},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        log.warning(f"Reddit search failed for '{query}': {e}")
        return {"count": 0, "hit_cap": False}

    posts = (data.get("data") or {}).get("children", [])
    in_window = [
        p for p in posts
        if window_start_ts <= (p.get("data", {}).get("created_utc") or 0) <= window_end_ts
    ]
    hit_cap = len(posts) >= 100 and len(in_window) >= 95  # page was full AND mostly in-window
    return {"count": len(in_window), "hit_cap": hit_cap}
