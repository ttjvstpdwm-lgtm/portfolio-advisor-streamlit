# Portfolio Advisor Streamlit

Streamlit version of the local Portfolio Advisor.

## How It Works

- Upload the bank "Portafoglio sintesi" export (`.xls`) or the IPS-ready Excel workbook (`.xlsx`) from the sidebar.
- Optionally upload one or more Fineco quarterly statement PDFs.
- The app calculates allocation, IPS drift, estimated income, realized coupons/dividends, tax withholding, and position-level tables.

The bank export is normalized at runtime into the app's internal portfolio schema. IPS subclasses are inferred from security type, ISIN, market, ticker, and title, then can be refined in code as new instruments appear.

## Privacy

This repository intentionally does not include portfolio data, statement PDFs, local JSON extracts, or local machine paths. Financial files are uploaded at runtime in the Streamlit session.

## Streamlit Cloud

Use `streamlit_app.py` as the app entrypoint.
