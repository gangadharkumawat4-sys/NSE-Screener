import pandas as pd
from pathlib import Path

_BUNDLED_CSV = Path(__file__).parent.parent / "neo_api_client" / "api" / "nse_cm.csv"


def load_universe() -> list[dict]:
    """
    Load NSE equity stocks (pGroup == 'EQ') from the bundled nse_cm.csv.
    Returns list of dicts with keys: pSymbol, pTrdSymbol, name, full_name, yf_ticker.
    """
    df = pd.read_csv(_BUNDLED_CSV, low_memory=False)

    eq = df[df["pGroup"] == "EQ"].copy()

    # yfinance ticker: strip series suffix (e.g. -EQ, -BE) and add .NS
    eq["yf_ticker"] = eq["pTrdSymbol"].str.rsplit("-", n=1).str[0] + ".NS"
    eq["name"] = eq["pSymbolName"].fillna("")
    eq["full_name"] = eq["pDesc"].fillna(eq["pSymbolName"]).fillna("")

    # Deduplicate by trading symbol
    eq = eq.drop_duplicates(subset="pTrdSymbol")

    return eq[["pSymbol", "pTrdSymbol", "name", "full_name", "yf_ticker"]].to_dict("records")
