"""Real Exchange Rate app.

Downloads monthly CPI and exchange-rate data from the IMF SDMX 3.0 API and
computes the bilateral real exchange rate between any two IMF countries
(or the Euro area).

Definitions
-----------
S(t)        = units of Country 2 currency per 1 unit of Country 1 currency
P1(t), P2(t) = monthly CPI of Country 1 and Country 2
RER(t)      = S(t) * P1(t) / P2(t)
RER*        = average of RER(t) over valid months in the selected sample
Real Index  = 100 * RER(t) / RER*
"""

from __future__ import annotations

import io

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st
from plotly.subplots import make_subplots

# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------
BASE_URL = "https://api.imf.org/external/sdmx/3.0"
AGENCY = "IMF.STA"
START_PERIOD = "1976-M01"
EURO_AREA = "G163"
HEADERS = {
    "Accept": "application/json",
    "User-Agent": "real-exchange-rate-app (Streamlit; educational use)",
}
TIMEOUT = 60

# Used only if the IMF country list cannot be downloaded.
FALLBACK_COUNTRIES = {
    "USA": "United States",
    "GBR": "United Kingdom",
    EURO_AREA: "Euro area",
    "JPN": "Japan",
    "CHN": "China",
    "CAN": "Canada",
    "AUS": "Australia",
    "CHE": "Switzerland",
    "MEX": "Mexico",
    "BRA": "Brazil",
    "IND": "India",
    "KOR": "Korea, Republic of",
    "DEU": "Germany",
    "FRA": "France",
    "ITA": "Italy",
    "ESP": "Spain",
    "SWE": "Sweden",
    "NOR": "Norway",
    "ZAF": "South Africa",
    "TUR": "Türkiye",
}


# --------------------------------------------------------------------------
# IMF API helpers
# --------------------------------------------------------------------------
def _get_json(url: str, params: dict | None = None) -> dict | None:
    """GET a URL and return parsed JSON, or None for 'no data' (204/404)."""
    last_error = None
    for _ in range(3):  # simple retry
        try:
            resp = requests.get(url, params=params, headers=HEADERS, timeout=TIMEOUT)
            if resp.status_code in (204, 404):
                return None
            resp.raise_for_status()
            if not resp.text.strip():
                return None
            return resp.json()
        except (requests.RequestException, ValueError) as err:
            last_error = err
    raise RuntimeError(f"IMF API request failed: {last_error}")


def parse_sdmx_series(payload: dict | None) -> pd.Series:
    """Turn an SDMX-JSON 2.0 data message into a monthly pandas Series.

    The index is a month-start Timestamp and the values are floats.
    """
    if not payload:
        return pd.Series(dtype=float)
    data = payload.get("data", {})
    datasets = data.get("dataSets") or []
    structures = data.get("structures") or [data.get("structure", {})]
    if not datasets or not structures:
        return pd.Series(dtype=float)

    obs_dims = structures[0].get("dimensions", {}).get("observation", [])
    time_dim = next((d for d in obs_dims if d.get("id") == "TIME_PERIOD"), None)
    if time_dim is None:
        return pd.Series(dtype=float)
    periods = [v.get("value") or v.get("id") for v in time_dim.get("values", [])]

    series_dict = datasets[0].get("series") or {}
    if not series_dict:
        return pd.Series(dtype=float)
    first_series = next(iter(series_dict.values()))  # key is fully specified
    observations = first_series.get("observations", {})

    records = {}
    for idx, values in observations.items():
        try:
            period = periods[int(idx)]
            value = float(values[0])
        except (IndexError, ValueError, TypeError):
            continue
        records[period] = value

    if not records:
        return pd.Series(dtype=float)
    s = pd.Series(records, dtype=float)
    # IMF monthly periods look like "1976-M01"
    s.index = pd.to_datetime(s.index.str.replace("-M", "-", regex=False), format="%Y-%m")
    return s.sort_index()


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def fetch_imf_series(dataflow: str, key: str) -> pd.Series:
    url = f"{BASE_URL}/data/dataflow/{AGENCY}/{dataflow}/+/{key}"
    params = {
        "c[TIME_PERIOD]": f"ge:{START_PERIOD}",
        "dimensionAtObservation": "TIME_PERIOD",
        "attributes": "none",
        "measures": "all",
    }
    return parse_sdmx_series(_get_json(url, params))


def fetch_cpi(iso3: str) -> pd.Series:
    return fetch_imf_series("CPI", f"{iso3}.CPI._T.IX.M")


def fetch_fx_per_usd(iso3: str, months: pd.DatetimeIndex | None = None) -> pd.Series:
    """Local currency units per USD (period average).

    The US dollar is 1 per USD by definition, so it is not downloaded.
    """
    if iso3 == "USA":
        if months is None or len(months) == 0:
            months = pd.date_range("1976-01-01", pd.Timestamp.today(), freq="MS")
        return pd.Series(1.0, index=months)
    return fetch_imf_series("ER", f"{iso3}.XDC_USD.PA_RT.M")


