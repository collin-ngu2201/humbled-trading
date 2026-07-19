"""radar.py: forward-indicators radar for the Humbled pipeline.

Gathers the market-regime inputs (volatility complex, rates and curve,
credit, risk-appetite ratios), sector rotation, and the forward event
radar (econ calendar + watchlist earnings), then scores the regime with
simple documented rules. Writes radar.json next to this file.

Free and keyless only, same stack as scan.py: yfinance + requests.
The econ calendar is reused from scan.py so both share one cache.

Run: python radar.py          (then: python render_radar.py, python notify_radar.py)
"""

import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yfinance as yf

from scan import load_week_events

ET = ZoneInfo("America/New_York")
HERE = Path(__file__).resolve().parent

# ---------------- config ----------------

# everything the regime score and info tiles need, one batch download
REGIME_TICKERS = [
    "^VIX", "^VIX9D", "^VIX3M", "^TNX", "^IRX",
    "SPY", "RSP", "QQQ", "IWM", "SMH",
    "HYG", "LQD", "HG=F", "GC=F", "DX-Y.NYB",
]

SECTORS = [
    ("XLK", "Technology"), ("XLC", "Communications"), ("XLY", "Cons. Discretionary"),
    ("XLF", "Financials"), ("XLI", "Industrials"), ("XLB", "Materials"),
    ("XLE", "Energy"), ("XLV", "Health Care"), ("XLP", "Cons. Staples"),
    ("XLU", "Utilities"), ("XLRE", "Real Estate"),
    ("SMH", "Semiconductors"), ("XBI", "Biotech"), ("ITA", "Defense/Aero"),
    ("IYT", "Transports"),
]

EVENT_LOOKAHEAD_DAYS = 7      # calendar days of econ events to surface
EARNINGS_LOOKAHEAD_DAYS = 21  # watchlist earnings horizon

RISK_ON, NEUTRAL, RISK_OFF = 1, 0, -1
SIGNAL_LABEL = {RISK_ON: "risk-on", NEUTRAL: "neutral", RISK_OFF: "risk-off"}


def log(msg):
    print(msg, flush=True)


# ---------------- price history ----------------

def fetch_closes(tickers):
    """Batch daily closes, one Series per ticker (missing tickers dropped)."""
    try:
        df = yf.download(tickers, period="4mo", interval="1d",
                         progress=False, auto_adjust=True)
    except Exception as e:
        log(f"  batch download failed: {e}")
        return {}
    closes = {}
    for sym in tickers:
        try:
            ser = df["Close"][sym].dropna()
            if len(ser) >= 2:
                closes[sym] = ser
            else:
                log(f"  no data for {sym}")
        except Exception:
            log(f"  no data for {sym}")
    return closes


def last(closes, sym):
    ser = closes.get(sym)
    return float(ser.iloc[-1]) if ser is not None else None


def change_pct(ser, days):
    """Percent change over the past `days` trading days."""
    if ser is None or len(ser) <= days:
        return None
    return (float(ser.iloc[-1]) / float(ser.iloc[-1 - days]) - 1.0) * 100.0


def ratio_change_pct(closes, num, den, days):
    a, b = closes.get(num), closes.get(den)
    if a is None or b is None:
        return None
    ratio = (a / b).dropna()
    return change_pct(ratio, days)


# ---------------- regime components ----------------
# Each component returns signal -1/0/+1 with the rule spelled out in
# `rule`, so the report can always show WHY the light is the color it is.

def band(value, lo, hi, invert=False):
    """+1 below lo, 0 between, -1 above hi (flipped when invert)."""
    if value is None:
        return None
    sig = RISK_ON if value < lo else (RISK_OFF if value > hi else NEUTRAL)
    return -sig if invert else sig


def trend_signal(pct, threshold):
    """+1 above +threshold, -1 below -threshold, else 0."""
    if pct is None:
        return None
    if pct > threshold:
        return RISK_ON
    if pct < -threshold:
        return RISK_OFF
    return NEUTRAL


def component(key, name, value_str, detail, signal, rule):
    return {
        "key": key,
        "name": name,
        "value": value_str,
        "detail": detail,
        "signal": signal,
        "signal_label": SIGNAL_LABEL.get(signal, "no data"),
        "rule": rule,
    }


