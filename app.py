import json
import os
import sys
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

# ── Path setup ──────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent.parent          # repo root (or kotak-neo-api-v2-main/)
DASH = Path(__file__).parent                 # dashboard/
sys.path.insert(0, str(ROOT))               # for neo_api_client imports
sys.path.insert(0, str(DASH))               # for screener imports

# Load .env locally; on Streamlit Cloud credentials come from st.secrets
load_dotenv(DASH / ".env")


def _secret(key: str) -> str:
    """Read from Streamlit secrets first, fall back to env var."""
    try:
        return st.secrets.get(key, os.getenv(key, ""))
    except Exception:
        return os.getenv(key, "")

from screener.universe import load_universe
from screener.data_fetcher import fetch_ohlc_batch, fetch_live_prices_kotak
from screener.filters import run_screener

PRESETS_DIR = DASH / "presets"
PRESETS_DIR.mkdir(exist_ok=True)

# ── Constants ────────────────────────────────────────────────────────────────
TIMEFRAMES = ["daily", "weekly", "monthly"]
INDICATORS = ["wma", "close", "open", "high", "low", "volume"]
OPERATORS  = [">", ">=", "<", "<=", "=="]

DEFAULT_CONDITIONS = [
    {
        "left":  {"timeframe": "daily",   "indicator": "wma",    "period": 1,  "offset_periods": 0},
        "op":    ">",
        "right": {"type": "indicator",    "timeframe": "monthly", "indicator": "wma", "period": 2, "offset_periods": 0, "abs_offset": 1.0},
    },
    {
        "left":  {"timeframe": "monthly", "indicator": "wma",    "period": 2,  "offset_periods": 0},
        "op":    ">",
        "right": {"type": "indicator",    "timeframe": "monthly", "indicator": "wma", "period": 4, "offset_periods": 0, "abs_offset": 2.0},
    },
    {
        "left":  {"timeframe": "daily",   "indicator": "wma",    "period": 1,  "offset_periods": 0},
        "op":    ">",
        "right": {"type": "indicator",    "timeframe": "weekly",  "indicator": "wma", "period": 6, "offset_periods": 0, "abs_offset": 2.0},
    },
    {
        "left":  {"timeframe": "weekly",  "indicator": "wma",    "period": 6,  "offset_periods": 0},
        "op":    ">",
        "right": {"type": "indicator",    "timeframe": "weekly",  "indicator": "wma", "period": 12, "offset_periods": 0, "abs_offset": 2.0},
    },
    {
        "left":  {"timeframe": "daily",   "indicator": "wma",    "period": 1,  "offset_periods": 0},
        "op":    ">",
        "right": {"type": "indicator",    "timeframe": "daily",   "indicator": "wma", "period": 12, "offset_periods": 4, "abs_offset": 2.0},
    },
    {
        "left":  {"timeframe": "daily",   "indicator": "wma",    "period": 1,  "offset_periods": 0},
        "op":    ">",
        "right": {"type": "indicator",    "timeframe": "daily",   "indicator": "wma", "period": 20, "offset_periods": 2, "abs_offset": 2.0},
    },
    {
        "left":  {"timeframe": "daily",   "indicator": "close",  "period": 1,  "offset_periods": 0},
        "op":    ">",
        "right": {"type": "number", "value": 25},
    },
    {
        "left":  {"timeframe": "daily",   "indicator": "close",  "period": 1,  "offset_periods": 0},
        "op":    "<=",
        "right": {"type": "number", "value": 500},
    },
    {
        "left":  {"timeframe": "weekly",  "indicator": "volume", "period": 1,  "offset_periods": 0},
        "op":    ">",
        "right": {"type": "number", "value": 85000},
    },
]

# ── Preset helpers ────────────────────────────────────────────────────────────

def list_presets() -> dict:
    out = {}
    for fp in sorted(PRESETS_DIR.glob("*.json")):
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
            out[data["name"]] = data["conditions"]
        except Exception:
            pass
    return out


def save_preset(name: str, conditions: list):
    slug = name.strip().lower().replace(" ", "_").replace("/", "_")
    fp = PRESETS_DIR / f"{slug}.json"
    fp.write_text(json.dumps({"name": name.strip(), "conditions": conditions}, indent=2), encoding="utf-8")


def delete_preset(name: str):
    for fp in PRESETS_DIR.glob("*.json"):
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
            if data.get("name") == name:
                fp.unlink()
                return
        except Exception:
            pass

# ── Session state init ────────────────────────────────────────────────────────

