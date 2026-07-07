from __future__ import annotations

import hashlib
import io
import re
from collections import defaultdict
from datetime import datetime
from typing import Any

import pandas as pd
import streamlit as st
from pypdf import PdfReader

from advisor import (
    MARKET_INDICATORS,
    build_allocation_frame,
    build_diagnostics,
    build_recommendations,
    build_watchlist,
    fetch_market_snapshot,
)
from portfolio_import import parse_portfolio_excel

try:
    from advisor import HOT_TRADING_UNIVERSE, fetch_hot_trading_signals
except ImportError:
    HOT_TRADING_UNIVERSE = []

    def fetch_hot_trading_signals(
        tickers: list[str],
        portfolio_tickers: list[str],
        budget_eur: float,
        max_loss_pct: float,
        max_positions: int,
    ) -> tuple[pd.DataFrame, str | None]:
        return pd.DataFrame(), "Hot Trading non disponibile: attendi il redeploy completo di Streamlit Cloud."


st.set_page_config(
    page_title="Portfolio Advisor",
    page_icon="PA",
    layout="wide",
    initial_sidebar_state="expanded",
)


DEFAULT_ASSUMPTIONS = {
    "Bonds Italy Gov": {"min": 0.12, "target": 0.17, "max": 0.23, "yield": 0.034, "expected": 0.032, "risk": 5},
    "Bonds ex-Italy Corp IG": {"min": 0.07, "target": 0.11, "max": 0.16, "yield": 0.032, "expected": 0.035, "risk": 3},
    "Bonds High Yield": {"min": 0.00, "target": 0.04, "max": 0.07, "yield": 0.061, "expected": 0.055, "risk": 7},
    "Bonds Financial Credit": {"min": 0.02, "target": 0.04, "max": 0.07, "yield": 0.047, "expected": 0.045, "risk": 6},
    "Bonds Italy Corp": {"min": 0.00, "target": 0.02, "max": 0.05, "yield": 0.038, "expected": 0.038, "risk": 4},
    "Equity Global Core": {"min": 0.17, "target": 0.21, "max": 0.28, "yield": 0.015, "expected": 0.064, "risk": 6},
    "Equity USA": {"min": 0.04, "target": 0.08, "max": 0.13, "yield": 0.007, "expected": 0.069, "risk": 6},
    "Equity Italy": {"min": 0.04, "target": 0.09, "max": 0.14, "yield": 0.044, "expected": 0.052, "risk": 8},
    "Equity EM": {"min": 0.00, "target": 0.04, "max": 0.07, "yield": 0.012, "expected": 0.070, "risk": 8},
    "Equity Defensive": {"min": 0.04, "target": 0.07, "max": 0.11, "yield": 0.026, "expected": 0.054, "risk": 5},
    "Equity Europe ex-IT": {"min": 0.00, "target": 0.04, "max": 0.08, "yield": 0.036, "expected": 0.054, "risk": 7},
    "Gold": {"min": 0.02, "target": 0.04, "max": 0.08, "yield": 0.000, "expected": 0.030, "risk": 5},
    "Structured / Certificates": {"min": 0.00, "target": 0.02, "max": 0.05, "yield": 0.055, "expected": 0.035, "risk": 7},
    "Structured / Leveraged ETF": {"min": 0.00, "target": 0.00, "max": 0.005, "yield": 0.000, "expected": -0.020, "risk": 10},
    "Cash": {"min": 0.01, "target": 0.03, "max": 0.08, "yield": 0.022, "expected": 0.020, "risk": 1},
}

LINE_RE = re.compile(
    r"^(?P<operation_date>\d{2}\.\d{2}\.\d{2})\s+"
    r"(?P<value_date>\d{2}\.\d{2}\.\d{2})\s+"
    r"(?P<amount>[+-]?(?:\d{1,3}\.)*\d{1,3},\d{2})\s+"
    r"(?P<description>.+)$",
    re.IGNORECASE,
)

