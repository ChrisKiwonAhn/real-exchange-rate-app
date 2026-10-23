
# Real Exchange Rate App

A Streamlit app that computes the bilateral **real exchange rate (RER)** between any two IMF countries (or the Euro area) using monthly IMF CPI and exchange-rate data from January 1976 where available.

## Definitions

| Symbol | Meaning |
|---|---|
| S(t) | Units of Country 2 currency per 1 unit of Country 1 currency |
| P1(t), P2(t) | Monthly consumer price index of Country 1 and Country 2 |
| RER(t) | S(t) × P1(t) / P2(t) |
| RER* | Average of RER(t) over valid months in the selected sample |
| Real Index(t) | 100 × RER(t) / RER* (averages 100 over the sample) |

IMF exchange rates are reported as local currency per USD (period average), so the app converts them:

S(t) = (Country 2 currency per USD) / (Country 1 currency per USD)

The US dollar is 1 per USD by definition. A higher Real Index means the Country 1 currency has appreciated in real terms against the Country 2 currency.

**Example:** with UK as Country 1 and U.S. as Country 2, S(t) is USD per GBP.

## Features

- Choose Country 1 and Country 2 from IMF countries and the Euro area (G163)
- Choose the start and end months; RER* and the Real Index are recalculated automatically
- Chart options: Real, Nominal, or Both (separate y-axes)
- Nominal S(t) is plotted in actual currency units (not normalized)
- CSV download of all observations and calculations in the selected sample

## Data source

IMF SDMX 3.0 API: `https://api.imf.org/external/sdmx/3.0`

| Series | Dataflow | Key |
|---|---|---|
| CPI (all items, index) | `IMF.STA/CPI` | `{ISO3}.CPI._T.IX.M` |
| Exchange rate (per USD, period average) | `IMF.STA/ER` | `{ISO3}.XDC_USD.PA_RT.M` |

No API key is needed. IMF data can have gaps or limited coverage; months with missing inputs are skipped when computing RER*. Live downloads may occasionally fail if the IMF server is busy—just try again.

## Files

- `app.py` – the Streamlit app
- `requirements.txt` – Python packages
- `README.md` – this file

## Run online

Deployed on Streamlit Community Cloud (Python 3.12) from this repository's `main` branch with `app.py` as the main file.

## Run locally (optional)

```bash
pip install -r requirements.txt
streamlit run app.py
```