def build_components(closes):
    comps = []

    vix = last(closes, "^VIX")
    comps.append(component(
        "vix", "VIX level",
        f"{vix:.2f}" if vix is not None else "n/a",
        f"20d ago: {closes['^VIX'].iloc[-21]:.2f}" if "^VIX" in closes and len(closes["^VIX"]) > 20 else "",
        band(vix, 16, 22),
        "<16 risk-on, 16-22 neutral, >22 risk-off",
    ))

    vix3m = last(closes, "^VIX3M")
    term = vix / vix3m if vix and vix3m else None
    comps.append(component(
        "term", "VIX term structure (VIX/VIX3M)",
        f"{term:.3f}" if term is not None else "n/a",
        f"VIX3M {vix3m:.2f}" if vix3m else "",
        band(term, 0.90, 1.00),
        "<0.90 contango risk-on, 0.90-1.00 flattening, >1.00 backwardation risk-off",
    ))

    vix9d = last(closes, "^VIX9D")
    near = vix9d / vix if vix9d and vix else None
    comps.append(component(
        "near_vol", "Near-term stress (VIX9D/VIX)",
        f"{near:.3f}" if near is not None else "n/a",
        f"VIX9D {vix9d:.2f}" if vix9d else "",
        band(near, 0.95, 1.05),
        "<0.95 near-term calm risk-on, 0.95-1.05 neutral, >1.05 event stress risk-off",
    ))

    credit = ratio_change_pct(closes, "HYG", "LQD", 20)
    comps.append(component(
        "credit", "Credit (HYG/LQD, 20d)",
        f"{credit:+.2f}%" if credit is not None else "n/a",
        "high-yield vs investment-grade",
        trend_signal(credit, 0.5),
        ">+0.5% risk-on, within +/-0.5% neutral, <-0.5% credit stress risk-off",
    ))

    breadth = ratio_change_pct(closes, "RSP", "SPY", 20)
    comps.append(component(
        "breadth", "Breadth (RSP/SPY, 20d)",
        f"{breadth:+.2f}%" if breadth is not None else "n/a",
        "equal-weight vs cap-weight participation",
        trend_signal(breadth, 0.75),
        ">+0.75% broadening risk-on, within +/-0.75% neutral, <-0.75% narrow leadership risk-off",
    ))

    semis = ratio_change_pct(closes, "SMH", "SPY", 20)
    comps.append(component(
        "semis", "Semis leadership (SMH/SPY, 20d)",
        f"{semis:+.2f}%" if semis is not None else "n/a",
        "semis lead the tech tape",
        trend_signal(semis, 0.75),
        ">+0.75% risk-on, within +/-0.75% neutral, <-0.75% risk-off",
    ))

    copper_gold = ratio_change_pct(closes, "HG=F", "GC=F", 20)
    comps.append(component(
        "copper_gold", "Copper/Gold (20d)",
        f"{copper_gold:+.2f}%" if copper_gold is not None else "n/a",
        "growth expectations vs safety bid",
        trend_signal(copper_gold, 2.0),
        ">+2% growth bid risk-on, within +/-2% neutral, <-2% safety bid risk-off",
    ))

    return comps


def composite_score(comps):
    scored = [c for c in comps if c["signal"] is not None]
    score = sum(c["signal"] for c in scored)
    if score >= 3:
        light, label = "green", "RISK-ON"
    elif score <= -3:
        light, label = "red", "RISK-OFF"
    else:
        light, label = "yellow", "MIXED"
    drivers = sorted(
        (c for c in scored if c["signal"] != 0),
        key=lambda c: (c["signal"], c["name"]),
    )
    return {
        "score": score,
        "max_score": len(scored),
        "components_scored": len(scored),
        "components_total": len(comps),
        "partial_data": len(scored) < 5,
        "light": light,
        "label": label,
        "drivers": [
            {"name": c["name"], "value": c["value"], "signal_label": c["signal_label"]}
            for c in drivers
        ],
    }


# ---------------- info tiles (context, not scored) ----------------

def tenor_yield(closes, sym):
    """^TNX / ^IRX come back from yfinance already in percent (e.g. 4.57).

    Returns (pct_now, bp_change_20d)."""
    ser = closes.get(sym)
    if ser is None:
        return None, None
    now = float(ser.iloc[-1])
    bp = (float(ser.iloc[-1]) - float(ser.iloc[-21])) * 100.0 if len(ser) > 20 else None
    return round(now, 2), round(bp, 0) if bp is not None else None


def build_info(closes):
    y10, y10_bp = tenor_yield(closes, "^TNX")
    y3m, _ = tenor_yield(closes, "^IRX")
    curve = round(y10 - y3m, 2) if y10 is not None and y3m is not None else None
    return {
        "ten_year_pct": y10,
        "ten_year_20d_bp": y10_bp,
        "three_month_pct": y3m,
        "curve_10y_3m": curve,
        "qqq_iwm_5d_pct": rnd(ratio_change_pct(closes, "QQQ", "IWM", 5)),
        "qqq_iwm_20d_pct": rnd(ratio_change_pct(closes, "QQQ", "IWM", 20)),
        "dxy_20d_pct": rnd(change_pct(closes.get("DX-Y.NYB"), 20)),
        "spy_5d_pct": rnd(change_pct(closes.get("SPY"), 5)),
        "spy_20d_pct": rnd(change_pct(closes.get("SPY"), 20)),
    }


def rnd(v, places=2):
    return round(v, places) if v is not None else None


