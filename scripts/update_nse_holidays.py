
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

IST = ZoneInfo("Asia/Kolkata")
URL = "https://www.nseindia.com/api/holiday-master?type=trading"
OUT = Path("nse_holidays.json")
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}


def main():
    session = requests.Session()
    session.headers.update(HEADERS)
    session.get("https://www.nseindia.com", timeout=20)
    response = session.get(URL, timeout=20)
    response.raise_for_status()
    payload = response.json()

    rows = payload.get("CM")
    if not isinstance(rows, list):
        raise RuntimeError("NSE holiday response did not contain a CM list.")

    year = datetime.now(IST).year
    holidays = []
    details = []
    for row in rows:
        raw = row.get("tradingDate")
        if not raw:
            continue
        try:
            date_value = datetime.strptime(raw, "%d-%b-%Y").date()
        except ValueError:
            continue
        if date_value.year != year:
            continue
        iso = date_value.isoformat()
        holidays.append(iso)
        details.append({
            "date": iso,
            "day": row.get("weekDay"),
            "description": row.get("description"),
        })

    holidays = sorted(set(holidays))
    result = {
        "year": year,
        "market": "CM",
        "source": "NSE holiday-master trading API",
        "updated_at_ist": datetime.now(IST).isoformat(),
        "holidays": holidays,
        "holiday_details": details,
    }

    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(OUT)
    print(f"Updated {OUT} with {len(holidays)} NSE CM holiday dates for {year}.")


if __name__ == "__main__":
    main()
