from __future__ import annotations

import io
import re
from typing import Any

import pandas as pd


NUMERIC_COLUMNS = [
    "quantity",
    "price",
    "market_value_eur",
    "portfolio_weight_pct",
    "avg_cost",
    "total_cost_eur",
    "unrealized_gain_eur",
    "unrealized_gain_pct",
    "tax_rate_applicable_pct",
]

IPS_SHEET_NAME = "Portfolio_Holdings_IPS"
IPS_REQUIRED_COLUMNS = {"market_value_eur", "sub_class_ips"}
OLE2_SIGNATURE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

BANK_REQUIRED_HEADERS = {
    "titolo",
    "isin",
    "strumento",
    "valuta",
    "quantita",
    "valore di mercato €",
}


def clean_text(value: Any) -> str:
    if pd.isna(value):
        return ""
    return re.sub(r"\s+", " ", str(value).strip())


def normalize_header(value: Any) -> str:
    text = clean_text(value).casefold()
    return text.replace("quantità", "quantita")


def parse_number(value: Any) -> float:
    if pd.isna(value) or value == "":
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)

    text = clean_text(value)
    if text in {"", "-"}:
        return 0.0
    text = text.replace("€", "").replace("%", "").replace("\xa0", "").replace(" ", "")

    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".")
    elif "," in text:
        text = text.replace(",", ".")

    return float(text)


def excel_engine_for(file_bytes: bytes, file_name: str = "") -> str | None:
    lower_name = file_name.casefold()
    if lower_name.endswith(".xls") or file_bytes.startswith(OLE2_SIGNATURE):
        return "xlrd"
    return None


def open_excel(file_bytes: bytes, file_name: str = "") -> pd.ExcelFile:
    engine = excel_engine_for(file_bytes, file_name)
    try:
        return pd.ExcelFile(io.BytesIO(file_bytes), engine=engine)
    except ImportError as exc:
        if engine == "xlrd" or "xlrd" in str(exc).casefold():
            raise ValueError(
                "Questo export .xls richiede la dipendenza 'xlrd'. "
                "Aggiorna/installa requirements.txt e riavvia l'app."
            ) from exc
        raise


def normalize_ips_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [clean_text(column) for column in df.columns]
    df = df.dropna(how="all").copy()

    if "market_value_eur" not in df.columns:
        raise ValueError("Il workbook IPS-ready non contiene la colonna market_value_eur.")

    for column in NUMERIC_COLUMNS:
        if column in df.columns:
            df[column] = pd.to_numeric(df[column], errors="coerce").fillna(0)

    if "total_cost_eur" not in df.columns:
        df["total_cost_eur"] = df["market_value_eur"]
    if "unrealized_gain_eur" not in df.columns:
        df["unrealized_gain_eur"] = df["market_value_eur"] - df["total_cost_eur"]
    if "sub_class_ips" not in df.columns:
        df["sub_class_ips"] = "Non classificato"
    if "asset_class_info_only" not in df.columns:
        df["asset_class_info_only"] = df["sub_class_ips"]
    if "isin_or_ticker" not in df.columns:
        df["isin_or_ticker"] = ""
    if "tax_rate_applicable_pct" not in df.columns:
        df["tax_rate_applicable_pct"] = 0.26

    total = float(df["market_value_eur"].sum())
    df["computed_weight"] = df["market_value_eur"] / total if total else 0
    return df


def dedupe_headers(headers: list[str]) -> list[str]:
    counts: dict[str, int] = {}
    result = []
    for index, header in enumerate(headers):
        name = header or f"Unnamed {index + 1}"
        counts[name] = counts.get(name, 0) + 1
        result.append(name if counts[name] == 1 else f"{name}_{counts[name]}")
    return result


def find_bank_header_row(raw: pd.DataFrame) -> int | None:
    for index, row in raw.iterrows():
        values = {normalize_header(value) for value in row.tolist() if clean_text(value)}
        if len(BANK_REQUIRED_HEADERS.intersection(values)) >= 5:
            return int(index)
    return None


def column_or_empty(df: pd.DataFrame, column: str) -> pd.Series:
    if column in df.columns:
        return df[column]
    return pd.Series([""] * len(df), index=df.index)


def numeric_column(df: pd.DataFrame, column: str) -> pd.Series:
    return column_or_empty(df, column).map(parse_number)


def text_column(df: pd.DataFrame, column: str) -> pd.Series:
    return column_or_empty(df, column).map(clean_text)


def is_bond_like(title: str, instrument: str) -> bool:
    bond_terms = ("BOND", "BND", "BTP", "OBBLIG", "GOVT")
    return "obblig" in instrument or any(term in title for term in bond_terms)


def is_equity_like(instrument: str) -> bool:
    return any(term in instrument for term in ("azione", "etf", "fondo", "pac"))


