"""notify_radar.py: push the Forward Radar summary to Telegram.

Usage: python notify_radar.py [--dry-run]
Reads TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID from .env next to this
script (real environment variables win), same pattern as deliver.py.
If keys are missing it prints a skip line and exits cleanly.
--dry-run prints the message instead of sending it.

Setup: create a bot with @BotFather to get the token, message the bot
once, then read your chat id from
https://api.telegram.org/bot<TOKEN>/getUpdates
"""

import json
import os
import sys
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
ENV_KEYS = ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")
MAX_LEN = 4000  # Telegram hard limit is 4096, leave headroom

LIGHT_EMOJI = {"green": "\U0001f7e2", "yellow": "\U0001f7e1", "red": "\U0001f534"}
SIGNAL_EMOJI = {"risk-on": "▲", "neutral": "●", "risk-off": "▼"}


def load_env():
    """Tiny KEY=VALUE parser for .env; the real environment wins."""
    values = {}
    env_file = HERE / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            values[key.strip()] = val.strip().strip('"').strip("'")
    for key in ENV_KEYS:
        if os.environ.get(key):
            values[key] = os.environ[key]
    return values


def build_message(radar):
    reg = radar["regime"]
    date = str(radar.get("generated_at", ""))[:10]
    lines = [
        f"{LIGHT_EMOJI.get(reg['light'], '')} *Forward Radar* — {date}",
        f"*{reg['label']}* (composite {reg['score']:+d} of ±{reg['max_score']})",
        "",
    ]

    lines.append("*Components*")
    for c in radar.get("components", []):
        mark = SIGNAL_EMOJI.get(c["signal_label"], "○")
        lines.append(f"{mark} {c['name']}: {c['value']}")

    info = radar.get("info", {})
    y10, curve = info.get("ten_year_pct"), info.get("curve_10y_3m")
    ctx = []
    if y10 is not None:
        bp = info.get("ten_year_20d_bp")
        ctx.append(f"10Y {y10}%" + (f" ({bp:+.0f}bp/20d)" if bp is not None else ""))
    if curve is not None:
        ctx.append(f"curve {curve:+.2f}")
    if ctx:
        lines += ["", "_" + ", ".join(ctx) + "_"]

    sectors = radar.get("sectors", [])
    if sectors:
        best = [s for s in sectors if s.get("rel_5d") is not None]
        if best:
            top, bottom = best[0], best[-1]
            lines += ["", "*Rotation (5d vs SPY)*",
                      f"Leader: {top['ticker']} {top['rel_5d']:+.2f}%",
                      f"Laggard: {bottom['ticker']} {bottom['rel_5d']:+.2f}%"]

    events = radar.get("econ_events", {}).get("events", [])
    if events:
        lines += ["", "*Next events*"]
        for e in events[:3]:
            when = "today" if e["days_until"] == 0 else f"in {e['days_until']}d"
            lines.append(f"• {e['weekday']} {e['time_et']} ET ({when}): {e['title']}")

    earn = radar.get("watchlist_earnings", {}).get("rows", [])
    if earn:
        lines += ["", "*Watchlist earnings*"]
        for r in earn[:4]:
            lines.append(f"• {r['ticker']} in {r['days_until']}d ({r['date']})")

    lines += ["", "_Educational only, not financial advice_"]
    msg = "\n".join(lines)
    return msg[:MAX_LEN]


def main():
    dry_run = "--dry-run" in sys.argv
    json_path = HERE / "radar.json"
    if not json_path.is_file():
        print("radar.json not found, run radar.py first")
        return 2
    radar = json.loads(json_path.read_text(encoding="utf-8"))
    message = build_message(radar)

    if dry_run:
        print(message)
        return 0

    env = load_env()
    token, chat_id = env.get("TELEGRAM_BOT_TOKEN"), env.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("telegram skipped, set TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID (.env or environment)")
        return 0

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    # calendar titles are external text; if they break Markdown parsing,
    # retry once as plain text rather than dropping the whole push
    for parse_mode in ("Markdown", None):
        payload = {"chat_id": chat_id, "text": message}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        try:
            r = requests.post(url, json=payload, timeout=30)
        except Exception as e:
            print(f"telegram failed: {e}")
            return 1
        body = {}
        try:
            body = r.json()
        except ValueError:
            pass
        if r.ok and body.get("ok"):
            print(f"telegram sent to chat {chat_id}")
            return 0
        if r.status_code != 400:
            break
    print(f"telegram failed: HTTP {r.status_code} {str(body or r.text)[:300]}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