def _init_state():
    if "conditions" not in st.session_state:
        st.session_state["conditions"] = DEFAULT_CONDITIONS.copy()
    if "results" not in st.session_state:
        st.session_state["results"] = None
    if "scan_meta" not in st.session_state:
        st.session_state["scan_meta"] = {}
    if "kotak_client" not in st.session_state:
        st.session_state["kotak_client"] = None
    if "kotak_status" not in st.session_state:
        st.session_state["kotak_status"] = "disconnected"  # disconnected | connected | error

_init_state()

# ── Condition builder helpers ─────────────────────────────────────────────────

def _load_preset_conditions(conditions: list):
    """Replace conditions and clear stale widget keys, then rerun."""
    n_old = len(st.session_state["conditions"])
    for i in range(n_old):
        for suffix in ["ltf", "lind", "lper", "lago", "op", "rtype", "rval",
                       "rtf", "rind", "rper", "rag", "rabs"]:
            st.session_state.pop(f"c{i}_{suffix}", None)
    st.session_state["conditions"] = [c.copy() for c in conditions]
    st.rerun()


def _get_current_conditions() -> list:
    """Rebuild conditions list from widget session-state values."""
    n = len(st.session_state["conditions"])
    result = []
    for i in range(n):
        left_tf   = st.session_state.get(f"c{i}_ltf",  "daily")
        left_ind  = st.session_state.get(f"c{i}_lind", "close")
        left_per  = int(st.session_state.get(f"c{i}_lper", 1))
        left_ago  = int(st.session_state.get(f"c{i}_lago", 0))
        op_val    = st.session_state.get(f"c{i}_op",   ">")
        rtype     = st.session_state.get(f"c{i}_rtype", "Number")

        left = {"timeframe": left_tf, "indicator": left_ind,
                "period": left_per, "offset_periods": left_ago}

        if rtype == "Number":
            rval  = float(st.session_state.get(f"c{i}_rval", 0))
            right = {"type": "number", "value": rval}
        else:
            right_tf  = st.session_state.get(f"c{i}_rtf",  "daily")
            right_ind = st.session_state.get(f"c{i}_rind", "close")
            right_per = int(st.session_state.get(f"c{i}_rper", 1))
            right_ago = int(st.session_state.get(f"c{i}_rag",  0))
            right_abs = float(st.session_state.get(f"c{i}_rabs", 0.0))
            right = {
                "type": "indicator",
                "timeframe": right_tf, "indicator": right_ind,
                "period": right_per, "offset_periods": right_ago,
                "abs_offset": right_abs,
            }

        result.append({"left": left, "op": op_val, "right": right})
    return result


def _render_condition_row(i: int, cond: dict):
    """Draw one condition row inside an expander. Returns whether Remove was clicked."""
    left  = cond.get("left",  {})
    right = cond.get("right", {})

    c1, c2, c3, c4 = st.columns([3, 3, 1, 1])
    c1.selectbox("Timeframe", TIMEFRAMES,
                 index=TIMEFRAMES.index(left.get("timeframe", "daily")),
                 key=f"c{i}_ltf")
    c2.selectbox("Indicator", INDICATORS,
                 index=INDICATORS.index(left.get("indicator", "close")),
                 key=f"c{i}_lind")
    l_ind = st.session_state.get(f"c{i}_lind", left.get("indicator", "close"))
    c3.number_input("Period", min_value=1, max_value=500,
                    value=int(left.get("period", 1)),
                    key=f"c{i}_lper", disabled=(l_ind != "wma"))
    c4.number_input("N ago", min_value=0, max_value=200,
                    value=int(left.get("offset_periods", 0)),
                    key=f"c{i}_lago")

    oc1, oc2 = st.columns([1, 3])
    oc1.selectbox("Operator", OPERATORS,
                  index=OPERATORS.index(cond.get("op", ">")),
                  key=f"c{i}_op")
    r_type_default = "Number" if right.get("type") == "number" else "Indicator"
    oc2.radio("Right side type", ["Number", "Indicator"],
              index=0 if r_type_default == "Number" else 1,
              key=f"c{i}_rtype", horizontal=True)

    rtype = st.session_state.get(f"c{i}_rtype", r_type_default)

    if rtype == "Number":
        st.number_input("Value (₹ or units)", value=float(right.get("value", 0)),
                        key=f"c{i}_rval")
    else:
        rc1, rc2, rc3, rc4, rc5 = st.columns([3, 3, 1, 1, 1])
        rc1.selectbox("Timeframe", TIMEFRAMES,
                      index=TIMEFRAMES.index(right.get("timeframe", "daily")),
                      key=f"c{i}_rtf")
        rc2.selectbox("Indicator", INDICATORS,
                      index=INDICATORS.index(right.get("indicator", "close")),
                      key=f"c{i}_rind")
        r_ind = st.session_state.get(f"c{i}_rind", right.get("indicator", "close"))
        rc3.number_input("Period", min_value=1, max_value=500,
                         value=int(right.get("period", 1)),
                         key=f"c{i}_rper", disabled=(r_ind != "wma"))
        rc4.number_input("N ago", min_value=0, max_value=200,
                         value=int(right.get("offset_periods", 0)),
                         key=f"c{i}_rag")
        rc5.number_input("+₹ offset", value=float(right.get("abs_offset", 0.0)),
                         key=f"c{i}_rabs")

    remove = st.button(f"🗑 Remove condition {i + 1}", key=f"remove_{i}")
    st.divider()
    return remove

