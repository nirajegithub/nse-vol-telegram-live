import json
import os
import subprocess
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

IST = ZoneInfo("Asia/Kolkata")
NSE_BASE_URL = "https://www.nseindia.com"
VOLUME_GAINERS_URL = f"{NSE_BASE_URL}/api/live-analysis-volume-gainers"
TELEGRAM_API_URL = "https://api.telegram.org"

STATE_FILE = Path("telegram_state.json")
HOLIDAY_FILE = Path("nse_holidays.json")

# Worker accepts scheduled checks from 09:22 through 15:17 IST.
MARKET_START_MINUTES = 9 * 60 + 15
MARKET_END_MINUTES = 15 * 60 + 18

TELEGRAM_MIN_VOLUME = 500_000
TELEGRAM_MIN_PRICE = 250.0
REQUEST_TIMEOUT = 15

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": f"{NSE_BASE_URL}/market-data/volume-gainers",
}


def log(message):
    print(
        f"[{datetime.now(IST):%Y-%m-%d %H:%M:%S} IST] {message}",
        flush=True,
    )


def create_nse_session():
    session = requests.Session()
    session.headers.update(HEADERS)

    home = session.get(NSE_BASE_URL, timeout=REQUEST_TIMEOUT)
    home.raise_for_status()

    time.sleep(0.5)

    page = session.get(
        f"{NSE_BASE_URL}/market-data/volume-gainers",
        timeout=REQUEST_TIMEOUT,
    )
    page.raise_for_status()

    time.sleep(0.5)
    return session


def fetch_volume_gainers():
    session = create_nse_session()

    response = session.get(
        VOLUME_GAINERS_URL,
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()

    payload = response.json()
    rows = payload.get("data", [])

    if not isinstance(rows, list):
        raise RuntimeError(
            "NSE Volume Gainers response did not contain a data list."
        )

    return rows


def apply_telegram_filters(rows):
    filtered = []

    for row in rows:
        try:
            symbol = str(row.get("symbol") or "").strip().upper()
            price = float(row.get("ltp") or 0)
            volume = float(row.get("volume") or 0)

            if (
                symbol
                and volume >= TELEGRAM_MIN_VOLUME
                and price >= TELEGRAM_MIN_PRICE
            ):
                filtered.append(row)

        except (TypeError, ValueError):
            continue

    return filtered


def load_holidays():
    if not HOLIDAY_FILE.exists():
        raise RuntimeError("nse_holidays.json is missing.")

    with HOLIDAY_FILE.open("r", encoding="utf-8") as f:
        data = json.load(f)

    if data.get("market") != "CM":
        raise RuntimeError(
            "nse_holidays.json must contain market='CM'."
        )

    return {str(x) for x in data.get("holidays", [])}


def is_monitoring_window(now):
    minutes = now.hour * 60 + now.minute

    return (
        MARKET_START_MINUTES
        <= minutes
        < MARKET_END_MINUTES
    )


def is_trading_day(now):
    if now.weekday() >= 5:
        log("Weekend - skipping.")
        return False

    today = now.strftime("%Y-%m-%d")

    if today in load_holidays():
        log(f"NSE CM holiday - skipping: {today}")
        return False

    return True


def load_state(today):
    default = {
        "date": today,
        "notified_symbols": [],
        "baseline_initialized": False,
    }

    if not STATE_FILE.exists():
        return default

    try:
        with STATE_FILE.open("r", encoding="utf-8") as f:
            state = json.load(f)

    except Exception as exc:
        log(f"Invalid state file; resetting: {exc}")
        return default

    if state.get("date") != today:
        log(
            "New trading date detected. "
            f"Resetting state from {state.get('date')} to {today}."
        )
        return default

    return {
        "date": today,
        "notified_symbols": sorted(
            set(state.get("notified_symbols", []))
        ),
        "baseline_initialized": bool(
            state.get("baseline_initialized", False)
        ),
    }


def save_state(state):
    tmp = STATE_FILE.with_suffix(".tmp")

    with tmp.open("w", encoding="utf-8") as f:
        json.dump(
            state,
            f,
            indent=2,
            ensure_ascii=False,
        )
        f.write("\n")

    tmp.replace(STATE_FILE)


def get_telegram_config():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID "
            "secrets are required."
        )

    return token, chat_id


def fmt_price(value):
    try:
        return f"₹{float(value):,.2f}"
    except (TypeError, ValueError):
        return "N/A"


def fmt_number(value):
    try:
        return f"{float(value):,.0f}"
    except (TypeError, ValueError):
        return "N/A"


def fmt_percent(value):
    try:
        return f"{float(value):+.2f}%"
    except (TypeError, ValueError):
        return "N/A"


