# Kalshi Sentiment Experiment

A personal, non-commercial research tool studying whether public Reddit discussion
volume precedes price movement on [Kalshi](https://kalshi.com) (a CFTC-regulated
event-contract exchange). **It does not trade, place orders, or hold any Kalshi
API key** — market data is read from Kalshi's public endpoints, which require no
authentication.

## What it does

Every 6 hours, for a rolling basket of up to 40 Kalshi markets currently priced at
an extreme probability (≤10¢ or ≥90¢):

1. Reads the market's current bid/ask from Kalshi's public REST API (no auth).
2. Runs one read-only search against Reddit's public `/search` endpoint for
   keywords derived from the market's title, and counts how many submissions
   were posted in the last 6 hours.
3. Stores both numbers, with timestamps, in a local SQLite database.

After several weeks of passive logging, `analyze.py` runs a single pre-committed
statistical test: does a spike in mention volume precede larger price movement
than quiet periods, compared using a Mann-Whitney U test.

## What it does NOT do (Reddit API use, specifically)

- No posting, commenting, voting, or messaging — the app has no write scope at all.
- No access to private data, DMs, or anything behind a login wall.
- No per-user tracking or profiling — only an aggregate count of how many public
  submissions matched a keyword in a time window is stored. Individual post
  content, authors, and IDs are never recorded.
- No subreddit is targeted specifically — it uses Reddit's site-wide public
  search, since Kalshi markets span unrelated topics (politics, economics,
  sports, crypto) that don't map to one subreddit.
- Read-only, app-only OAuth (`client_credentials` grant) — there is no
  Reddit user login anywhere in this code, and it never acts as any Reddit
  account.
- Request volume: ~30-40 search calls per 6-hour cycle (well under 1 request per
  10 minutes on average), far below standard rate limits.

## Files

| File | Purpose |
|---|---|
| `kalshi_client.py` | Kalshi public market-data client: scans for qualifying markets, fetches live prices. |
| `reddit_client.py` | Reddit read-only API client: app-only OAuth token, keyword search + mention counting. |
| `db.py` | SQLite schema (tracked markets, price snapshots, mention snapshots). |
| `logger.py` | The scheduler — runs the 6-hourly observation cycle. Entry point: `python logger.py`. |
| `analyze.py` | Post-hoc statistical analysis, run manually after several weeks of data. |

## Setup

```
pip install -r requirements.txt
cp .env.example .env   # fill in REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET
python logger.py
```

Kalshi market data requires no credentials. Reddit requires a free, read-only
"script" app registered at reddit.com/prefs/apps.