SECURITY_RE = re.compile(
    r"^(?P<prefix>Rit\.)?(?P<kind>ced|div)\.su\s+"
    r"(?P<quantity>(?:\d{1,3}\.)*\d{1,3},\d{3})\s+"
    r"(?P<instrument>.+)$",
    re.IGNORECASE,
)


def eur(value: float) -> str:
    return f"{value:,.0f} €".replace(",", "X").replace(".", ",").replace("X", ".")


def pct(value: float) -> str:
    return f"{value * 100:.1f}%".replace(".", ",")


def parse_amount(value: str) -> float:
    return float(value.replace(".", "").replace(",", "."))


def parse_date(value: str) -> str:
    return datetime.strptime(value, "%d.%m.%y").date().isoformat()


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


@st.cache_data(ttl=900, show_spinner=False)
def cached_market_snapshot(tickers: tuple[str, ...]) -> tuple[pd.DataFrame, str | None]:
    return fetch_market_snapshot(list(tickers))


@st.cache_data(ttl=900, show_spinner=False)
def cached_hot_trading_signals(
    tickers: tuple[str, ...],
    portfolio_tickers: tuple[str, ...],
    budget_eur: float,
    max_loss_pct: float,
    max_positions: int,
) -> tuple[pd.DataFrame, str | None]:
    return fetch_hot_trading_signals(list(tickers), list(portfolio_tickers), budget_eur, max_loss_pct, max_positions)


def parse_movement(line: str, page: int, source_file: str) -> dict[str, Any] | None:
    match = LINE_RE.match(clean_text(line))
    if not match:
        return None
    description = clean_text(match.group("description"))
    amount = parse_amount(match.group("amount"))
    payment_date = parse_date(match.group("operation_date"))
    value_date = parse_date(match.group("value_date"))
    lower = description.lower()

    security = SECURITY_RE.match(description)
    if security:
        event_type = "Cedola" if security.group("kind").lower() == "ced" else "Dividendo"
        is_tax = bool(security.group("prefix"))
        quantity = parse_amount(security.group("quantity"))
        return {
            "source_file": source_file,
            "payment_date": payment_date,
            "value_date": value_date,
            "amount": amount,
            "event_type": event_type,
            "cash_flow": "tax" if is_tax else "gross",
            "quantity": quantity,
            "instrument": clean_text(security.group("instrument")),
            "raw_description": description,
            "page": page,
        }

    if "interessi portaf. remun." in lower and "rit." not in lower:
        return {
            "source_file": source_file,
            "payment_date": payment_date,
            "value_date": value_date,
            "amount": amount,
            "event_type": "Interesse",
            "cash_flow": "gross",
            "quantity": None,
            "instrument": "Liquidità remunerata",
            "raw_description": description,
            "page": page,
        }

    if "rit. fisc. interessi portaf.remun." in lower:
        return {
            "source_file": source_file,
            "payment_date": payment_date,
            "value_date": value_date,
            "amount": amount,
            "event_type": "Interesse",
            "cash_flow": "tax",
            "quantity": None,
            "instrument": "Liquidità remunerata",
            "raw_description": description,
            "page": page,
        }
    return None


def parse_fineco_pdf(file_bytes: bytes, source_file: str) -> pd.DataFrame:
    reader = PdfReader(io.BytesIO(file_bytes))
    movements = []
    for page_number, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        for line in text.splitlines():
            movement = parse_movement(line, page_number, source_file)
            if movement:
                movements.append(movement)

    buckets: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for movement in movements:
        key = (
            movement["payment_date"],
            movement["value_date"],
            movement["event_type"],
            movement["instrument"].lower(),
            movement["quantity"],
            movement["source_file"],
        )
        buckets[key].append(movement)

    events = []
    for rows in buckets.values():
        gross_rows = [row for row in rows if row["cash_flow"] == "gross"]
        tax_rows = [row for row in rows if row["cash_flow"] == "tax"]
        for index in range(max(len(gross_rows), len(tax_rows))):
            gross = gross_rows[index] if index < len(gross_rows) else None
            tax = tax_rows[index] if index < len(tax_rows) else None
            base = gross or tax
            if not base:
                continue
            gross_amount = float(gross["amount"]) if gross else 0.0
            tax_amount = float(tax["amount"]) if tax else 0.0
            event = {
                "Fonte": base["source_file"],
                "Data pagamento": base["payment_date"],
                "Tipo": base["event_type"],
                "Strumento": base["instrument"],
                "Quantità": base["quantity"],
                "Lordo": round(gross_amount, 2),
                "Ritenuta": round(tax_amount, 2),
                "Netto": round(gross_amount - tax_amount, 2),
                "Tax rate": tax_amount / gross_amount if gross_amount else None,
                "Stato": "Completo" if gross and tax else "Da verificare",
            }
            basis = "|".join(str(event[key]) for key in ["Fonte", "Data pagamento", "Tipo", "Strumento", "Lordo", "Ritenuta"])
            event["ID"] = hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16]
            events.append(event)
    return pd.DataFrame(events).drop_duplicates("ID") if events else pd.DataFrame()


