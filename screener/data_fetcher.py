import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf

_CACHE_DIR = Path(__file__).parent.parent / "cache"


def fetch_ohlc_batch(tickers: list[str], period: str = "6mo") -> dict[str, pd.DataFrame]:
    """
    Download 6 months of daily OHLCV for all tickers via yfinance.
    Results are cached per day in cache/ohlc_<date>.parquet to avoid re-downloading.
    Returns {ticker: DataFrame} with columns Open/High/Low/Close/Volume.
    """
    _CACHE_DIR.mkdir(exist_ok=True)
    cache_file = _CACHE_DIR / f"ohlc_{date.today().isoformat()}.parquet"

    if cache_file.exists():
        try:
            cached = pd.read_parquet(cache_file)
            result = _split_multiindex(cached, tickers)
            if result:
                return result
        except Exception:
            pass

    # yf.download with a space-separated string always returns MultiIndex columns
    ticker_str = " ".join(tickers)
    raw = yf.download(
        tickers=ticker_str,
        period=period,
        group_by="ticker",
        auto_adjust=True,
        progress=False,
        threads=True,
    )

    try:
        raw.to_parquet(cache_file)
    except Exception:
        pass

    return _split_multiindex(raw, tickers)


def _split_multiindex(raw: pd.DataFrame, tickers: list[str]) -> dict[str, pd.DataFrame]:
    """Extract per-ticker DataFrames from a MultiIndex yfinance result."""
    result = {}
    if raw.empty:
        return result

    if isinstance(raw.columns, pd.MultiIndex):
        for ticker in tickers:
            try:
                df = raw[ticker].dropna(how="all")
                if not df.empty and len(df) >= 10:
                    result[ticker] = df
            except (KeyError, Exception):
                pass
    else:
        # Single ticker returned flat columns
        df = raw.dropna(how="all")
        if not df.empty and len(tickers) == 1:
            result[tickers[0]] = df

    return result


def fetch_live_prices_kotak(
    kotak_client, psymbols: list[str], batch_size: int = 50
) -> dict[str, dict]:
    """
    Fetch live LTP from Kotak Neo Quotes API using the authenticated client.
    Returns {pSymbol_str: {ltp, change, per_change}}.
    Requires kotak_client to be fully authenticated (totp_login + totp_validate done).
    """
    results = {}

    for i in range(0, len(psymbols), batch_size):
        batch = psymbols[i : i + batch_size]
        tokens = [
            {"exchange_segment": "nse_cm", "instrument_token": str(sym)} for sym in batch
        ]
        try:
            resp = kotak_client.quotes(instrument_tokens=tokens, quote_type="ltp")
            if isinstance(resp, list):
                for item in resp:
                    token = str(item.get("exchange_token", ""))
                    try:
                        results[token] = {
                            "ltp": float(item.get("ltp") or 0),
                            "change": float(item.get("change") or 0),
                            "per_change": float(item.get("per_change") or 0),
                        }
                    except (TypeError, ValueError):
                        pass
        except Exception:
            pass

    return results


def fetch_market_caps(tickers: list[str], max_workers: int = 30) -> dict[str, float]:
    """
    Fetch market cap (in ₹ Crores) for all tickers using yfinance fast_info.
    Results are cached per day. Returns {yf_ticker: mcap_in_crores}.
    """
    _CACHE_DIR.mkdir(exist_ok=True)
    cache_file = _CACHE_DIR / f"mcap_{date.today().isoformat()}.parquet"

    if cache_file.exists():
        try:
            return pd.read_parquet(cache_file)["mcap_cr"].to_dict()
        except Exception:
            pass

    results: dict[str, float] = {}

    def _get_one(ticker: str):
        try:
            mc = yf.Ticker(ticker).fast_info.market_cap
            if mc and mc > 0:
                return ticker, mc / 1e7  # rupees → crores
        except Exception:
            pass
        return ticker, None

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(_get_one, t): t for t in tickers}
        for fut in as_completed(futures):
            ticker, val = fut.result()
            if val is not None:
                results[ticker] = round(val, 2)

    try:
        pd.DataFrame.from_dict(results, orient="index", columns=["mcap_cr"]).to_parquet(cache_file)
    except Exception:
        pass

    return results
