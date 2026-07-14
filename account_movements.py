from __future__ import annotations

import hashlib
import re
from typing import Any

import pandas as pd

from portfolio_import import open_excel


MOVEMENT_REQUIRED_HEADERS = {
    "data_operazione",
    "data_valuta",
    "entrate",
    "uscite",
    "descrizione",
    "descrizione_completa",
}

SECURITY_RE = re.compile(
    r"^(?P<prefix>Rit\.|Riten\.|Add\.rit\.)?"
    r"\s*(?P<tax_rate>\d{1,2}(?:,\d+)?%)?\s*"
    r"(?P<kind>ced|div)\.su\s+"
    r"(?P<quantity>(?:\d{1,3}\.)*\d{1,3},\d{3})\s+"
    r"(?P<instrument>.+)$",
    re.IGNORECASE,
)

PORTFOLIO_REMUNERATION_DIVIDEND_RE = re.compile(
    r"^(?P<prefix>Acc\.div\.|Add\.rit\.)Port\.Rem\.\s+(?P<instrument>.+)$",
    re.IGNORECASE,
)


def clean_text(value: Any) -> str:
    if pd.isna(value):
        return ""
    return re.sub(r"\s+", " ", str(value).strip())


def normalize_header(value: Any) -> str:
    text = clean_text(value).casefold()
    text = text.replace(" ", "_").replace("-", "_")
    return text


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


def instrument_key(value: Any) -> str:
    return re.sub(r"\s+", "", clean_text(value).casefold())


