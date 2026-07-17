"""render_report.py: turn the Markdown premarket report into a clean HTML page.

Usage: python render_report.py REPORT.md [YYYY-MM-DD]
Writes reports/premarket_<date>.html next to this script.
"""

import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import markdown

HERE = Path(__file__).resolve().parent
ET = ZoneInfo("America/New_York")

CSS = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body {
  margin: 0;
  background: #f5f6f8;
  color: #1f2430;
  font: 16px/1.65 -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
}
.page { max-width: 900px; margin: 0 auto; padding: 32px 24px 48px; }
header.report {
  border-bottom: 2px solid #d8dce3;
  padding-bottom: 16px;
  margin-bottom: 24px;
}
header.report h1 { margin: 0 0 4px; font-size: 1.7em; letter-spacing: -0.01em; }
header.report .date { color: #5b6472; font-size: 1.05em; }
h1, h2, h3 { line-height: 1.3; }
h2 {
  margin-top: 2em;
  padding-bottom: 6px;
  border-bottom: 1px solid #e1e4ea;
  font-size: 1.3em;
}
h3 { font-size: 1.08em; }
a { color: #2456a6; }
hr { border: 0; border-top: 1px solid #e1e4ea; margin: 2em 0; }
code {
  background: #edeff3;
  padding: 1px 5px;
  border-radius: 4px;
  font-size: 0.92em;
}
pre { background: #edeff3; padding: 12px 14px; border-radius: 6px; overflow-x: auto; }
pre code { background: none; padding: 0; }
blockquote {
  margin: 1em 0;
  padding: 2px 16px;
  border-left: 3px solid #c8cdd6;
  color: #4a5261;
}
table {
  border-collapse: collapse;
  width: 100%;
  margin: 1em 0;
  font-size: 0.95em;
  background: #ffffff;
}
th {
  background: #eef1f5;
  text-align: left;
  font-weight: 600;
  color: #333b49;
}
th, td { padding: 9px 12px; border-bottom: 1px solid #e4e7ec; vertical-align: top; }
tbody tr:nth-child(even) { background: #f8f9fb; }
footer.report {
  margin-top: 40px;
  padding-top: 14px;
  border-top: 1px solid #d8dce3;
  color: #6a7280;
  font-size: 0.88em;
}
"""

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} | {date}</title>
<style>{css}</style>
</head>
<body>
<div class="page">
<header class="report">
  <h1>{title}</h1>
  <div class="date">{date}</div>
</header>
{body}
<footer class="report">{footer}</footer>
</div>
</body>
</html>
"""


def main():
    if len(sys.argv) not in (2, 3):
        print("usage: python render_report.py REPORT.md [YYYY-MM-DD]")
        return 2
    md_path = Path(sys.argv[1])
    if not md_path.is_file():
        print(f"markdown report not found: {md_path}")
        return 2

    now = datetime.now(ET)
    date = sys.argv[2] if len(sys.argv) == 3 else now.strftime("%Y-%m-%d")

    body = markdown.markdown(
        md_path.read_text(encoding="utf-8"),
        extensions=["tables", "fenced_code", "sane_lists"],
    )
    footer = (
        f"Generated {now:%Y-%m-%d %H:%M} ET · Built by Claude + Codex · "
        "Educational only, not financial advice"
    )
    html = PAGE.format(
        title="AI Premarket Report", date=date, css=CSS, body=body, footer=footer
    )

    out_path = HERE / "reports" / f"premarket_{date}.html"
    out_path.parent.mkdir(exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
