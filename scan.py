"""scan.py: raw premarket data gatherer for the Humbled report.

Collects data into packet.json and does ZERO analysis. No conviction,
no buckets, no opinions. All judgment happens later in the AI prompts.
Free and keyless only: yfinance, feedparser, requests, stdlib zoneinfo.

Run: python scan.py  (writes packet.json next to this file)
"""

import json
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import feedparser
import requests
import yfinance as yf

ET = ZoneInfo("America/New_York")
HERE = Path(__file__).resolve().parent

# ---------------- config ----------------

SNAPSHOT_INSTRUMENTS = {
    "S&P 500": "^GSPC",
    "Dow": "^DJI",
    "Nasdaq": "^IXIC",
    "Russell 2000": "^RUT",
    "VIX": "^VIX",
    "US 10Y": "^TNX",
    "US 3M": "^IRX",
    "WTI Oil": "CL=F",
    "Dollar (DXY)": "DX-Y.NYB",
}

# static fallback when the live screeners come back thin
UNIVERSE = [
    "NVDA", "AMD", "AVGO", "SMCI", "MRVL", "TSLA", "AAPL", "MSFT", "META",
    "AMZN", "GOOGL", "NFLX", "DELL", "SNOW", "PLTR", "COIN", "MSTR", "SOFI",
    "RIVN", "NIO", "MARA", "RIOT", "BA", "DIS", "JPM", "BAC", "XOM", "CVX",
    "HOOD", "UBER", "CRWD", "PANW", "CELH", "LULU", "NKE", "CAVA", "DKNG",
    "ARM", "INTC", "MU",
]

RSS_FEEDS = {
    "MarketWatch Top": "https://feeds.content.dowjones.io/public/rss/mw_topstories",
    "MarketWatch RealTime": "https://feeds.content.dowjones.io/public/rss/mw_realtimeheadlines",
    "CNBC": "https://www.cnbc.com/id/100003114/device/rss/rss.html",
    "Yahoo Finance": "https://finance.yahoo.com/news/rssindex",
    "Google News Markets": (
        "https://news.google.com/rss/search?"
        "q=stock+market+OR+earnings+when:1d&hl=en-US&gl=US&ceid=US:en"
    ),
}

GAP_MIN_ABS_PCT = 4.0
PRICE_MIN = 3.0
MAX_GAPPERS = 12

CAL_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
CAL_CACHE = HERE / ".ff_calendar_cache.json"
CAL_TTL_SECONDS = 4 * 3600  # feed 429s on rapid calls, so cache about 4 hours

TAG_RE = re.compile(r"<[^>]+>")
# obvious SEO spam: "price prediction" pieces and "2025-2030" style year ranges
SPAM_RE = re.compile(r"price prediction|20\d{2}\s*-\s*20\d{2}", re.IGNORECASE)

# Fix for catalyst cross-matching: generic company-name words must never
# count as a match on their own, because they hit unrelated firms (for
# example "Applied" matching both Applied Optoelectronics and Applied
# Digital). A headline only counts if it names the ticker on a word
# boundary or contains a DISTINCTIVE 4+ letter name token not in this set.
NAME_STOP = {
    "the", "inc", "incorporated", "corp", "corporation", "company", "companies",
    "holdings", "holding", "group", "ltd", "limited", "plc", "class", "trust",
    "technologies", "technology", "digital", "applied", "advanced", "strategy",
    "strategies", "motors", "motor", "energy", "platforms", "platform",
    "industries", "industrial", "international", "global", "systems", "system",
    "solutions", "services", "financial", "capital", "partners", "resources",
    "pharmaceuticals", "pharma", "therapeutics", "bancorp", "brands", "labs",
    "media", "entertainment", "communications", "sciences", "first", "united",
    "american", "national", "general", "enterprises", "acquisition", "fund",
}

PRIMARY_PUBLISHERS = (
    "bloomberg", "reuters", "cnbc", "marketwatch", "barron", "yahoo finance",
    "wsj", "wall street journal", "financial times", "associated press", "dow jones",
)


def log(msg):
    print(msg, flush=True)


# ---------------- 1) market snapshot ----------------

def market_snapshot():
    out = {}
    for name, sym in SNAPSHOT_INSTRUMENTS.items():
        try:
            closes = yf.Ticker(sym).history(period="7d", interval="1d")["Close"].dropna()
            if len(closes) >= 2:
                last, prev = float(closes.iloc[-1]), float(closes.iloc[-2])
                out[name] = {
                    "symbol": sym,
                    "last": round(last, 2),
                    "prev_close": round(prev, 2),
                    "change_pct": round((last - prev) / prev * 100.0, 2),
                }
            else:
                out[name] = {"symbol": sym, "error": "not enough daily history"}
        except Exception as e:
            out[name] = {"symbol": sym, "error": str(e)}
        log(f"  snapshot {name}: {out[name].get('change_pct', 'ERR')}")
    return out