def classify_subclass(row: pd.Series) -> str:
    title = clean_text(row.get("instrument_name")).upper()
    instrument = clean_text(row.get("instrument_type")).casefold()
    isin = clean_text(row.get("isin")).upper()
    ticker = clean_text(row.get("ticker")).upper()
    market = clean_text(row.get("market")).upper()

    if any(term in title for term in ("3X", "LEVERAGED", "DAILY SHORT", "SHORT")):
        return "Structured / Leveraged ETF"
    if "certificate" in instrument:
        return "Structured / Certificates"
    if any(term in title for term in ("GOLD", "ORO")):
        return "Gold"

    if is_bond_like(title, instrument):
        if title.startswith("BTP") or "ITALY GOVT" in title:
            return "Bonds Italy Gov"
        if any(term in title for term in ("HIGH YIELD", "HIG YIE", "HI YL")):
            return "Bonds High Yield"
        if any(term in title for term in ("FIN CR", "FINANCIAL CREDIT", "FINANC")):
            return "Bonds Financial Credit"
        if isin.startswith("IT"):
            return "Bonds Italy Corp"
        return "Bonds ex-Italy Corp IG"

    if is_equity_like(instrument):
        if any(term in title for term in ("CHINA", "EMERGING", " MSCI EM", " EM ")):
            return "Equity EM"
        if any(term in title for term in ("MINIMUM VOLATILITY", "LOW VOL", "HIGH DIVIDEND", "DIVIDEND YIELD")):
            return "Equity Defensive"
        if market == "NASDAQ" or ticker.endswith(".O") or isin.startswith("US"):
            return "Equity USA"
        if "azione" in instrument and (isin.startswith("IT") or ticker.endswith(".MI")):
            return "Equity Italy"
        if "EUROPE" in title:
            return "Equity Europe ex-IT"
        return "Equity Global Core"

    return "Non classificato"


def classify_asset_class(row: pd.Series) -> str:
    subclass = clean_text(row.get("sub_class_ips"))
    instrument = clean_text(row.get("instrument_type")).casefold()

    if subclass.startswith("Bonds"):
        return "Bonds"
    if subclass.startswith("Equity"):
        return "Equity"
    if subclass == "Gold":
        return "Gold"
    if "certificate" in instrument or subclass.startswith("Structured"):
        return "Structured"
    if "fondo" in instrument:
        return "Funds"
    return clean_text(row.get("instrument_type")) or "Other"


def applicable_tax_rate(row: pd.Series) -> float:
    if clean_text(row.get("sub_class_ips")) == "Bonds Italy Gov":
        return 0.125
    return 0.26


def parse_bank_portfolio_sheet(raw: pd.DataFrame, sheet_name: str) -> pd.DataFrame:
    header_row = find_bank_header_row(raw)
    if header_row is None:
        raise ValueError("Non trovo la riga intestazione dell'export portafoglio banca.")

    headers = dedupe_headers([clean_text(value) for value in raw.iloc[header_row].tolist()])
    data = raw.iloc[header_row + 1 :].copy()
    data.columns = headers
    data = data.dropna(how="all").copy()

    title = text_column(data, "Titolo")
    isin = text_column(data, "ISIN")
    data = data[(title != "") & (isin.str.len() >= 8)].copy()

    portfolio = pd.DataFrame(
        {
            "instrument_name": text_column(data, "Titolo"),
            "isin": text_column(data, "ISIN"),
            "ticker": text_column(data, "Simbolo"),
            "market": text_column(data, "Mercato"),
            "instrument_type": text_column(data, "Strumento"),
            "currency": text_column(data, "Valuta"),
            "quantity": numeric_column(data, "Quantità"),
            "avg_cost": numeric_column(data, "P.zo medio di carico"),
            "load_exchange_rate": numeric_column(data, "Cambio di carico"),
            "total_cost_eur": numeric_column(data, "Valore di carico"),
            "price": numeric_column(data, "P.zo di mercato"),
            "market_exchange_rate": numeric_column(data, "Cambio di mercato"),
            "market_value_eur": numeric_column(data, "Valore di mercato €"),
            "unrealized_gain_pct": numeric_column(data, "Var%") / 100,
            "unrealized_gain_eur": numeric_column(data, "Var €"),
            "unrealized_gain_ccy": numeric_column(data, "Var in valuta"),
            "accrued_interest_eur": numeric_column(data, "Rateo"),
            "source_sheet": sheet_name,
            "source_format": "bank_export",
        }
    )
    portfolio["isin_or_ticker"] = portfolio["isin"].where(portfolio["isin"] != "", portfolio["ticker"])
    portfolio["sub_class_ips"] = portfolio.apply(classify_subclass, axis=1)
    portfolio["asset_class_info_only"] = portfolio.apply(classify_asset_class, axis=1)
    portfolio["tax_rate_applicable_pct"] = portfolio.apply(applicable_tax_rate, axis=1)

    total = float(portfolio["market_value_eur"].sum())
    portfolio["computed_weight"] = portfolio["market_value_eur"] / total if total else 0
    portfolio["portfolio_weight_pct"] = portfolio["computed_weight"]
    return portfolio


def parse_bank_portfolio(xls: pd.ExcelFile) -> pd.DataFrame:
    errors = []
    for sheet_name in xls.sheet_names:
        raw = pd.read_excel(xls, sheet_name=sheet_name, header=None)
        try:
            return parse_bank_portfolio_sheet(raw, sheet_name)
        except ValueError as exc:
            errors.append(f"{sheet_name}: {exc}")
    raise ValueError("Formato portafoglio non riconosciuto. " + " | ".join(errors))


def parse_portfolio_excel(file_bytes: bytes, file_name: str = "") -> pd.DataFrame:
    xls = open_excel(file_bytes, file_name)

    if IPS_SHEET_NAME in xls.sheet_names:
        return normalize_ips_dataframe(pd.read_excel(xls, sheet_name=IPS_SHEET_NAME))

    for sheet_name in xls.sheet_names:
        first_sheet = pd.read_excel(xls, sheet_name=sheet_name)
        first_sheet.columns = [clean_text(column) for column in first_sheet.columns]
        if IPS_REQUIRED_COLUMNS.issubset(first_sheet.columns):
            return normalize_ips_dataframe(first_sheet)

    return parse_bank_portfolio(xls)