# ── Kotak auth helper ─────────────────────────────────────────────────────────

def _kotak_connect(totp: str):
    try:
        from neo_api_client import NeoAPI
        api_token = _secret("KOTAK_API_TOKEN")
        mobile    = _secret("KOTAK_MOBILE")
        ucc       = _secret("KOTAK_UCC")
        mpin      = _secret("KOTAK_MPIN")

        if not all([api_token, mobile, ucc, mpin]):
            return None, "Missing credentials in .env file"

        client = NeoAPI(environment="prod", consumer_key=api_token)
        r1 = client.totp_login(mobile_number=mobile, ucc=ucc, totp=totp)
        if r1.get("data", {}).get("status") != "success":
            msg = r1.get("data", {}).get("message") or str(r1)
            return None, f"Login failed: {msg}"

        r2 = client.totp_validate(mpin=mpin)
        if r2.get("data", {}).get("status") != "success":
            msg = r2.get("data", {}).get("message") or str(r2)
            return None, f"Validate failed: {msg}"

        return client, None
    except Exception as e:
        return None, str(e)

# ── Main UI ───────────────────────────────────────────────────────────────────

st.set_page_config(page_title="NSE Stock Screener", page_icon="📈", layout="wide")
st.title("📈 NSE Stock Screener")

# ── Sidebar ──────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ Settings")

    # ── Preset manager ──
    st.subheader("💾 Presets")
    presets = list_presets()

    col_load, col_del = st.columns(2)
    preset_names = list(presets.keys())
    if preset_names:
        selected_preset = col_load.selectbox("Load preset", ["— select —"] + preset_names,
                                              label_visibility="collapsed")
        if col_load.button("Load", use_container_width=True):
            if selected_preset != "— select —":
                _load_preset_conditions(presets[selected_preset])

        if col_del.button("Delete", use_container_width=True, type="secondary"):
            if selected_preset != "— select —":
                delete_preset(selected_preset)
                st.rerun()
    else:
        st.caption("No saved presets yet.")

    save_name = st.text_input("Save current as…", placeholder="Preset name")
    if st.button("💾 Save preset", use_container_width=True):
        if save_name.strip():
            save_preset(save_name.strip(), _get_current_conditions())
            st.success(f'Saved "{save_name.strip()}"')
            st.rerun()
        else:
            st.warning("Enter a preset name first.")

    st.divider()

    # ── Sort options ──
    st.subheader("📊 Sort results by")
    sort_col = st.radio("Sort by gain", ["1M%", "3M%", "1W%", "1D%"],
                        horizontal=True, label_visibility="collapsed")

    st.divider()

    # ── Kotak live price ──
    st.subheader("🔴 Kotak Live Prices (optional)")
    kotak_status = st.session_state["kotak_status"]
    if kotak_status == "connected":
        st.success("Connected ✅")
        if st.button("Disconnect"):
            st.session_state["kotak_client"] = None
            st.session_state["kotak_status"] = "disconnected"
            st.rerun()
    else:
        totp_input = st.text_input("Enter TOTP (6-digit code)", max_chars=6,
                                    type="password", placeholder="From authenticator app")
        if st.button("🔗 Connect", use_container_width=True):
            if len(totp_input) == 6 and totp_input.isdigit():
                with st.spinner("Authenticating…"):
                    client, err = _kotak_connect(totp_input)
                if err:
                    st.error(err)
                    st.session_state["kotak_status"] = "error"
                else:
                    st.session_state["kotak_client"] = client
                    st.session_state["kotak_status"] = "connected"
                    st.success("Connected!")
                    st.rerun()
            else:
                st.warning("TOTP must be 6 digits.")
        if kotak_status == "error":
            st.caption("Auth failed — using yfinance close price.")
        else:
            st.caption("If not connected, uses yfinance last close.")

    st.divider()

    # ── Run Scan ──
    run_scan = st.button("▶ Run Scan", use_container_width=True, type="primary")