def assumption_table(labels: list[str]) -> pd.DataFrame:
    rows = []
    for label in labels:
        assumption = DEFAULT_ASSUMPTIONS.get(label, {"min": 0, "target": 0.03, "max": 0.06, "yield": 0.02, "expected": 0.035, "risk": 5})
        rows.append({"Sottoclasse": label, **assumption})
    return pd.DataFrame(rows)


def portfolio_summary(df: pd.DataFrame, assumptions: pd.DataFrame) -> dict[str, float]:
    total = float(df["market_value_eur"].sum())
    cost = float(df["total_cost_eur"].sum())
    gain = float(df["unrealized_gain_eur"].sum())
    accrued = float(df["accrued_interest_eur"].sum()) if "accrued_interest_eur" in df.columns else 0.0
    by_sub = df.groupby("sub_class_ips", dropna=False)["market_value_eur"].sum().reset_index()
    by_sub.columns = ["Sottoclasse", "Valore"]
    by_sub["Peso"] = by_sub["Valore"] / total if total else 0
    merged = by_sub.merge(assumptions, on="Sottoclasse", how="left").fillna({"yield": 0.02, "expected": 0.035, "risk": 5})
    gross_income = float((merged["Valore"] * merged["yield"]).sum())
    net_income = 0.0
    for _, holding in df.iterrows():
        assumption = assumptions.loc[assumptions["Sottoclasse"] == holding["sub_class_ips"]]
        gross_yield = float(assumption["yield"].iloc[0]) if not assumption.empty else 0.02
        tax = float(holding.get("tax_rate_applicable_pct", 0.26) or 0.26)
        net_income += float(holding["market_value_eur"]) * gross_yield * (1 - tax)
    expected = float((merged["Peso"] * merged["expected"]).sum())
    risk = float((merged["Peso"] * merged["risk"]).sum())
    return {
        "total": total,
        "cost": cost,
        "gain": gain,
        "gain_pct": gain / cost if cost else 0,
        "gross_income": gross_income,
        "net_income": net_income,
        "net_yield": net_income / total if total else 0,
        "expected": expected,
        "risk": risk,
        "accrued": accrued,
    }


st.sidebar.title("Portfolio Advisor")
st.sidebar.caption("App Streamlit deployabile via GitHub")

portfolio_file = st.sidebar.file_uploader(
    "Carica export portafoglio banca o IPS-ready",
    type=["xls", "xlsx"],
    help="Supporta l'export banca Portafoglio sintesi (.xls) e il workbook IPS-ready (.xlsx). Il file resta nella sessione Streamlit.",
)

fineco_files = st.sidebar.file_uploader(
    "Carica estratti Fineco PDF",
    type=["pdf"],
    accept_multiple_files=True,
)

income_target = st.sidebar.number_input("Obiettivo netto annuo", min_value=0, step=500, value=18000)

st.title("Portfolio Advisor")
st.caption("Analisi portafoglio, reddito atteso, incassi consuntivi e drift IPS.")

if not portfolio_file:
    st.info("Carica il workbook Excel del portafoglio dalla barra laterale per iniziare.")
    st.stop()