def build_message(row, detected_at):
    symbol = str(
        row.get("symbol") or "N/A"
    ).strip().upper()

    return (
        "🚨 <b>New NSE Volume Gainer</b>\n\n"
        f"📌 <b>Symbol:</b> {symbol}\n"
        f"💰 <b>Price:</b> {fmt_price(row.get('ltp'))}\n"
        f"📈 <b>Price Change:</b> "
        f"{fmt_percent(row.get('pChange'))}\n"
        f"📊 <b>Volume:</b> "
        f"{fmt_number(row.get('volume'))}\n"
        f"🔥 <b>Volume Spike vs 1wk:</b> "
        f"{fmt_percent(row.get('week1volChange'))}\n"
        f"🔥 <b>Volume Spike vs 2wk:</b> "
        f"{fmt_percent(row.get('week2volChange'))}\n\n"
        f"🕒 <b>Detected:</b> {detected_at} IST\n\n"
        "⚠️ <b>Disclaimer:</b>\n"
        "This is an automated NSE market-data alert for "
        "informational purposes only. It is not investment advice, "
        "a buy/sell recommendation, or a trading signal. "
        "Please do your own research and risk assessment before "
        "making any investment or trading decision."
    )


def send_telegram(message):
    token, chat_id = get_telegram_config()

    response = requests.post(
        f"{TELEGRAM_API_URL}/bot{token}/sendMessage",
        json={
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=REQUEST_TIMEOUT,
    )

    response.raise_for_status()

    result = response.json()

    if not result.get("ok"):
        raise RuntimeError(
            f"Telegram API error: {result}"
        )


def commit_state():
    if os.getenv("GITHUB_ACTIONS") != "true":
        log("Local run: skipping git commit.")
        return

    subprocess.run(
        [
            "git",
            "config",
            "user.name",
            "github-actions[bot]",
        ],
        check=True,
    )

    subprocess.run(
        [
            "git",
            "config",
            "user.email",
            "41898282+github-actions[bot]"
            "@users.noreply.github.com",
        ],
        check=True,
    )

    subprocess.run(
        ["git", "add", str(STATE_FILE)],
        check=True,
    )

    diff = subprocess.run(
        ["git", "diff", "--cached", "--quiet"]
    )

    if diff.returncode == 0:
        log("No state change to commit.")
        return

    subprocess.run(
        [
            "git",
            "commit",
            "-m",
            "Update Telegram notification state",
        ],
        check=True,
    )

    subprocess.run(
        ["git", "push"],
        check=True,
    )

    log("State committed and pushed.")


def run():
    now = datetime.now(IST)

    log("NSE Telegram Volume Gainer worker started.")
    log(
        f"Current time: "
        f"{now:%Y-%m-%d %H:%M:%S} IST"
    )

    if not is_trading_day(now):
        return

    if not is_monitoring_window(now):
        log(
            "Outside 09:15-15:17 IST "
            "monitoring window - skipping."
        )
        return

    today = now.strftime("%Y-%m-%d")
    state = load_state(today)
    notified = set(state["notified_symbols"])

    rows = fetch_volume_gainers()

    log(
        f"NSE Volume Gainers rows from NSE: "
        f"{len(rows)}"
    )

    filtered_rows = apply_telegram_filters(rows)

    log(
        f"Telegram filters: "
        f"Volume >= {TELEGRAM_MIN_VOLUME:,}, "
        f"Price >= ₹{TELEGRAM_MIN_PRICE:,.2f}"
    )

    log(
        f"Eligible Telegram rows: "
        f"{len(filtered_rows)}"
    )

    current_symbols = {
        str(row.get("symbol") or "").strip().upper()
        for row in filtered_rows
        if str(row.get("symbol") or "").strip()
    }

    # First successful run of every trading day creates
    # the daily baseline. Existing qualifying symbols are
    # not alerted on that first run.
    if not state.get("baseline_initialized", False):
        state["date"] = today
        state["notified_symbols"] = sorted(
            current_symbols
        )
        state["baseline_initialized"] = True

        save_state(state)
        commit_state()

        log(
            "Daily filtered baseline initialized with "
            f"{len(current_symbols)} symbols. "
            "No alerts sent on baseline run."
        )

        return

    new_rows = []

    for row in filtered_rows:
        symbol = str(
            row.get("symbol") or ""
        ).strip().upper()

        if symbol and symbol not in notified:
            new_rows.append(row)

    log(
        f"New qualifying symbols: "
        f"{len(new_rows)}"
    )

    state_changed = False
    detected_at = now.strftime("%I:%M %p")

    for row in new_rows:
        symbol = str(
            row.get("symbol") or ""
        ).strip().upper()

        try:
            send_telegram(
                build_message(
                    row,
                    detected_at,
                )
            )

            notified.add(symbol)
            state_changed = True

            log(
                f"Telegram sent: {symbol}"
            )

        except Exception as exc:
            log(
                f"Telegram failed for "
                f"{symbol}: {exc}"
            )

    if state_changed:
        state["date"] = today
        state["notified_symbols"] = sorted(
            notified
        )
        state["baseline_initialized"] = True

        save_state(state)
        commit_state()

    else:
        log(
            "No new Telegram notifications."
        )


if __name__ == "__main__":
    run()