# ---------------- sector rotation ----------------

def sector_rotation(closes):
    spy = closes.get("SPY")
    rows = []
    for sym, name in SECTORS:
        ser = closes.get(sym)
        row = {"ticker": sym, "name": name}
        for label, days in (("rel_1d", 1), ("rel_5d", 5), ("rel_21d", 21)):
            own, base = change_pct(ser, days), change_pct(spy, days)
            row[label] = rnd(own - base) if own is not None and base is not None else None
        rows.append(row)
    rows.sort(key=lambda r: r["rel_5d"] if r["rel_5d"] is not None else -999, reverse=True)
    return rows


# ---------------- event radar ----------------

def upcoming_events():
    """USD high-impact events for the next EVENT_LOOKAHEAD_DAYS calendar days.

    The ForexFactory feed only covers the current week, so late-week runs
    see a short horizon; coverage_through says how far the data actually goes.
    """
    today = datetime.now(ET).date()
    horizon = today + timedelta(days=EVENT_LOOKAHEAD_DAYS)
    out = {"events": [], "note": "", "coverage_through": None}
    events, note = load_week_events()
    out["note"] = note
    if events is None:
        out["error"] = note
        return out
    all_dates = []
    for ev in events:
        try:
            dt = datetime.fromisoformat(ev["date"]).astimezone(ET)
            all_dates.append(dt.date())
            if ev.get("country") != "USD" or ev.get("impact") != "High":
                continue
            if today <= dt.date() <= horizon:
                out["events"].append({
                    "date": str(dt.date()),
                    "weekday": dt.strftime("%a"),
                    "time_et": dt.strftime("%H:%M"),
                    "days_until": (dt.date() - today).days,
                    "title": ev.get("title", ""),
                    "forecast": ev.get("forecast", ""),
                    "previous": ev.get("previous", ""),
                })
        except Exception:
            continue
    out["events"].sort(key=lambda e: (e["date"], e["time_et"]))
    if all_dates:
        out["coverage_through"] = str(max(all_dates))
    return out


def watchlist_earnings():
    """Earnings dates for the last scan's gappers (packet.json), soonest first."""
    packet_path = HERE / "packet.json"
    if not packet_path.exists():
        return {"rows": [], "note": "packet.json not found, run scan.py first"}
    try:
        packet = json.loads(packet_path.read_text(encoding="utf-8"))
    except Exception as e:
        return {"rows": [], "note": f"packet.json unreadable: {e}"}
    today = datetime.now(ET).date()
    rows = []
    for g in packet.get("gappers", []):
        raw = g.get("next_earnings")
        if not raw:
            continue
        try:
            d = datetime.fromisoformat(str(raw)[:10]).date()
        except ValueError:
            continue
        days = (d - today).days
        if 0 <= days <= EARNINGS_LOOKAHEAD_DAYS:
            rows.append({
                "ticker": g["ticker"],
                "date": str(d),
                "days_until": days,
                "gap_pct_at_scan": g.get("gap_pct"),
            })
    rows.sort(key=lambda r: r["days_until"])
    scan_date = str(packet.get("generated_at", ""))[:10]
    return {"rows": rows, "note": f"from scan.py packet of {scan_date}"}


# ---------------- main ----------------

def main():
    now = datetime.now(ET)
    log(f"radar.py starting {now:%Y-%m-%d %H:%M} ET")

    log("[1/4] regime instruments")
    all_tickers = REGIME_TICKERS + [s for s, _ in SECTORS if s not in REGIME_TICKERS]
    closes = fetch_closes(all_tickers)
    log(f"  {len(closes)}/{len(all_tickers)} instruments with data")

    log("[2/4] regime score")
    comps = build_components(closes)
    regime = composite_score(comps)
    log(f"  {regime['label']} (score {regime['score']:+d} of {regime['max_score']})")

    log("[3/4] sector rotation")
    sectors = sector_rotation(closes)

    log("[4/4] event radar")
    events = upcoming_events()
    earnings = watchlist_earnings()
    log(f"  {len(events['events'])} econ events, {len(earnings['rows'])} watchlist earnings")

    radar = {
        "generated_at": now.isoformat(),
        "regime": regime,
        "components": comps,
        "info": build_info(closes),
        "sectors": sectors,
        "econ_events": events,
        "watchlist_earnings": earnings,
        "method_notes": [
            "Composite: sum of component signals; >=+3 RISK-ON, <=-3 RISK-OFF, else MIXED",
            "Ratio trends use 20 trading days; sector rotation is return minus SPY return",
            "All data keyless via yfinance; futures ratios (copper/gold) are noisier than ETF ratios",
        ],
    }
    out_path = HERE / "radar.json"
    out_path.write_text(json.dumps(radar, indent=2, default=str), encoding="utf-8")
    log(f"done: {out_path}")


if __name__ == "__main__":
    main()
