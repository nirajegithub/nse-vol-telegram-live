# NSE OI Trend Finder + Telegram Volume Gainer Monitor

## Components
- `nse_bullish_oi_app.py` - Streamlit dashboard; refreshes every 15 minutes.
- `telegram_worker.py` - independent Volume Gainer monitor for Telegram.
- `nse_holidays.json` - NSE Capital Market holiday calendar used by the worker.
- `telegram_state.json` - daily notification/baseline state.
- `.github/workflows/telegram-monitor.yml` - runs the Telegram worker every 15 minutes.
- `.github/workflows/update-holidays.yml` - refreshes the NSE CM holiday JSON every January 1.
- `scripts/update_nse_holidays.py` - pulls the NSE holiday API and replaces the JSON file.

## GitHub Secrets
Create these repository secrets:
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID` = `-1002595436007`

## Telegram behavior
- Volume Gainers only.
- Monday-Friday only.
- 09:15-15:00 IST only.
- NSE CM holidays are skipped using `nse_holidays.json`.
- First valid run of each trading day creates a baseline and sends no alerts.
- Later runs send only symbols not already seen that day.
- A symbol is marked notified only after Telegram successfully accepts the message.

## Important
The GitHub Actions workflow commits `telegram_state.json` when it changes. If the Streamlit deployment is connected to this same branch/repository, state commits may cause a Streamlit redeploy depending on the hosting configuration. If that becomes undesirable, move the state to a separate branch/repository in a later hardening step.