def _available_codes(dataflow: str, key: str) -> set[str]:
    url = f"{BASE_URL}/availability/dataflow/{AGENCY}/{dataflow}/+/{key}/COUNTRY"
    payload = _get_json(url, {"mode": "exact", "references": "none"})
    codes: set[str] = set()
    if not payload:
        return codes
    for constraint in payload.get("data", {}).get("dataConstraints", []):
        for region in constraint.get("cubeRegions", []):
            for comp in region.get("components", []):
                if comp.get("id") == "COUNTRY":
                    for v in comp.get("values", []):
                        code = v.get("value") or v.get("id")
                        if code:
                            codes.add(code)
    return codes


def _label(item: dict) -> str:
    name = item.get("name")
    if isinstance(name, dict):
        name = name.get("en") or next(iter(name.values()), "")
    if not name:
        names = item.get("names") or {}
        name = names.get("en") or next(iter(names.values()), "")
    return name or item.get("id", "")


@st.cache_data(ttl=7 * 24 * 3600, show_spinner=False)
def load_countries() -> tuple[dict[str, str], str | None]:
    """Return ({ISO3: name}, warning).  Countries with both CPI and FX data."""
    warning = None
    try:
        payload = _get_json(f"{BASE_URL}/structure/codelist/IMF/CL_COUNTRY/+")
        codes = payload["data"]["codelists"][0]["codes"]
        names = {c["id"]: _label(c) for c in codes}
    except Exception:  # noqa: BLE001
        return dict(FALLBACK_COUNTRIES), (
            "Could not download the IMF country list; showing a short default list."
        )

    try:
        cpi_codes = _available_codes("CPI", "*.CPI._T.IX.M")
        fx_codes = _available_codes("ER", "*.XDC_USD.PA_RT.M") | {"USA"}
        usable = cpi_codes & fx_codes
        if usable:
            names = {k: v for k, v in names.items() if k in usable}
    except Exception:  # noqa: BLE001
        warning = "Could not check data availability; some countries may have no data."

    names[EURO_AREA] = "Euro area"
    return dict(sorted(names.items(), key=lambda kv: kv[1])), warning


# --------------------------------------------------------------------------
# Economics
# --------------------------------------------------------------------------
def build_full_table(cpi1, cpi2, fx1_usd, fx2_usd) -> pd.DataFrame:
    """Merge inputs and compute S(t) and RER(t) for every month since 1976."""
    df = pd.concat(
        {
            "P1_CPI": cpi1,
            "P2_CPI": cpi2,
            "FX1_per_USD": fx1_usd,
            "FX2_per_USD": fx2_usd,
        },
        axis=1,
    )
    df = df[df.index >= pd.Timestamp("1976-01-01")].sort_index()
    # S(t) = Country 2 currency per 1 Country 1 currency
    df["S"] = df["FX2_per_USD"] / df["FX1_per_USD"]
    df["RER"] = df["S"] * df["P1_CPI"] / df["P2_CPI"]
    return df


def apply_sample(df: pd.DataFrame, start, end) -> tuple[pd.DataFrame, float]:
    """Restrict to [start, end], compute RER* and the Real Index."""
    sample = df.loc[(df.index >= start) & (df.index <= end)].copy()
    valid = sample["RER"].dropna()
    rer_star = float(valid.mean()) if len(valid) else float("nan")
    sample["RER_star"] = rer_star
    sample["Real_Index"] = 100 * sample["RER"] / rer_star
    return sample, rer_star


