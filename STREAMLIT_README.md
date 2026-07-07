# Portfolio Advisor Streamlit

Streamlit version of the local Portfolio Advisor.

## How It Works

- Upload the bank "Portafoglio sintesi" export (`.xls`) or the IPS-ready Excel workbook (`.xlsx`) from the sidebar.
- Optionally upload one or more Fineco quarterly statement PDFs.
- The app calculates allocation, IPS drift, estimated income, realized coupons/dividends, tax withholding, advisor diagnostics, suggested actions, Hot Trading signals, market monitoring, and position-level tables.

The bank export is normalized at runtime into the app's internal portfolio schema. IPS subclasses are inferred from security type, ISIN, market, ticker, and title, then can be refined in code as new instruments appear.

The Advisor tab is rule-based and explainable: it highlights IPS breaches, concentration, home bias, currency exposure, structured/leveraged exposure, income gaps, and positions whose thesis should be reviewed. The Mercati tab uses public Yahoo Finance data as market context.

The Hot Trading tab is a separate tactical sleeve for small short-term experiments. It excludes current portfolio tickers, ranks technical buy/sell-short signals, and sizes ideas against a user-defined budget and maximum sleeve loss.

## Privacy

This repository intentionally does not include portfolio data, statement PDFs, local JSON extracts, or local machine paths. Financial files are uploaded at runtime in the Streamlit session.

## Streamlit Cloud

Use `streamlit_app.py` as the app entrypoint.