# ---------------- 2) live movers via keyless screeners ----------------

def fetch_predefined_screener(name):
    try:
        res = yf.screen(name, count=25)  # keyless predefined screener
    except AttributeError:  # older yfinance API
        s = yf.Screener()
        s.set_predefined_body(name)
        res = s.response
    return res.get("quotes", []) if isinstance(res, dict) else []


def live_movers():
    quotes, seen = [], set()
    for scr in ("day_gainers", "most_actives"):
        try:
            batch = fetch_predefined_screener(scr)
        except Exception as e:
            log(f"  screener {scr} failed: {e}")
            batch = []
        for q in batch:
            sym = q.get("symbol")
            if not sym or sym in seen:
                continue
            seen.add(sym)
            quotes.append({
                "ticker": sym,
                "name": q.get("shortName") or q.get("longName") or sym,
                "price": q.get("regularMarketPrice"),
                "prev_close": q.get("regularMarketPreviousClose"),
                "gap_pct": q.get("regularMarketChangePercent"),
                "market_cap": q.get("marketCap"),
                "volume": q.get("regularMarketVolume"),
            })
    return quotes


def universe_movers():
    out = []
    for sym in UNIVERSE:
        try:
            t = yf.Ticker(sym)
            closes = t.history(period="7d", interval="1d")["Close"].dropna()
            if len(closes) < 2:
                continue
            last, prev = float(closes.iloc[-1]), float(closes.iloc[-2])
            try:
                mc = t.fast_info.market_cap
            except Exception:
                mc = None
            out.append({
                "ticker": sym,
                "name": sym,
                "price": round(last, 2),
                "prev_close": round(prev, 2),
                "gap_pct": round((last - prev) / prev * 100.0, 2),
                "market_cap": mc,
                "volume": None,
            })
        except Exception as e:
            log(f"  universe {sym} failed: {e}")
    return out


def gather_candidates():
    movers = live_movers()
    if len(movers) >= 5:
        return movers, "live screeners (day_gainers + most_actives)"
    log(f"  live screeners returned {len(movers)} names, falling back to static universe")
    return universe_movers(), "static universe fallback (gap from last two daily closes)"


# ---------------- 3) gap filter ----------------

def filter_gappers(movers):
    kept = [
        m for m in movers
        if m.get("gap_pct") is not None and m.get("price") is not None
        and abs(m["gap_pct"]) >= GAP_MIN_ABS_PCT and m["price"] >= PRICE_MIN
    ]
    kept.sort(key=lambda m: abs(m["gap_pct"]), reverse=True)
    return kept[:MAX_GAPPERS]


# ---------------- 4) market-wide news via RSS ----------------

def fetch_rss():
    items = []
    for src, url in RSS_FEEDS.items():
        try:
            feed = feedparser.parse(url)
            n = 0
            for e in feed.entries[:30]:
                title = (e.get("title") or "").strip()
                if not title or SPAM_RE.search(title):
                    continue
                publisher = src
                gsrc = e.get("source")
                if gsrc and gsrc.get("title"):  # Google News carries the real outlet
                    publisher = gsrc["title"]
                items.append({
                    "source": src,
                    "publisher": publisher,
                    "title": title,
                    "summary": TAG_RE.sub(" ", e.get("summary", "")).strip()[:300],
                    "link": e.get("link", ""),
                    "published": e.get("published", ""),
                })
                n += 1
            log(f"  RSS {src}: {n} items")
        except Exception as e:
            log(f"  RSS {src} failed: {e}")
    return items


# ---------------- 5) economic calendar (ForexFactory weekly JSON) ----------------

