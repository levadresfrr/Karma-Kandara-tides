#!/usr/bin/env python3
"""
Updates tides-data.json automatically:
  1. Reads the list of published tide chart PDFs from indosurf.com.au/tide-charts/
  2. Downloads any month not yet present in tides-data.json
  3. Parses the PDF text into the same {day: [{t,h,state}, ...]} format used by the site
  4. Removes months that have fully passed (nobody needs last month's tide chart)
  5. Writes the updated tides-data.json back

Run manually with:  python3 update_tides.py
Run automatically via the GitHub Actions workflow in .github/workflows/update-tides.yml
"""

import re
import json
import sys
from datetime import date
from pathlib import Path

import requests
import pdfplumber
import io

LISTING_URL = "https://indosurf.com.au/tide-charts/"
DATA_FILE = Path(__file__).parent.parent / "tides-data.json"

MONTH_NAME_TO_NUM = {
    "JANUARY": 1, "FEBRUARY": 2, "MARCH": 3, "APRIL": 4, "MAY": 5, "JUNE": 6,
    "JULY": 7, "AUGUST": 8, "SEPTEMBER": 9, "SEPT": 9, "OCTOBER": 10,
    "NOVEMBER": 11, "DECEMBER": 12,
}

EVENT_RE = re.compile(
    r"(High|Low)\s+(\d{1,2}):(\d{2})\s*(AM|PM)\s+(-?[\d.]+)\s*m", re.IGNORECASE
)
DATE_RE = re.compile(r"^(\d{1,2})/(\d{2})/(\d{4})")


def fetch_listing():
    """Return list of (year, month, pdf_url) for every 'BALI TIDE CHART ...' link found."""
    resp = requests.get(LISTING_URL, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    html = resp.text
    # Links look like: <a href="...pdf">BALI TIDE CHART SEPTEMBER 2026</a>
    pattern = re.compile(
        r'href="([^"]+\.pdf)"[^>]*>\s*BALI TIDE CHART\s+([A-Z]+)\s+(\d{4})',
        re.IGNORECASE,
    )
    results = []
    for m in pattern.finditer(html):
        url, month_name, year = m.group(1), m.group(2).upper(), int(m.group(3))
        month_num = MONTH_NAME_TO_NUM.get(month_name)
        if month_num:
            results.append((year, month_num, url))
    return results


def parse_pdf_text(text):
    """Parse IndoSurf & Lingo tide chart PDF text into {day: [events]} dict."""
    days = {}
    current_day = None
    for line in text.splitlines():
        line = line.strip()
        date_match = DATE_RE.match(line)
        if date_match:
            current_day = str(int(date_match.group(1)))
            days.setdefault(current_day, [])
            continue
        event_match = EVENT_RE.search(line)
        if event_match and current_day is not None:
            state = "high" if event_match.group(1).lower() == "high" else "low"
            hh, mm, ampm = int(event_match.group(2)), int(event_match.group(3)), event_match.group(4).upper()
            if ampm == "AM":
                hh = 0 if hh == 12 else hh
            else:
                hh = 12 if hh == 12 else hh + 12
            t = round(hh + mm / 60, 4)
            h = float(event_match.group(5))
            days[current_day].append({"t": t, "h": h, "state": state})
    for d in days:
        days[d].sort(key=lambda e: e["t"])
    return days


def download_and_parse(url):
    resp = requests.get(url, timeout=60, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
        text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    return parse_pdf_text(text)


def month_key(year, month):
    return f"{year:04d}-{month:02d}"


def main():
    data = {}
    if DATA_FILE.exists():
        data = json.loads(DATA_FILE.read_text())

    today = date.today()
    current_key = month_key(today.year, today.month)

    # 1. Drop months that have fully passed (keep current month onward)
    before = set(data.keys())
    data = {k: v for k, v in data.items() if k >= current_key}
    removed = before - set(data.keys())
    if removed:
        print(f"Removed past months: {sorted(removed)}")

    # 2. Check what's published and add anything new
    try:
        listing = fetch_listing()
    except Exception as e:
        print(f"Could not fetch listing page: {e}", file=sys.stderr)
        listing = []

    added = []
    for year, month, url in listing:
        key = month_key(year, month)
        if key < current_key:
            continue  # don't bother re-adding old months
        if key in data:
            continue  # already have it
        try:
            print(f"Fetching new month {key} from {url} ...")
            parsed = download_and_parse(url)
            if parsed:
                data[key] = parsed
                added.append(key)
            else:
                print(f"  WARNING: parsed 0 days for {key}, skipping")
        except Exception as e:
            print(f"  ERROR fetching/parsing {key}: {e}", file=sys.stderr)

    if added:
        print(f"Added new months: {sorted(added)}")
    else:
        print("No new months to add.")

    DATA_FILE.write_text(json.dumps(data, separators=(",", ":"), sort_keys=True))
    print(f"tides-data.json now covers: {sorted(data.keys())}")

    # Exit code 0 always -- the workflow decides whether to commit based on git diff,
    # not based on this script's exit code.


if __name__ == "__main__":
    main()
