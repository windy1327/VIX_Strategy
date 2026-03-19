import json
import os
import smtplib
import time
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf
from dotenv import load_dotenv


load_dotenv()

STRATEGY_MODE = os.getenv("STRATEGY_MODE", "original").strip().lower()
TRADE_SYMBOL = os.getenv("TRADE_SYMBOL", "SPY").strip().upper()
LOOKBACK_PERIOD = int(os.getenv("LOOKBACK_PERIOD", "400"))
STATE_DIR = Path(os.getenv("STATE_DIR", "/app/state"))
STATE_DIR.mkdir(parents=True, exist_ok=True)
STATE_FILE = STATE_DIR / f"state_{TRADE_SYMBOL}_{STRATEGY_MODE}.json"
RUN_MODE = os.getenv("RUN_MODE", "daily").strip().lower()
RUN_ON_START = os.getenv("RUN_ON_START", "true").lower() == "true"
RUN_TIME_UTC = os.getenv("RUN_TIME_UTC", "22:30").strip()

SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USERNAME = os.getenv("SMTP_USERNAME", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_USE_TLS = os.getenv("SMTP_USE_TLS", "true").lower() == "true"
EMAIL_FROM = os.getenv("EMAIL_FROM", "")
EMAIL_TO = os.getenv("EMAIL_TO", "")
EMAIL_SUBJECT_PREFIX = os.getenv("EMAIL_SUBJECT_PREFIX", "[VIX-Strategy]")


def compute_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(100)


def load_data() -> pd.DataFrame:
    symbols = ["^VIX", "^GSPC", TRADE_SYMBOL]
    raw = yf.download(symbols, period=f"{LOOKBACK_PERIOD}d", interval="1d", auto_adjust=False, progress=False)
    if raw.empty:
        raise RuntimeError("No data downloaded from yfinance")

    if not isinstance(raw.columns, pd.MultiIndex):
        raise RuntimeError("Unexpected yfinance column format")

    close = raw["Close"].copy()
    df = pd.DataFrame(index=close.index)
    df["vix_close"] = close["^VIX"]
    df["spx_close"] = close["^GSPC"]
    df["asset_close"] = close[TRADE_SYMBOL]
    df["asset_open"] = raw["Open"][TRADE_SYMBOL]
    df["asset_high"] = raw["High"][TRADE_SYMBOL]
    df["asset_low"] = raw["Low"][TRADE_SYMBOL]

    df = df.dropna().copy()
    df["vix_ret_pct"] = df["vix_close"].pct_change() * 100
    df["spx_rsi14"] = compute_rsi(df["spx_close"], 14)
    df["vix_ma10"] = df["vix_close"].rolling(10).mean()
    df["vix_ma20"] = df["vix_close"].rolling(20).mean()
    df["vix_std20"] = df["vix_close"].rolling(20).std(ddof=0)
    df["vix_upper"] = df["vix_ma20"] + 2 * df["vix_std20"]
    df["vix_lower"] = df["vix_ma20"] - 2 * df["vix_std20"]
    return df


def latest_signal(df: pd.DataFrame) -> dict:
    row = df.iloc[-1]
    signal = "HOLD"
    reasons = []

    if STRATEGY_MODE == "original":
        vix_spike = False
        if pd.notna(row["vix_ret_pct"]) and row["vix_ret_pct"] > 9:
            vix_spike = True
            reasons.append(f"VIX单日涨幅 {row['vix_ret_pct']:.2f}% > 9%")
        if pd.notna(row["vix_ma10"]) and row["vix_close"] > row["vix_ma10"] * 1.10:
            vix_spike = True
            reasons.append("VIX > 10日均线110%")
        if pd.notna(row["vix_upper"]) and row["vix_close"] > row["vix_upper"]:
            vix_spike = True
            reasons.append("VIX突破20日布林上轨")

        buy_cond = vix_spike and row["spx_rsi14"] < 35 and row["vix_close"] > 25
        sell_cond = (
            (pd.notna(row["vix_ma20"]) and row["vix_close"] <= row["vix_ma20"])
            or (pd.notna(row["vix_ret_pct"]) and row["vix_ret_pct"] <= -9)
            or (pd.notna(row["vix_lower"]) and row["vix_close"] < row["vix_lower"])
            or (row["vix_close"] < 20)
        )

    elif STRATEGY_MODE == "highfreq":
        vix_spike = False
        if pd.notna(row["vix_ret_pct"]) and row["vix_ret_pct"] > 6:
            vix_spike = True
            reasons.append(f"VIX单日涨幅 {row['vix_ret_pct']:.2f}% > 6%")
        if pd.notna(row["vix_upper"]) and row["vix_close"] > row["vix_upper"]:
            vix_spike = True
            reasons.append("VIX突破20日布林上轨")

        buy_cond = vix_spike and row["spx_rsi14"] < 45 and row["vix_close"] > 20
        sell_cond = (
            (pd.notna(row["vix_ma10"]) and row["vix_close"] <= row["vix_ma10"])
            or (pd.notna(row["vix_ret_pct"]) and row["vix_ret_pct"] <= -6)
            or (pd.notna(row["vix_lower"]) and row["vix_close"] < row["vix_lower"])
            or (row["vix_close"] < 20)
        )
    else:
        raise ValueError(f"Unsupported STRATEGY_MODE: {STRATEGY_MODE}")

    if buy_cond:
        signal = "BUY"
    elif sell_cond:
        signal = "SELL"

    return {
        "date": str(df.index[-1].date()),
        "signal": signal,
        "symbol": TRADE_SYMBOL,
        "strategy": STRATEGY_MODE,
        "vix_close": float(row["vix_close"]),
        "spx_close": float(row["spx_close"]),
        "asset_close": float(row["asset_close"]),
        "spx_rsi14": float(row["spx_rsi14"]),
        "vix_ret_pct": None if pd.isna(row["vix_ret_pct"]) else float(row["vix_ret_pct"]),
        "reasons": reasons,
        "data_source": "yfinance",
    }


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def send_email(signal_info: dict) -> None:
    if not all([SMTP_HOST, SMTP_PORT, EMAIL_FROM, EMAIL_TO]):
        raise RuntimeError("SMTP/EMAIL env vars are incomplete")

    subject = f"{EMAIL_SUBJECT_PREFIX} {signal_info['strategy']} {signal_info['symbol']} {signal_info['signal']} {signal_info['date']}"
    body = f"""
策略: {signal_info['strategy']}
标的: {signal_info['symbol']}
日期: {signal_info['date']}
信号: {signal_info['signal']}
数据源: {signal_info['data_source']}

收盘数据:
- {signal_info['symbol']} Close: {signal_info['asset_close']:.2f}
- S&P 500 Close: {signal_info['spx_close']:.2f}
- VIX Close: {signal_info['vix_close']:.2f}
- SPX RSI14: {signal_info['spx_rsi14']:.2f}
- VIX 日涨跌幅: {signal_info['vix_ret_pct'] if signal_info['vix_ret_pct'] is not None else 'N/A'}

触发原因:
- """ + "\n- ".join(signal_info["reasons"] or ["满足该策略综合条件"]) + """
""".strip()

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = EMAIL_FROM
    msg["To"] = EMAIL_TO

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
        if SMTP_USE_TLS:
            server.starttls()
        if SMTP_USERNAME:
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
        server.sendmail(EMAIL_FROM, [EMAIL_TO], msg.as_string())


def should_notify(prev: dict, curr: dict) -> bool:
    if curr["signal"] not in {"BUY", "SELL"}:
        return False
    if not prev:
        return True
    return prev.get("date") != curr.get("date") or prev.get("signal") != curr.get("signal")


def run_once() -> None:
    df = load_data()
    signal_info = latest_signal(df)
    prev = load_state()

    print(json.dumps(signal_info, ensure_ascii=False))

    if should_notify(prev, signal_info):
        send_email(signal_info)
        save_state(signal_info)
        print("Email sent.")
    else:
        print("No new actionable signal.")


def parse_run_time_utc(value: str) -> tuple[int, int]:
    hour_str, minute_str = value.split(":", 1)
    hour = int(hour_str)
    minute = int(minute_str)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("RUN_TIME_UTC must be HH:MM")
    return hour, minute


def seconds_until_next_run() -> int:
    hour, minute = parse_run_time_utc(RUN_TIME_UTC)
    now = datetime.now(timezone.utc)
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target = target + timedelta(days=1)
    return max(1, int((target - now).total_seconds()))


def main() -> None:
    if RUN_MODE == "once":
        run_once()
        return

    if RUN_MODE != "daily":
        raise ValueError("RUN_MODE must be 'daily' or 'once'")

    if RUN_ON_START:
        try:
            run_once()
        except Exception as e:
            print(f"ERROR: {e}")

    while True:
        sleep_seconds = seconds_until_next_run()
        print(f"Next run in {sleep_seconds} seconds at {RUN_TIME_UTC} UTC")
        time.sleep(sleep_seconds)
        try:
            run_once()
        except Exception as e:
            print(f"ERROR: {e}")


if __name__ == "__main__":
    main()