# --------------------------------------------------------------------------
# User interface
# --------------------------------------------------------------------------
def main() -> None:
    st.set_page_config(page_title="Real Exchange Rate App", layout="wide")
    st.title("Real Exchange Rate App")
    st.caption(
        "Monthly CPI and exchange rates from the IMF (SDMX 3.0 API), January 1976 "
        "onward where available."
    )

    with st.spinner("Loading IMF country list..."):
        countries, warning = load_countries()
    if warning:
        st.warning(warning)

    codes = list(countries.keys())
    labels = {c: f"{countries[c]} ({c})" for c in codes}
    default1 = codes.index("GBR") if "GBR" in codes else 0
    default2 = codes.index("USA") if "USA" in codes else min(1, len(codes) - 1)

    col1, col2 = st.columns(2)
    c1 = col1.selectbox("Country 1", codes, index=default1, format_func=labels.get)
    c2 = col2.selectbox("Country 2", codes, index=default2, format_func=labels.get)
    if c1 == c2:
        st.info("Choose two different countries.")
        st.stop()

    name1, name2 = countries[c1], countries[c2]
    unit = f"{name2} currency per 1 {name1} currency"

    try:
        with st.spinner("Downloading CPI and exchange-rate data from the IMF..."):
            cpi1 = fetch_cpi(c1)
            cpi2 = fetch_cpi(c2)
            all_months = cpi1.index.union(cpi2.index)
            fx1 = fetch_fx_per_usd(c1, all_months)
            fx2 = fetch_fx_per_usd(c2, all_months)
    except RuntimeError as err:
        st.error(f"{err}\n\nThe IMF server may be busy. Please try again in a minute.")
        st.stop()

    missing = [
        label
        for label, s in [
            (f"CPI for {name1}", cpi1),
            (f"CPI for {name2}", cpi2),
            (f"exchange rate for {name1}", fx1),
            (f"exchange rate for {name2}", fx2),
        ]
        if s.empty
    ]
    if missing:
        st.error("The IMF returned no data for: " + ", ".join(missing) + ".")
        st.stop()

    full = build_full_table(cpi1, cpi2, fx1, fx2)
    valid_months = full.index[full["RER"].notna()]
    if len(valid_months) == 0:
        st.error("No months where CPI and exchange-rate data overlap for both countries.")
        st.stop()

    first, last = valid_months.min(), valid_months.max()
    month_options = list(pd.date_range(first, last, freq="MS"))
    start, end = st.select_slider(
        "Sample period",
        options=month_options,
        value=(first, last),
        format_func=lambda d: d.strftime("%b %Y"),
    )

    sample, rer_star = apply_sample(full, start, end)
    n_valid = int(sample["RER"].notna().sum())
    if n_valid == 0:
        st.error("No valid months in the selected sample.")
        st.stop()

    m1, m2, m3 = st.columns(3)
    m1.metric("RER* (sample average)", f"{rer_star:,.4f}")
    m2.metric("Valid months", f"{n_valid}")
    m3.metric("Real Index average", f"{sample['Real_Index'].mean():.2f}")
    if n_valid < len(sample):
        st.caption(f"{len(sample) - n_valid} month(s) in the sample have missing data and are skipped.")

    view = st.radio("Chart", ["Real", "Nominal", "Both"], horizontal=True)

    plot = sample.dropna(subset=["RER"])
    real_trace = go.Scatter(
        x=plot.index, y=plot["Real_Index"], name="Real Index (avg = 100)", line=dict(color="#1f77b4")
    )
    nominal_trace = go.Scatter(
        x=plot.index, y=plot["S"], name=f"Nominal S ({unit})", line=dict(color="#d62728")
    )

    if view == "Both":
        fig = make_subplots(specs=[[{"secondary_y": True}]])
        fig.add_trace(real_trace, secondary_y=False)
        fig.add_trace(nominal_trace, secondary_y=True)
        fig.update_yaxes(title_text="Real Index (sample avg = 100)", secondary_y=False)
        fig.update_yaxes(title_text=f"S: {unit}", secondary_y=True)
    elif view == "Real":
        fig = go.Figure(real_trace)
        fig.add_hline(y=100, line_dash="dot", line_color="gray")
        fig.update_yaxes(title_text="Real Index (sample avg = 100)")
    else:
        fig = go.Figure(nominal_trace)
        fig.update_yaxes(title_text=f"S: {unit}")

    fig.update_layout(
        title=f"{name1} vs {name2}: {start:%b %Y} to {end:%b %Y}",
        hovermode="x unified",
        legend=dict(orientation="h", y=-0.2),
        height=520,
    )
    st.plotly_chart(fig, use_container_width=True)

    st.markdown(
        f"**Definitions.** S(t) = {unit}, computed as "
        f"({c2} per USD) / ({c1} per USD). RER(t) = S(t) × P1(t) / P2(t), where "
        f"P1 is the CPI of {name1} and P2 is the CPI of {name2}. "
        "Real Index(t) = 100 × RER(t) / RER*, where RER* is the average RER over "
        "valid months in the selected sample. A rise in the index means a real "
        f"appreciation of the {name1} currency against the {name2} currency."
    )

    out = sample.reset_index().rename(columns={"index": "Month"})
    out["Month"] = out["Month"].dt.strftime("%Y-%m")
    out.insert(1, "Country1", c1)
    out.insert(2, "Country2", c2)
    out = out.rename(
        columns={
            "P1_CPI": f"P1_CPI_{c1}",
            "P2_CPI": f"P2_CPI_{c2}",
            "FX1_per_USD": f"{c1}_per_USD",
            "FX2_per_USD": f"{c2}_per_USD",
            "S": f"S_{c2}_per_{c1}",
        }
    )

    buffer = io.StringIO()
    out.to_csv(buffer, index=False)
    st.download_button(
        "Download CSV (selected sample)",
        data=buffer.getvalue(),
        file_name=f"rer_{c1}_{c2}_{start:%Y%m}_{end:%Y%m}.csv",
        mime="text/csv",
    )

    with st.expander("Show data table"):
        st.dataframe(out, use_container_width=True, hide_index=True)


if __name__ == "__main__":
    main()
