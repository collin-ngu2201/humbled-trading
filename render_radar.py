"""render_radar.py: turn radar.json into the Forward Radar dashboard page.

Usage: python render_radar.py [radar.json] [YYYY-MM-DD]
Writes reports/radar_<date>.html and refreshes reports/radar.html (latest).

Styling matches the premarket report pages. Status colors always ship with
an icon + word so meaning never rides on color alone; the sector heatmap
prints every value and uses a blue/red diverging fill around a gray zero.
"""

import html
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
ET = ZoneInfo("America/New_York")

# diverging poles for the heatmap fills (values are printed in every cell,
# so the fill is a redundant cue, capped light enough for dark text)
POS_RGB, NEG_RGB = (42, 120, 214), (227, 73, 72)   # blue outperform, red lag
FILL_MAX_ALPHA, FILL_SCALE_PCT = 0.42, 4.0

SIGNAL_STYLE = {
    "risk-on": ("&#9650;", "sig-on"),      # ▲
    "neutral": ("&#9679;", "sig-neutral"), # ●
    "risk-off": ("&#9660;", "sig-off"),    # ▼
    "no data": ("&#9711;", "sig-na"),      # ◯
}

REGIME_STYLE = {
    "green": ("&#9650;", "regime-on"),
    "yellow": ("&#9679;", "regime-mixed"),
    "red": ("&#9660;", "regime-off"),
}