try:
    portfolio = parse_portfolio_excel(portfolio_file.getvalue(), portfolio_file.name)
except ValueError as exc:
    st.error(str(exc))
    st.stop()
except Exception as exc:
    st.error("Non riesco a leggere il file del portafoglio. Verifica che sia un export conto titoli o un workbook IPS-ready.")
    st.caption(str(exc))
    st.stop()

subclasses = sorted(set(portfolio["sub_class_ips"].fillna("Non classificato").astype(str)) | set(DEFAULT_ASSUMPTIONS))
assumptions = assumption_table(subclasses)
summary = portfolio_summary(portfolio, assumptions)
allocation = build_allocation_frame(portfolio, assumptions)

col1, col2, col3, col4 = st.columns(4)
col1.metric("Valore portafoglio", eur(summary["total"]), f"{len(portfolio)} posizioni")
col2.metric("P&L non realizzato", eur(summary["gain"]), pct(summary["gain_pct"]))
col3.metric("Reddito netto stimato", eur(summary["net_income"]), pct(summary["net_yield"]))
col4.metric("Rendimento atteso", pct(summary["expected"]), f"Rischio {summary['risk']:.1f}/10")

tabs = st.tabs(["Dashboard", "Advisor", "Hot Trading", "Mercati", "Allocazione", "Cedole & Dividendi", "Posizioni"])

with tabs[0]:
    left, right = st.columns([1.1, 0.9])
    by_asset = portfolio.groupby("asset_class_info_only", dropna=False)["market_value_eur"].sum().sort_values(ascending=False)
    by_sub = portfolio.groupby("sub_class_ips", dropna=False)["market_value_eur"].sum().sort_values(ascending=False)
    with left:
        st.subheader("Allocazione corrente")
        st.bar_chart(by_asset)
    with right:
        st.subheader("Priorità advisor")
        st.write(f"Reddito netto stimato: **{eur(summary['net_income'])}** su obiettivo **{eur(income_target)}**.")
        if summary["accrued"]:
            st.write(f"Ratei presenti nell'export: **{eur(summary['accrued'])}**.")
        if summary["net_income"] < income_target:
            st.warning(f"Gap reddito: {eur(summary['net_income'] - income_target)}")
        italy = portfolio[
            portfolio["sub_class_ips"].astype(str).str.contains("Italy", case=False, na=False)
            | portfolio["isin_or_ticker"].astype(str).str.startswith("IT")
        ]["market_value_eur"].sum()
        st.write(f"Home bias Italia stimato: **{pct(float(italy) / summary['total'])}**")
        st.write(f"Top 5 posizioni: **{pct(portfolio.nlargest(5, 'market_value_eur')['market_value_eur'].sum() / summary['total'])}**")

    st.subheader("Sottoclassi")
    st.dataframe(by_sub.rename("Valore EUR"), use_container_width=True)

