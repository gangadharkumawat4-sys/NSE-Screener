from screener.rule_engine import apply_conditions


def _pct_gain(close_series, n_bars: int, current_price: float):
    """Return % gain over n_bars trading days, or None if insufficient history."""
    if len(close_series) <= n_bars:
        return None
    prev = float(close_series.iloc[-(n_bars + 1)])
    if prev == 0:
        return None
    return round((current_price - prev) / prev * 100, 2)


def run_screener(
    ohlc_data: dict,
    stock_universe: list[dict],
    conditions: list[dict],
    live_prices: dict = None,
    progress_callback=None,
) -> list[dict]:
    """
    Screen all stocks and return those passing every condition.

    Args:
        ohlc_data:        {yf_ticker: DataFrame} from data_fetcher
        stock_universe:   list of stock dicts from universe.load_universe()
        conditions:       list of condition dicts (rule_engine format)
        live_prices:      optional {pSymbol_str: {ltp, ...}} from Kotak
        progress_callback: called with (done, total) after each stock

    Returns:
        List of result dicts sorted by 1M% gain descending.
    """
    results = []
    total = len(stock_universe)

    for i, stock in enumerate(stock_universe):
        if progress_callback:
            progress_callback(i + 1, total)

        df = ohlc_data.get(stock["yf_ticker"])
        if df is None or len(df) < 10:
            continue

        if apply_conditions(df, conditions) is not True:
            continue

        close = df["Close"].dropna()
        current_price = float(close.iloc[-1])

        # Override with Kotak live price if available
        if live_prices:
            lp = live_prices.get(str(stock["pSymbol"]))
            if lp and lp["ltp"] > 0:
                current_price = lp["ltp"]

        results.append(
            {
                "Symbol": stock["pTrdSymbol"].rsplit("-", 1)[0],
                "Name": stock["full_name"],
                "CMP": round(current_price, 2),
                "1D%": _pct_gain(close, 1, current_price),
                "1W%": _pct_gain(close, 5, current_price),
                "1M%": _pct_gain(close, 22, current_price),
                "3M%": _pct_gain(close, 66, current_price),
            }
        )

    # Sort by 1M% descending by default (nulls last)
    results.sort(key=lambda x: x["1M%"] if x["1M%"] is not None else float("-inf"), reverse=True)
    return results