def parse_date(value: Any) -> str | None:
    parsed = pd.to_datetime(value, dayfirst=True, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.date().isoformat()


def find_movements_header_row(raw: pd.DataFrame) -> int | None:
    for index, row in raw.iterrows():
        values = {normalize_header(value) for value in row.tolist() if clean_text(value)}
        if len(MOVEMENT_REQUIRED_HEADERS.intersection(values)) >= 5:
            return int(index)
    return None


def read_current_account_movements(file_bytes: bytes, file_name: str = "") -> pd.DataFrame:
    xls = open_excel(file_bytes, file_name)
    errors = []
    for sheet_name in xls.sheet_names:
        raw = pd.read_excel(xls, sheet_name=sheet_name, header=None)
        header_row = find_movements_header_row(raw)
        if header_row is None:
            errors.append(f"{sheet_name}: intestazione movimenti non trovata")
            continue

        headers = [clean_text(value) for value in raw.iloc[header_row].tolist()]
        data = raw.iloc[header_row + 1 :].copy()
        data.columns = headers
        data = data.dropna(how="all").copy()
        return normalize_movements_dataframe(data, file_name)

    raise ValueError("Formato movimenti conto corrente non riconosciuto. " + " | ".join(errors))


def normalize_movements_dataframe(data: pd.DataFrame, source_file: str) -> pd.DataFrame:
    normalized = pd.DataFrame(
        {
            "Fonte": source_file,
            "Data operazione": data.get("Data_Operazione", pd.Series(index=data.index)).map(parse_date),
            "Data valuta": data.get("Data_Valuta", pd.Series(index=data.index)).map(parse_date),
            "Entrate": data.get("Entrate", pd.Series(index=data.index)).map(parse_number),
            "Uscite": data.get("Uscite", pd.Series(index=data.index)).map(parse_number),
            "Descrizione": data.get("Descrizione", pd.Series(index=data.index)).map(clean_text),
            "Descrizione completa": data.get("Descrizione_Completa", pd.Series(index=data.index)).map(clean_text),
            "Stato": data.get("Stato", pd.Series(index=data.index)).map(clean_text),
            "Moneymap": data.get("Moneymap", pd.Series(index=data.index)).map(clean_text),
        }
    )
    normalized["Importo"] = normalized["Entrate"] + normalized["Uscite"]
    normalized = normalized[normalized["Data operazione"].notna()].copy()
    normalized["Tipo movimento"] = normalized.apply(classify_movement_row, axis=1)
    return normalized.reset_index(drop=True)


def parse_current_account_excel(file_bytes: bytes, file_name: str = "") -> tuple[pd.DataFrame, pd.DataFrame]:
    movements = read_current_account_movements(file_bytes, file_name)
    income_movements = []
    for _, row in movements.iterrows():
        movement = movement_to_income_component(row)
        if movement:
            income_movements.append(movement)

    income = build_income_events(income_movements)
    return income, movements


def classify_movement_row(row: pd.Series) -> str:
    lower = f"{row.get('Descrizione', '')} {row.get('Descrizione completa', '')}".casefold()
    if any(term in lower for term in ("dividendo", "div.su", "acc.div.port.rem")):
        return "Dividendo"
    if any(term in lower for term in ("cedola", "ced.su")):
        return "Cedola"
    if "interessi portaf" in lower:
        return "Interesse"
    if "rit" in lower and any(term in lower for term in ("div", "ced", "interessi")):
        return "Ritenuta"
    if row.get("Entrate", 0) > 0:
        return "Entrata"
    if row.get("Uscite", 0) < 0:
        return "Uscita"
    return "Altro"


def movement_to_income_component(row: pd.Series) -> dict[str, Any] | None:
    full_description = clean_text(row.get("Descrizione completa"))
    description = clean_text(row.get("Descrizione"))
    lower = f"{description} {full_description}".casefold()
    entry = float(row.get("Entrate", 0) or 0)
    exit_amount = abs(float(row.get("Uscite", 0) or 0))

    security = parse_security_description(full_description)
    if security:
        event_type = "Cedola" if security["kind"] == "ced" else "Dividendo"
        is_tax = security["is_tax"] or exit_amount > 0 or "riten" in lower
        amount = exit_amount if is_tax else entry
        if amount == 0:
            return None
        return {
            "source_file": row.get("Fonte"),
            "payment_date": row.get("Data operazione"),
            "value_date": row.get("Data valuta"),
            "amount": abs(amount),
            "event_type": event_type,
            "cash_flow": "tax" if is_tax else "gross",
            "quantity": security["quantity"],
            "instrument": security["instrument"],
            "raw_description": full_description or description,
        }

    remuneration_dividend = PORTFOLIO_REMUNERATION_DIVIDEND_RE.match(full_description)
    if remuneration_dividend:
        is_tax = bool(remuneration_dividend.group("prefix") and "rit" in remuneration_dividend.group("prefix").casefold())
        amount = exit_amount if is_tax else entry
        if amount == 0:
            return None
        return {
            "source_file": row.get("Fonte"),
            "payment_date": row.get("Data operazione"),
            "value_date": row.get("Data valuta"),
            "amount": abs(amount),
            "event_type": "Dividendo",
            "cash_flow": "tax" if is_tax else "gross",
            "quantity": None,
            "instrument": clean_text(remuneration_dividend.group("instrument")),
            "raw_description": full_description or description,
        }

    if "interessi portaf" in lower:
        is_tax = "rit" in lower or exit_amount > 0
        amount = exit_amount if is_tax else entry
        if amount == 0:
            return None
        return {
            "source_file": row.get("Fonte"),
            "payment_date": row.get("Data operazione"),
            "value_date": row.get("Data valuta"),
            "amount": abs(amount),
            "event_type": "Interesse",
            "cash_flow": "tax" if is_tax else "gross",
            "quantity": None,
            "instrument": "Liquidità remunerata",
            "raw_description": full_description or description,
        }

    return None


def parse_security_description(description: str) -> dict[str, Any] | None:
    match = SECURITY_RE.match(clean_text(description))
    if not match:
        return None
    prefix = clean_text(match.group("prefix")).casefold()
    return {
        "kind": match.group("kind").casefold(),
        "is_tax": bool(prefix),
        "quantity": parse_number(match.group("quantity")),
        "instrument": clean_text(match.group("instrument")),
    }


def build_income_events(components: list[dict[str, Any]]) -> pd.DataFrame:
    if not components:
        return pd.DataFrame()

    rows = pd.DataFrame(components)
    rows["instrument_key"] = rows["instrument"].map(instrument_key)
    group_columns = [
        "source_file",
        "payment_date",
        "value_date",
        "event_type",
        "instrument_key",
        "quantity",
    ]
    events = []
    for _, group in rows.groupby(group_columns, dropna=False):
        gross_rows = group[group["cash_flow"] == "gross"]
        base = gross_rows.iloc[0] if not gross_rows.empty else group.iloc[0]
        gross_amount = float(group.loc[group["cash_flow"] == "gross", "amount"].sum())
        tax_amount = float(group.loc[group["cash_flow"] == "tax", "amount"].sum())
        status = "Completo"
        if gross_amount and not tax_amount:
            status = "Solo lordo"
        elif tax_amount and not gross_amount:
            status = "Solo ritenuta"

        event = {
            "Fonte": base["source_file"],
            "Data pagamento": base["payment_date"],
            "Tipo": base["event_type"],
            "Strumento": base["instrument"],
            "Quantità": base["quantity"] if pd.notna(base["quantity"]) else None,
            "Lordo": round(gross_amount, 2),
            "Ritenuta": round(tax_amount, 2),
            "Netto": round(gross_amount - tax_amount, 2),
            "Tax rate": tax_amount / gross_amount if gross_amount else None,
            "Stato": status,
        }
        basis = "|".join(str(event[key]) for key in ["Fonte", "Data pagamento", "Tipo", "Strumento", "Lordo", "Ritenuta"])
        event["ID"] = hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16]
        events.append(event)

    return pd.DataFrame(events).drop_duplicates("ID")