with tabs[1]:
    st.subheader("Advisor decisionale")
    st.caption("Motore rule-based: evidenzia priorità e scenari da valutare, senza sostituire una consulenza regolamentata.")

    c1, c2, c3, c4 = st.columns(4)
    max_position_weight = c1.slider("Soglia posizione", min_value=3, max_value=20, value=8, step=1) / 100
    home_bias_limit = c2.slider("Limite Italia", min_value=20, max_value=70, value=40, step=5) / 100
    usd_limit = c3.slider("Limite USD", min_value=10, max_value=60, value=25, step=5) / 100
    structured_limit = c4.slider("Limite structured", min_value=0, max_value=20, value=7, step=1) / 100

    diagnostics = build_diagnostics(
        portfolio,
        allocation,
        summary,
        income_target,
        max_position_weight=max_position_weight,
        home_bias_limit=home_bias_limit,
        usd_limit=usd_limit,
        structured_limit=structured_limit,
    )
    recommendations = build_recommendations(
        portfolio,
        allocation,
        summary,
        income_target,
        max_position_weight=max_position_weight,
    )
    watchlist = build_watchlist(portfolio, allocation)

    high_priority = int((diagnostics["Priorità"] == "Alta").sum()) if not diagnostics.empty else 0
    medium_priority = int((diagnostics["Priorità"] == "Media").sum()) if not diagnostics.empty else 0
    suggested_amount = float(recommendations["Importo indicativo"].sum()) if not recommendations.empty else 0.0

    a1, a2, a3, a4 = st.columns(4)
    a1.metric("Priorità alta", str(high_priority))
    a2.metric("Priorità media", str(medium_priority))
    a3.metric("Azioni suggerite", str(len(recommendations)))
    a4.metric("Importo in esame", eur(suggested_amount))

    st.subheader("Diagnosi")
    if diagnostics.empty:
        st.success("Nessuna criticità rilevante con le soglie correnti.")
    else:
        st.dataframe(diagnostics, use_container_width=True, hide_index=True)

    st.subheader("Azioni suggerite")
    if recommendations.empty:
        st.info("Nessuna azione suggerita con le soglie correnti.")
    else:
        st.dataframe(
            recommendations,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Importo indicativo": st.column_config.NumberColumn(format="€ %.0f"),
            },
        )

    st.subheader("Watchlist investibile")
    watchlist_display = watchlist.copy()
    watchlist_display["Peso sottoclasse"] = watchlist_display["Peso sottoclasse"] * 100
    st.dataframe(
        watchlist_display,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Valore già in portafoglio": st.column_config.NumberColumn(format="€ %.0f"),
            "Peso sottoclasse": st.column_config.NumberColumn(format="%.1f%%"),
        },
    )

with tabs[2]:
    st.subheader("Hot Trading")
    st.caption("Sleeve tattica separata dal portafoglio core. Segnali tecnici su titoli non in portafoglio, con perdita massima della sleeve impostabile.")

    h1, h2, h3 = st.columns(3)
    hot_budget = h1.slider("Budget Hot Trading", min_value=500, max_value=3000, value=2500, step=250)
    hot_loss_pct = h2.slider("Perdita max sleeve", min_value=5, max_value=30, value=30, step=5) / 100
    hot_positions = h3.slider("Numero max idee", min_value=1, max_value=5, value=3, step=1)

    hot_loss_budget = hot_budget * hot_loss_pct
    b1, b2, b3 = st.columns(3)
    b1.metric("Budget sleeve", eur(float(hot_budget)))
    b2.metric("Perdita massima", eur(float(hot_loss_budget)), pct(hot_loss_pct))
    b3.metric("Rischio per idea", eur(float(hot_loss_budget / hot_positions)))

    hot_labels = {item["ticker"]: f"{item['Nome']} ({item['ticker']})" for item in HOT_TRADING_UNIVERSE}
    default_hot = [item["ticker"] for item in HOT_TRADING_UNIVERSE[:10]]
    selected_hot = st.multiselect(
        "Universo Hot Trading",
        options=list(hot_labels),
        default=default_hot,
        format_func=hot_labels.get,
    )
    custom_hot = st.text_input("Ticker custom separati da virgola", value="")
    custom_tickers = [ticker.strip().upper() for ticker in custom_hot.split(",") if ticker.strip()]

    held_tickers: list[str] = []
    if "ticker" in portfolio.columns:
        held_tickers = sorted(
            {
                str(ticker).strip().upper()
                for ticker in portfolio["ticker"].dropna().tolist()
                if str(ticker).strip() and str(ticker).strip() != "-"
            }
        )
    hot_tickers = tuple(dict.fromkeys(selected_hot + custom_tickers))
    run_hot = st.toggle("Calcola segnali live", value=False)

    if not run_hot:
        st.info("Attiva il calcolo live per scaricare i dati e generare segnali tecnici.")
    elif not hot_tickers:
        st.info("Seleziona almeno un ticker per Hot Trading.")
    else:
        hot_signals, hot_error = cached_hot_trading_signals(hot_tickers, tuple(held_tickers), float(hot_budget), float(hot_loss_pct), int(hot_positions))
        if hot_error:
            st.warning(hot_error)
        if not hot_signals.empty:
            show_neutral = st.checkbox("Mostra anche segnali neutrali", value=False)
            filtered_signals = hot_signals if show_neutral else hot_signals[hot_signals["Direzione"] != "No trade"]
            if filtered_signals.empty:
                st.info("Nessun segnale operativo forte con le soglie tecniche correnti.")
            else:
                hot_display = filtered_signals.copy()
                for column in ["5D", "1M", "3M", "Vol 20g ann.", "Stop tecnico"]:
                    hot_display[column] = hot_display[column] * 100
                st.dataframe(
                    hot_display[
                        [
                            "ticker",
                            "Nome",
                            "Area",
                            "Segnale",
                            "Direzione",
                            "Score",
                            "Confidenza",
                            "Ultimo",
                            "5D",
                            "1M",
                            "3M",
                            "RSI 14",
                            "Vol 20g ann.",
                            "Stop tecnico",
                            "Size max",
                            "Rischio stimato",
                            "Stop price",
                            "Target tattico",
                            "Motivo",
                            "Aggiornato",
                        ]
                    ],
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "Ultimo": st.column_config.NumberColumn(format="%.2f"),
                        "5D": st.column_config.NumberColumn(format="%.1f%%"),
                        "1M": st.column_config.NumberColumn(format="%.1f%%"),
                        "3M": st.column_config.NumberColumn(format="%.1f%%"),
                        "RSI 14": st.column_config.NumberColumn(format="%.1f"),
                        "Vol 20g ann.": st.column_config.NumberColumn(format="%.1f%%"),
                        "Stop tecnico": st.column_config.NumberColumn(format="%.1f%%"),
                        "Size max": st.column_config.NumberColumn(format="€ %.0f"),
                        "Rischio stimato": st.column_config.NumberColumn(format="€ %.0f"),
                        "Stop price": st.column_config.NumberColumn(format="%.2f"),
                        "Target tattico": st.column_config.NumberColumn(format="%.2f"),
                    },
                )