# ── Condition Builder (main area) ────────────────────────────────────────────
with st.expander("🔧 Condition Builder", expanded=(st.session_state["results"] is None)):
    conditions = st.session_state["conditions"]
    remove_idx = None

    for i, cond in enumerate(conditions):
        if _render_condition_row(i, cond):
            remove_idx = i

    if remove_idx is not None:
        st.session_state["conditions"].pop(remove_idx)
        # Clear widget keys for removed index
        for suffix in ["ltf", "lind", "lper", "lago", "op", "rtype", "rval",
                       "rtf", "rind", "rper", "rag", "rabs"]:
            st.session_state.pop(f"c{remove_idx}_{suffix}", None)
        st.rerun()

    if st.button("➕ Add Condition"):
        st.session_state["conditions"].append(
            {
                "left":  {"timeframe": "daily", "indicator": "close", "period": 1, "offset_periods": 0},
                "op":    ">",
                "right": {"type": "number", "value": 0},
            }
        )
        st.rerun()

# ── Summary metrics ──────────────────────────────────────────────────────────
meta = st.session_state.get("scan_meta", {})
m1, m2, m3, m4 = st.columns(4)
m1.metric("Stocks in universe", meta.get("universe_size", "—"))
m2.metric("Stocks scanned",     meta.get("scanned", "—"))
m3.metric("Passed filters",     meta.get("passed", "—"))
m4.metric("Last scan",          meta.get("last_scan", "—"))

# ── Scan logic ───────────────────────────────────────────────────────────────
if run_scan:
    current_conditions = _get_current_conditions()

    with st.status("Running scan…", expanded=True) as status:
        st.write("Loading stock universe…")
        universe = load_universe()
        tickers = [s["yf_ticker"] for s in universe]
        st.write(f"Universe: {len(universe)} NSE equity stocks")

        st.write("Downloading OHLC data (cached daily)…")
        ohlc = fetch_ohlc_batch(tickers, period="6mo")
        st.write(f"Data loaded for {len(ohlc)} stocks")

        live_prices = {}
        kotak_client = st.session_state.get("kotak_client")
        if kotak_client:
            st.write("Fetching live prices from Kotak Neo…")
            psymbols = [str(s["pSymbol"]) for s in universe]
            live_prices = fetch_live_prices_kotak(kotak_client, psymbols)
            st.write(f"Live prices fetched for {len(live_prices)} stocks")

        st.write("Applying filters…")
        progress_bar = st.progress(0)
        progress_text = st.empty()
        scanned_count = {"n": 0}

        def _on_progress(done, total):
            scanned_count["n"] = done
            progress_bar.progress(done / total)
            progress_text.text(f"Scanning {done}/{total}…")

        results = run_screener(
            ohlc_data=ohlc,
            stock_universe=universe,
            conditions=current_conditions,
            live_prices=live_prices if live_prices else None,
            progress_callback=_on_progress,
        )

        progress_bar.progress(1.0)
        progress_text.empty()
        status.update(label=f"✅ Scan complete — {len(results)} stocks passed", state="complete")

    from datetime import datetime
    st.session_state["results"] = results
    st.session_state["scan_meta"] = {
        "universe_size": len(universe),
        "scanned": len(ohlc),
        "passed": len(results),
        "last_scan": datetime.now().strftime("%H:%M:%S"),
    }
    st.rerun()

# ── Results table ─────────────────────────────────────────────────────────────
results = st.session_state.get("results")
if results:
    df_res = pd.DataFrame(results)

    # Re-sort by user's chosen column
    sort_ascending = sort_col.startswith("1D") and False  # always descending
    df_sorted = df_res.sort_values(sort_col, ascending=False, na_position="last")

    # Colour coding for gain columns
    def _colour_gain(val):
        if val is None or (isinstance(val, float) and pd.isna(val)):
            return ""
        color = "color: #16a34a" if val > 0 else ("color: #dc2626" if val < 0 else "")
        return color

    gain_cols = ["1D%", "1W%", "1M%", "3M%"]
    styled = df_sorted.style.map(_colour_gain, subset=gain_cols).format(
        {c: "{:.2f}" for c in gain_cols if c in df_sorted.columns}
    ).format({"CMP": "₹{:.2f}"})

    st.dataframe(styled, use_container_width=True, height=600)

    # Download button
    csv = df_sorted.to_csv(index=False)
    st.download_button("⬇ Download CSV", data=csv, file_name="screener_results.csv",
                       mime="text/csv")
elif st.session_state["results"] is not None:
    st.info("No stocks matched all conditions. Try relaxing the filters.")
else:
    st.info("Configure your conditions in the builder above, then click ▶ Run Scan.")
