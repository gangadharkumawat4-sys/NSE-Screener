import operator as op_module
from screener.indicators import wma, resample_to_weekly, resample_to_monthly

_OPS = {
    ">": op_module.gt,
    ">=": op_module.ge,
    "<": op_module.lt,
    "<=": op_module.le,
    "==": op_module.eq,
}

_COL_MAP = {
    "close": "Close",
    "open": "Open",
    "high": "High",
    "low": "Low",
    "volume": "Volume",
}


def _resolve_value(df_daily, df_weekly, df_monthly, spec: dict):
    """
    Evaluate one side of a condition.
    spec keys: timeframe, indicator, period, offset_periods
    Returns a float or None if there's insufficient data.
    """
    tf = spec.get("timeframe", "daily")
    indicator = spec.get("indicator", "close")
    period = int(spec.get("period", 1))
    offset = int(spec.get("offset_periods", 0))

    if tf == "daily":
        df = df_daily
    elif tf == "weekly":
        df = df_weekly
    elif tf == "monthly":
        df = df_monthly
    else:
        return None

    if df is None or df.empty:
        return None

    if indicator == "wma":
        series = wma(df["Close"], period)
    elif indicator in _COL_MAP:
        series = df[_COL_MAP[indicator]]
    else:
        return None

    # Drop NaN and check enough history exists
    valid = series.dropna()
    target_idx = -(1 + offset)  # 0 offset → last bar; 1 offset → second-to-last, etc.

    if len(valid) < abs(target_idx):
        return None

    return float(valid.iloc[target_idx])


def evaluate_condition(df_daily, df_weekly, df_monthly, condition: dict):
    """
    Evaluate a single condition dict.
    Returns True/False, or None when data is insufficient.
    """
    left_val = _resolve_value(df_daily, df_weekly, df_monthly, condition["left"])
    if left_val is None:
        return None

    right = condition["right"]
    if right["type"] == "number":
        right_val = float(right["value"])
    else:
        right_val = _resolve_value(df_daily, df_weekly, df_monthly, right)
        if right_val is None:
            return None
        right_val += float(right.get("abs_offset", 0.0))

    op_fn = _OPS.get(condition.get("op", ">"))
    if op_fn is None:
        return None

    return op_fn(left_val, right_val)


def apply_conditions(df_daily, conditions: list):
    """
    Resample once, then evaluate all conditions (AND logic).
    Returns True if all pass, False if any fail, None if data is insufficient.
    """
    if df_daily is None or len(df_daily) < 10:
        return None

    df_weekly = resample_to_weekly(df_daily)
    df_monthly = resample_to_monthly(df_daily)

    for cond in conditions:
        result = evaluate_condition(df_daily, df_weekly, df_monthly, cond)
        if result is None:
            return None
        if not result:
            return False

    return True