with tabs[3]:
    st.subheader("Monitor mercati")
    st.caption("Dati pubblici via Yahoo Finance, usati come proxy di contesto. Non includono importi o dati personali.")

    indicator_options = {item["ticker"]: f"{item['Nome']} ({item['ticker']})" for item in MARKET_INDICATORS}
    default_indicators = [item["ticker"] for item in MARKET_INDICATORS[:9]]
    selected_indicators = st.multiselect(
        "Indicatori macro/mercato",
        options=list(indicator_options),
        default=default_indicators,
        format_func=indicator_options.get,
    )
    include_portfolio_tickers = st.checkbox("Includi ticker quotati del portafoglio", value=True)
    portfolio_tickers: list[str] = []
    if include_portfolio_tickers and "ticker" in portfolio.columns:
        portfolio_tickers = sorted(
            {
                str(ticker).strip()
                for ticker in portfolio["ticker"].dropna().tolist()
                if str(ticker).strip() and str(ticker).strip() != "-"
            }
        )

    market_tickers = tuple(dict.fromkeys(selected_indicators + portfolio_tickers))
    run_market = st.toggle("Aggiorna monitor mercati live", value=False)
    if not run_market:
        st.info("Attiva il monitor live per scaricare dati di mercato aggiornati.")
    elif not market_tickers:
        st.info("Seleziona almeno un indicatore di mercato.")
    else:
        market_data, market_error = cached_market_snapshot(market_tickers)
        if market_error:
            st.warning(market_error)
        if not market_data.empty:
            market_labels = pd.DataFrame(MARKET_INDICATORS).rename(columns={"ticker": "ticker"})
            market_data = market_data.merge(market_labels[["ticker", "Nome", "Area", "Tipo"]], on="ticker", how="left")
            market_data["Nome"] = market_data["Nome"].fillna(market_data["ticker"])
            market_data["Area"] = market_data["Area"].fillna("Portafoglio")
            market_data["Tipo"] = market_data["Tipo"].fillna("Strumento")
            market_data = market_data[["ticker", "Nome", "Area", "Tipo", "Ultimo", "1M", "3M", "YTD", "1Y", "Vol 3M ann.", "Trend 50g", "Aggiornato"]]
            market_display = market_data.copy()
            for column in ["1M", "3M", "YTD", "1Y", "Vol 3M ann.", "Trend 50g"]:
                market_display[column] = market_display[column] * 100
            st.dataframe(
                market_display.sort_values(["Area", "Nome"]),
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Ultimo": st.column_config.NumberColumn(format="%.2f"),
                    "1M": st.column_config.NumberColumn(format="%.1f%%"),
                    "3M": st.column_config.NumberColumn(format="%.1f%%"),
                    "YTD": st.column_config.NumberColumn(format="%.1f%%"),
                    "1Y": st.column_config.NumberColumn(format="%.1f%%"),
                    "Vol 3M ann.": st.column_config.NumberColumn(format="%.1f%%"),
                    "Trend 50g": st.column_config.NumberColumn(format="%.1f%%"),
                },
            )
            weak_markets = market_data[(market_data["3M"].fillna(0) < -0.08) | (market_data["Trend 50g"].fillna(0) < -0.05)]
            strong_markets = market_data[(market_data["3M"].fillna(0) > 0.08) & (market_data["Trend 50g"].fillna(0) > 0.03)]
            m1, m2, m3 = st.columns(3)
            m1.metric("Strumenti monitorati", str(len(market_data)))
            m2.metric("Deboli / sotto trend", str(len(weak_markets)))
            m3.metric("Forti / sopra trend", str(len(strong_markets)))