def load_week_events():
    """Returns (events, note). Cache first, live fetch, stale cache last."""
    if CAL_CACHE.exists():
        try:
            c = json.loads(CAL_CACHE.read_text(encoding="utf-8"))
            if time.time() - c.get("fetched_at", 0) < CAL_TTL_SECONDS:
                return c.get("events", []), "cache"
        except Exception:
            pass
    try:
        r = requests.get(CAL_URL, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        events = r.json()
        CAL_CACHE.write_text(
            json.dumps({"fetched_at": time.time(), "events": events}), encoding="utf-8"
        )
        return events, "live"
    except Exception as e:
        if CAL_CACHE.exists():
            try:
                c = json.loads(CAL_CACHE.read_text(encoding="utf-8"))
                return c.get("events", []), f"stale cache, live fetch failed: {e}"
            except Exception:
                pass
        return None, f"fetch failed: {e}"


def econ_calendar():
    today = datetime.now(ET).date()
    tomorrow = today + timedelta(days=1)
    out = {
        "source": CAL_URL,
        "filter": "country USD, impact High (covers data prints and Fed events)",
        "today_date": str(today),
        "tomorrow_date": str(tomorrow),
        "today": [],
        "tomorrow": [],
    }
    try:
        events, note = load_week_events()
        out["note"] = note
        if events is None:
            out["error"] = note
            return out
        buckets = {today: [], tomorrow: []}
        for ev in events:
            try:
                if ev.get("country") != "USD" or ev.get("impact") != "High":
                    continue
                dt = datetime.fromisoformat(ev["date"]).astimezone(ET)
                if dt.date() in buckets:
                    buckets[dt.date()].append((dt, {
                        "time_et": dt.strftime("%H:%M"),
                        "title": ev.get("title", ""),
                        "forecast": ev.get("forecast", ""),
                        "previous": ev.get("previous", ""),
                    }))
            except Exception:
                continue
        out["today"] = [rec for _, rec in sorted(buckets[today], key=lambda x: x[0])]
        out["tomorrow"] = [rec for _, rec in sorted(buckets[tomorrow], key=lambda x: x[0])]
    except Exception as e:
        out["error"] = str(e)
    return out


# ---------------- 6) per-gapper enrichment ----------------

def name_tokens(company):
    toks = re.findall(r"[A-Za-z]+", (company or "").lower())
    return [t for t in toks if len(t) >= 4 and t not in NAME_STOP]


def headline_matches(ticker, company, title):
    # ticker match is case sensitive on a word boundary so "COIN" does not
    # match the word "coin"; company match needs a distinctive token only
    if re.search(rf"\b{re.escape(ticker)}\b", title):
        return True
    low = title.lower()
    return any(re.search(rf"\b{re.escape(tok)}\b", low) for tok in name_tokens(company))


def publisher_rank(publisher):
    p = (publisher or "").lower()
    return 0 if any(k in p for k in PRIMARY_PUBLISHERS) else 1


def yf_ticker_news(t):
    out = []
    try:
        for item in (t.news or [])[:10]:
            c = item.get("content", item)
            title = (c.get("title") or "").strip()
            if not title:
                continue
            provider = c.get("provider") or {}
            out.append({
                "publisher": provider.get("displayName") or item.get("publisher", ""),
                "title": title,
                "link": ((c.get("canonicalUrl") or {}).get("url")) or item.get("link", ""),
                "published": str(c.get("pubDate") or item.get("providerPublishTime", "")),
            })
    except Exception as e:
        log(f"    yf news failed: {e}")
    return out


def catalyst_headlines(t, ticker, company, rss_items):
    heads = yf_ticker_news(t)
    for it in rss_items:
        if headline_matches(ticker, company, it["title"]):
            heads.append({
                "publisher": it["publisher"],
                "title": it["title"],
                "link": it["link"],
                "published": it["published"],
            })
    seen, unique = set(), []
    for h in heads:
        key = h["title"].lower()
        if key not in seen:
            seen.add(key)
            unique.append(h)
    unique.sort(key=lambda h: publisher_rank(h["publisher"]))
    return unique[:6]


def intraday_levels(t):
    out = {}
    try:
        h = t.history(period="1d", interval="5m", prepost=True)
        if h.empty:
            return {"error": "no intraday bars"}
        vol = h["Volume"].sum()
        out["vwap"] = round(float((h["Close"] * h["Volume"]).sum() / vol), 2) if vol else None
        out["hod"] = round(float(h["High"].max()), 2)
        out["lod"] = round(float(h["Low"].min()), 2)
        idx = h.index.tz_convert(ET) if h.index.tz is not None else h.index
        pm_mask = [(ts.hour, ts.minute) < (9, 30) for ts in idx]
        pm = h[pm_mask]
        out["premarket_high"] = round(float(pm["High"].max()), 2) if not pm.empty else None
        out["premarket_volume"] = int(pm["Volume"].sum()) if not pm.empty else 0
    except Exception as e:
        out["error"] = str(e)
    return out


def daily_metrics(t):
    out = {}
    try:
        d = t.history(period="1y", interval="1d")
        if d.empty:
            return {"error": "no daily bars"}
        today = datetime.now(ET).date()
        today_row = None
        hist = d
        if d.index[-1].date() == today:  # exclude today's partial bar from metrics
            today_row, hist = d.iloc[-1], d.iloc[:-1]
        if hist.empty:
            return {"error": "no completed daily bars"}
        out["sma_200"] = round(float(hist["Close"].tail(200).mean()), 2) if len(hist) >= 200 else None
        out["prior_day_high"] = round(float(hist["High"].iloc[-1]), 2)
        out["prior_close"] = round(float(hist["Close"].iloc[-1]), 2)
        out["today_open"] = round(float(today_row["Open"]), 2) if today_row is not None else None
        avg20 = float(hist["Volume"].tail(20).mean())
        out["avg_volume_20d"] = int(avg20)
        today_vol = int(today_row["Volume"]) if today_row is not None else None
        out["today_volume"] = today_vol
        # yfinance reports about 0 premarket volume, so a true premarket RVOL
        # needs a premarket feed (e.g. Alpaca); full-day relative volume is the
        # keyless stand-in here
        out["rvol"] = round(today_vol / avg20, 2) if today_vol and avg20 else None
    except Exception as e:
        out["error"] = str(e)
    return out


def next_earnings(t):
    try:
        cal = t.calendar
        if isinstance(cal, dict):
            d = cal.get("Earnings Date")
            if d:
                return str(d[0] if isinstance(d, (list, tuple)) else d)
    except Exception:
        pass
    return None


# ---------------- 7) deterministic eligibility flags ----------------
# These encode the validated rules from WATCHLIST_CRITERIA.md. They are
# computed in code so the AI passes judge quality, not membership.

def eligibility_flags(g):
    def have(*vals):
        return all(v is not None for v in vals)

    gap, price, mc = g.get("gap_pct"), g.get("price"), g.get("market_cap")
    dm = g.get("daily", {})
    rvol, ph = dm.get("rvol"), dm.get("prior_day_high")
    opn, sma = dm.get("today_open"), dm.get("sma_200")

    day = bool(
        have(gap, price, mc, rvol, ph)
        and gap > 3 and price > 3 and mc > 1e9 and rvol > 1.5 and price > ph
    )
    swing = bool(
        have(gap, price, mc, opn, ph, sma)
        and gap >= 8 and price > 3 and opn > ph and opn > sma
        and mc >= 8e8 and g.get("catalyst_found")
    )
    return day, swing


# ---------------- main ----------------

def main():
    now = datetime.now(ET)
    log(f"scan.py starting {now:%Y-%m-%d %H:%M} ET")

    log("[1/6] market snapshot")
    snapshot = market_snapshot()

    log("[2/6] top movers")
    movers, candidate_source = gather_candidates()
    log(f"  {len(movers)} candidates via {candidate_source}")
    gappers = filter_gappers(movers)
    log(f"  {len(gappers)} pass gap filter (abs gap >= {GAP_MIN_ABS_PCT}%, price >= ${PRICE_MIN})")

    log("[3/6] market news RSS")
    rss_items = fetch_rss()

    log("[4/6] economic calendar")
    econ = econ_calendar()
    log(f"  today {len(econ['today'])} events, tomorrow {len(econ['tomorrow'])} ({econ.get('note', econ.get('error', ''))})")

    log("[5/6] enriching gappers")
    for g in gappers:
        tkr = g["ticker"]
        log(f"  {tkr} ({g['gap_pct']:+.1f}%)")
        t = yf.Ticker(tkr)
        heads = catalyst_headlines(t, tkr, g.get("name", ""), rss_items)
        g["catalyst_headlines"] = heads
        g["catalyst_found"] = bool(heads)
        g["intraday"] = intraday_levels(t)
        g["daily"] = daily_metrics(t)
        g["next_earnings"] = next_earnings(t)
        g["day_eligible"], g["swing_eligible"] = eligibility_flags(g)

    log("[6/6] writing packet.json")
    packet = {
        "generated_at": now.isoformat(),
        "candidate_source": candidate_source,
        "trading_day_note": (
            "weekend run, data reflects the last trading session"
            if now.weekday() >= 5 else "weekday run"
        ),
        "scan_params": {
            "gap_min_abs_pct": GAP_MIN_ABS_PCT,
            "price_min": PRICE_MIN,
            "max_gappers": MAX_GAPPERS,
            "screeners": ["day_gainers", "most_actives"],
            "rss_feeds": list(RSS_FEEDS),
        },
        "criteria": {
            "day_eligible": (
                "gap > 3% and price > $3 and market cap > $1B and RVOL > 1.5 "
                "and price above prior-day high"
            ),
            "swing_eligible": (
                "gap >= 8% and price > $3 and open above prior-day high and "
                "open above 200-day SMA and market cap >= $800M and a real catalyst exists"
            ),
        },
        "market_snapshot": snapshot,
        "econ_calendar": econ,
        "gappers": gappers,
        "market_news": rss_items[:20],
        "gaps_to_fill": [
            "market-wide earnings calendar is only partial (per-gapper next_earnings only)",
            "intraday levels depend on intraday bar availability at run time",
            "premarket RVOL not available keyless: yfinance shows about 0 premarket volume, rvol is full-day relative volume",
        ],
    }
    out_path = HERE / "packet.json"
    out_path.write_text(json.dumps(packet, indent=2, default=str), encoding="utf-8")
    log(f"done: {out_path} ({len(gappers)} gappers, {len(rss_items)} news items)")


if __name__ == "__main__":
    main()
