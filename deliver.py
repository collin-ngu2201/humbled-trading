"""deliver.py: email the HTML premarket report to your inbox via Resend.

Usage: python deliver.py reports/premarket_YYYY-MM-DD.html

Reads RESEND_API_KEY and EMAIL_TO from a .env file next to this script.
Real environment variables win over the file. Optional EMAIL_FROM.
If keys are missing it prints a skip line and exits cleanly, never crashes.
"""

import os
import re
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

HERE = Path(__file__).resolve().parent
ENV_KEYS = ("RESEND_API_KEY", "EMAIL_TO", "EMAIL_FROM")
DEFAULT_FROM = "AI Premarket Analyst <onboarding@resend.dev>"
RESEND_URL = "https://api.resend.com/emails"


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


def report_date(path):
    """Date from the filename (premarket_YYYY-MM-DD.html), else today in ET."""
    m = re.search(r"(\d{4}-\d{2}-\d{2})", path.name)
    return m.group(1) if m else datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")


def main():
    if len(sys.argv) != 2:
        print("usage: python deliver.py reports/premarket_YYYY-MM-DD.html")
        return 2
    path = Path(sys.argv[1])
    if not path.is_file():
        print(f"report not found: {path}")
        return 2

    env = load_env()
    api_key, to = env.get("RESEND_API_KEY"), env.get("EMAIL_TO")
    if not api_key or not to:
        print("email skipped, set RESEND_API_KEY + EMAIL_TO (.env or environment)")
        return 0

    subject = f"AI Premarket Report - {report_date(path)}"
    try:
        r = requests.post(
            RESEND_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "from": env.get("EMAIL_FROM", DEFAULT_FROM),
                "to": [to],
                "subject": subject,
                "html": path.read_text(encoding="utf-8"),
            },
            timeout=30,
        )
    except Exception as e:
        print(f"email failed: {e}")
        return 1

    if r.ok:
        try:
            msg_id = r.json().get("id", "?")
        except ValueError:
            msg_id = "?"
        print(f"email sent to {to}: {subject} (id {msg_id})")
        return 0
    print(f"email failed: HTTP {r.status_code} {r.text[:300]}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