with tabs[4]:
    allocation_display = allocation.copy()
    for column in ["Peso attuale", "min", "target", "max", "yield"]:
        allocation_display[column] = allocation_display[column] * 100
    st.dataframe(
        allocation_display[["Sottoclasse", "Peso attuale", "min", "target", "max", "yield", "Drift vs target EUR", "Stato"]],
        use_container_width=True,
        column_config={
            "Peso attuale": st.column_config.NumberColumn(format="%.1f%%"),
            "min": st.column_config.NumberColumn(format="%.1f%%"),
            "target": st.column_config.NumberColumn(format="%.1f%%"),
            "max": st.column_config.NumberColumn(format="%.1f%%"),
            "yield": st.column_config.NumberColumn(format="%.1f%%"),
        },
    )

with tabs[5]:
    income_frames = []
    for uploaded_pdf in fineco_files:
        income_frames.append(parse_fineco_pdf(uploaded_pdf.getvalue(), uploaded_pdf.name))
    income = pd.concat(income_frames, ignore_index=True).drop_duplicates("ID") if income_frames else pd.DataFrame()

    if income.empty:
        st.info("Carica uno o più PDF Fineco nella barra laterale per vedere il consuntivo cedole/dividendi.")
    else:
        income["Data pagamento"] = pd.to_datetime(income["Data pagamento"])
        selected_year = int(income["Data pagamento"].dt.year.max())
        annual = income[income["Data pagamento"].dt.year == selected_year]
        c1, c2, c3, c4 = st.columns(4)
        c1.metric(f"Netto {selected_year}", eur(float(annual["Netto"].sum())))
        c2.metric("Lordo", eur(float(annual["Lordo"].sum())))
        c3.metric("Ritenute", eur(float(annual["Ritenuta"].sum())))
        c4.metric("Eventi", str(len(annual)))

        by_type = annual.groupby("Tipo")["Netto"].sum().sort_values(ascending=False)
        st.bar_chart(by_type)
        st.dataframe(
            annual.sort_values("Data pagamento", ascending=False)[
                ["Data pagamento", "Tipo", "Strumento", "Quantità", "Lordo", "Ritenuta", "Netto", "Tax rate", "Fonte", "Stato"]
            ],
            use_container_width=True,
        )

with tabs[6]:
    st.dataframe(
        portfolio.sort_values("market_value_eur", ascending=False),
        use_container_width=True,
    )

st.caption("Supporto decisionale. Non sostituisce consulenza finanziaria regolamentata.")