CSS = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body {
  margin: 0;
  background: #f5f6f8;
  color: #1f2430;
  font: 16px/1.65 -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
}
.page { max-width: 980px; margin: 0 auto; padding: 32px 24px 48px; }
header.report { border-bottom: 2px solid #d8dce3; padding-bottom: 16px; margin-bottom: 24px; }
header.report h1 { margin: 0 0 4px; font-size: 1.7em; letter-spacing: -0.01em; }
header.report .date { color: #5b6472; font-size: 1.05em; }
h2 { margin-top: 2em; padding-bottom: 6px; border-bottom: 1px solid #e1e4ea; font-size: 1.3em; line-height: 1.3; }
.banner { border-radius: 10px; padding: 18px 22px; margin: 4px 0 8px; border: 1px solid; }
.banner .headline { font-size: 1.45em; font-weight: 700; letter-spacing: 0.01em; }
.banner .score { color: #52514e; margin-top: 2px; }
.banner .drivers { margin-top: 8px; font-size: 0.95em; }
.regime-on { background: rgba(12,163,12,0.10); border-color: rgba(12,163,12,0.45); }
.regime-on .headline { color: #006300; }
.regime-mixed { background: rgba(250,178,25,0.14); border-color: rgba(201,133,0,0.5); }
.regime-mixed .headline { color: #6e4e00; }
.regime-off { background: rgba(208,59,59,0.10); border-color: rgba(208,59,59,0.5); }
.regime-off .headline { color: #a32424; }
.tiles { display: flex; flex-wrap: wrap; gap: 10px; margin: 14px 0; }
.tile {
  flex: 1 1 150px; min-width: 150px; background: #fff; border: 1px solid #e1e4ea;
  border-radius: 8px; padding: 10px 14px;
}
.tile .label { color: #5b6472; font-size: 0.82em; }
.tile .value { font-size: 1.25em; font-weight: 650; margin-top: 2px; }
.tile .sub { color: #898781; font-size: 0.8em; }
table { border-collapse: collapse; width: 100%; margin: 1em 0; font-size: 0.95em; background: #fff; }
th { background: #eef1f5; text-align: left; font-weight: 600; color: #333b49; }
th, td { padding: 9px 12px; border-bottom: 1px solid #e4e7ec; vertical-align: top; }
tbody tr:nth-child(even) { background: #f8f9fb; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
.rule { color: #898781; font-size: 0.82em; }
.sig { font-weight: 600; white-space: nowrap; }
.sig-on { color: #006300; }
.sig-off { color: #b02f2f; }
.sig-neutral { color: #52514e; }
.sig-na { color: #898781; font-weight: 400; }
.heat td.cell { text-align: right; font-variant-numeric: tabular-nums; }
.note { color: #5b6472; font-size: 0.9em; }
.empty { color: #5b6472; font-style: italic; }
footer.report { margin-top: 40px; padding-top: 14px; border-top: 1px solid #d8dce3; color: #6a7280; font-size: 0.88em; }
@media (max-width: 640px) { .banner .headline { font-size: 1.2em; } }
"""

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Forward Radar | {date}</title>
<style>{css}</style>
</head>
<body>
<div class="page">
<header class="report">
  <h1>&#128225; Forward Radar</h1>
  <div class="date">{date} &middot; regime, rotation and what can hit you next</div>
</header>
{body}
<footer class="report">{footer}</footer>
</div>
</body>
</html>
"""


def esc(s):
    return html.escape(str(s)) if s is not None else ""


def fmt(v, suffix="", plus=False, dash="&ndash;"):
    if v is None:
        return dash
    return f"{v:+.2f}{suffix}" if plus else f"{v}{suffix}"


def heat_style(v):
    if v is None:
        return ""
    r, g, b = POS_RGB if v >= 0 else NEG_RGB
    alpha = min(abs(v) / FILL_SCALE_PCT, 1.0) * FILL_MAX_ALPHA
    return f' style="background: rgba({r},{g},{b},{alpha:.2f})"'


def signal_html(label):
    icon, cls = SIGNAL_STYLE.get(label, SIGNAL_STYLE["no data"])
    return f'<span class="sig {cls}">{icon} {esc(label)}</span>'


# ---------------- sections ----------------

def regime_banner(radar):
    reg = radar["regime"]
    icon, cls = REGIME_STYLE.get(reg["light"], REGIME_STYLE["yellow"])
    drivers = reg.get("drivers", [])
    if drivers:
        parts = ", ".join(f"{esc(d['name'])} ({esc(d['value'])})" for d in drivers)
        drivers_html = f'<div class="drivers"><strong>Drivers:</strong> {parts}</div>'
    else:
        drivers_html = '<div class="drivers">All components neutral.</div>'
    partial = ' <span class="note">(partial data, treat with care)</span>' if reg.get("partial_data") else ""
    return f"""
<div class="banner {cls}">
  <div class="headline">{icon} {esc(reg['label'])}</div>
  <div class="score">Composite {reg['score']:+d} of &plusmn;{reg['max_score']}
  ({reg['components_scored']}/{reg['components_total']} components scored){partial}</div>
  {drivers_html}
</div>"""


def components_table(radar):
    rows = []
    for c in radar["components"]:
        detail = f' <span class="rule">{esc(c["detail"])}</span>' if c.get("detail") else ""
        rows.append(
            f"<tr><td>{esc(c['name'])}</td>"
            f"<td class='num'>{esc(c['value'])}{detail}</td>"
            f"<td>{signal_html(c['signal_label'])}</td>"
            f"<td class='rule'>{esc(c['rule'])}</td></tr>"
        )
    return (
        "<h2>Regime components</h2>"
        "<table><thead><tr><th>Component</th><th class='num'>Value</th>"
        "<th>Signal</th><th>Rule</th></tr></thead><tbody>"
        + "".join(rows) + "</tbody></table>"
    )


def context_tiles(radar):
    info = radar.get("info", {})
    y10bp = info.get("ten_year_20d_bp")
    tiles = [
        ("US 10Y yield", fmt(info.get("ten_year_pct"), "%"),
         f"{y10bp:+.0f} bp over 20d" if y10bp is not None else ""),
        ("Curve 10Y&ndash;3M", fmt(info.get("curve_10y_3m"), " pts"),
         "negative = inverted"),
        ("SPY 20d", fmt(info.get("spy_20d_pct"), "%", plus=True),
         f"5d {fmt(info.get('spy_5d_pct'), '%', plus=True)}"),
        ("QQQ/IWM 20d", fmt(info.get("qqq_iwm_20d_pct"), "%", plus=True),
         "growth vs small caps"),
        ("Dollar (DXY) 20d", fmt(info.get("dxy_20d_pct"), "%", plus=True),
         "strong dollar = headwind"),
    ]
    tile_html = "".join(
        f'<div class="tile"><div class="label">{t}</div>'
        f'<div class="value">{v}</div><div class="sub">{s}</div></div>'
        for t, v, s in tiles
    )
    return f"<h2>Context (not scored)</h2><div class='tiles'>{tile_html}</div>"


def sectors_table(radar):
    rows = []
    for s in radar.get("sectors", []):
        cells = "".join(
            f"<td class='cell'{heat_style(s[k])}>{fmt(s[k], '%', plus=True)}</td>"
            for k in ("rel_1d", "rel_5d", "rel_21d")
        )
        rows.append(
            f"<tr><td><strong>{esc(s['ticker'])}</strong> "
            f"<span class='rule'>{esc(s['name'])}</span></td>{cells}</tr>"
        )
    return (
        "<h2>Sector rotation vs SPY</h2>"
        "<p class='note'>Return minus SPY return, sorted by 5-day. "
        "Blue = outperforming, red = lagging; leadership rotating out of a "
        "group is the early tell for the names inside it.</p>"
        "<table class='heat'><thead><tr><th>Sector</th><th class='num'>1d</th>"
        "<th class='num'>5d</th><th class='num'>21d</th></tr></thead><tbody>"
        + "".join(rows) + "</tbody></table>"
    )


def events_table(radar):
    ev = radar.get("econ_events", {})
    events = ev.get("events", [])
    head = "<h2>Event radar &middot; next 7 days</h2>"
    cover = ev.get("coverage_through")
    cover_note = (
        f"<p class='note'>Calendar coverage through {esc(cover)} "
        "(ForexFactory weekly feed); days beyond that are not visible yet.</p>"
        if cover else ""
    )
    if not events:
        return head + cover_note + "<p class='empty'>No high-impact US events in view.</p>"
    rows = "".join(
        f"<tr><td>{esc(e['weekday'])} {esc(e['date'])}</td>"
        f"<td class='num'>{esc(e['time_et'])}</td>"
        f"<td class='num'>{e['days_until']}</td>"
        f"<td>{esc(e['title'])}</td>"
        f"<td class='num'>{esc(e['forecast']) or '&ndash;'}</td>"
        f"<td class='num'>{esc(e['previous']) or '&ndash;'}</td></tr>"
        for e in events
    )
    return (
        head
        + "<table><thead><tr><th>Date</th><th class='num'>Time ET</th>"
        "<th class='num'>In (days)</th><th>Event</th><th class='num'>Forecast</th>"
        "<th class='num'>Previous</th></tr></thead><tbody>" + rows + "</tbody></table>"
        + cover_note
    )


def earnings_table(radar):
    we = radar.get("watchlist_earnings", {})
    rows_data = we.get("rows", [])
    head = "<h2>Watchlist earnings ahead</h2>"
    note = f"<p class='note'>{esc(we.get('note', ''))}</p>" if we.get("note") else ""
    if not rows_data:
        return head + note + "<p class='empty'>No watchlist names report in the next three weeks.</p>"
    rows = "".join(
        f"<tr><td><strong>{esc(r['ticker'])}</strong></td>"
        f"<td>{esc(r['date'])}</td><td class='num'>{r['days_until']}</td>"
        f"<td class='num'>{fmt(r.get('gap_pct_at_scan'), '%', plus=True)}</td></tr>"
        for r in rows_data
    )
    return (
        head
        + "<table><thead><tr><th>Ticker</th><th>Earnings date</th>"
        "<th class='num'>In (days)</th><th class='num'>Gap at scan</th></tr></thead>"
        "<tbody>" + rows + "</tbody></table>" + note
    )


def method_notes(radar):
    notes = radar.get("method_notes", [])
    if not notes:
        return ""
    items = "".join(f"<li>{esc(n)}</li>" for n in notes)
    return f"<h2>Method</h2><ul class='note'>{items}</ul>"


# ---------------- main ----------------

def main():
    args = sys.argv[1:]
    json_path = Path(args[0]) if args else HERE / "radar.json"
    if not json_path.is_file():
        print(f"radar json not found: {json_path}, run radar.py first")
        return 2
    radar = json.loads(json_path.read_text(encoding="utf-8"))

    now = datetime.now(ET)
    date = args[1] if len(args) > 1 else str(radar.get("generated_at", ""))[:10] or now.strftime("%Y-%m-%d")

    body = "".join([
        regime_banner(radar),
        components_table(radar),
        context_tiles(radar),
        sectors_table(radar),
        events_table(radar),
        earnings_table(radar),
        method_notes(radar),
    ])
    footer = (
        f"Generated {now:%Y-%m-%d %H:%M} ET from radar.json of "
        f"{esc(str(radar.get('generated_at', ''))[:16].replace('T', ' '))} ET &middot; "
        "Educational only, not financial advice"
    )
    page = PAGE.format(date=date, css=CSS, body=body, footer=footer)

    out_dir = HERE / "reports"
    out_dir.mkdir(exist_ok=True)
    dated = out_dir / f"radar_{date}.html"
    dated.write_text(page, encoding="utf-8")
    (out_dir / "radar.html").write_text(page, encoding="utf-8")
    print(f"wrote {dated} and refreshed reports/radar.html")
    return 0


if __name__ == "__main__":
    sys.exit(main())
