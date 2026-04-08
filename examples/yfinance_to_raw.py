from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import yfinance as yf

WATCHLIST = {
    "CPO／矽光子與光通訊": [
        "LITE", "COHR", "INFN", "AAOI", "CIEN",
        "3163.TWO", "3363.TWO", "4979.TWO", "6442.TW", "3081.TWO",
    ],
    "半導體": [
        "NVDA", "AMD", "AVGO", "TSM", "ASML", "ARM",
        "2330.TW", "2454.TW", "2303.TW", "3711.TW", "3443.TW",
    ],
    "記憶體": [
        "MU", "WDC", "STX", "SIMO",
        "2408.TW", "2344.TW", "2337.TW", "2451.TW",
    ],
    "電動車": [
        "TSLA", "RIVN", "LI", "NIO", "GM",
        "2308.TW", "1519.TW", "3665.TW",
    ],
    "散熱": [
        "VRT", "SMCI", "MOD",
        "3017.TW", "3324.TWO", "3653.TW", "2421.TW", "2308.TW",
    ],
    "量子電腦": [
        "IONQ", "RGTI", "QBTS", "QUBT", "IBM", "GOOGL",
    ],
}


def build_insight(ticker: str, industry_theme: str) -> dict[str, object]:
    stock = yf.Ticker(ticker)
    info = stock.info
    long_name = info.get("longName") or ticker
    sector = info.get("sector") or "未知產業"
    industry = info.get("industry") or industry_theme
    market_cap = info.get("marketCap")
    country = info.get("country") or "未知地區"

    content = (
        f"{long_name} 屬於 {sector} / {industry}，註冊地或主要市場為 {country}。"
        f"目前可用的市值欄位為 {market_cap}。"
    )

    return {
        "type": "stock",
        "content": content,
        "tickers": [ticker],
        "key_points": [
            f"company: {long_name}",
            f"theme: {industry_theme}",
            f"sector: {sector}",
            f"industry: {industry}",
            f"country: {country}",
            f"market_cap: {market_cap}",
        ],
    }


def main() -> None:
    out_dir = Path("raw")
    out_dir.mkdir(parents=True, exist_ok=True)

    for theme, tickers in WATCHLIST.items():
        payload = {
            "episode": f"{theme} 產業觀察樣本",
            "podcast_name": "yfinance",
            "date": date.today().isoformat(),
            "insights": [build_insight(ticker, theme) for ticker in tickers],
        }
        output_path = out_dir / f"{date.today().isoformat()}_{theme}_yfinance_sample.json"
        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
        print(output_path)


if __name__ == "__main__":
    main()
