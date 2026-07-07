from __future__ import annotations

import math
from datetime import datetime
from typing import Any

import pandas as pd


MARKET_INDICATORS = [
    {"ticker": "SPY", "Nome": "S&P 500", "Area": "Azionario USA", "Tipo": "Proxy mercato"},
    {"ticker": "EFA", "Nome": "Azionario sviluppati ex USA", "Area": "Azionario globale", "Tipo": "Proxy mercato"},
    {"ticker": "EEM", "Nome": "Azionario emergenti", "Area": "Emergenti", "Tipo": "Proxy mercato"},
    {"ticker": "EWU", "Nome": "Azionario UK", "Area": "Europa", "Tipo": "Proxy mercato"},
    {"ticker": "EWI", "Nome": "Azionario Italia", "Area": "Italia", "Tipo": "Proxy mercato"},
    {"ticker": "IEF", "Nome": "Treasury USA 7-10 anni", "Area": "Tassi", "Tipo": "Proxy mercato"},
    {"ticker": "LQD", "Nome": "Credito investment grade", "Area": "Credito", "Tipo": "Proxy mercato"},
    {"ticker": "HYG", "Nome": "High yield", "Area": "Credito", "Tipo": "Proxy mercato"},
    {"ticker": "GLD", "Nome": "Oro", "Area": "Materie prime", "Tipo": "Proxy mercato"},
    {"ticker": "EURUSD=X", "Nome": "EUR/USD", "Area": "Valute", "Tipo": "Cambio"},
    {"ticker": "^VIX", "Nome": "VIX", "Area": "Volatilità", "Tipo": "Indicatore"},
]


INVESTABLE_WATCHLIST = [
    {
        "ticker": "SWDA.MI",
        "Nome": "iShares Core MSCI World",
        "Sottoclasse": "Equity Global Core",
        "Ruolo": "Core azionario globale",
        "Uso": "Incremento core equity se sotto target",
    },
    {
        "ticker": "VWCE.MI",
        "Nome": "Vanguard FTSE All-World Acc",
        "Sottoclasse": "Equity Global Core",
        "Ruolo": "Core azionario globale",
        "Uso": "Alternativa globale diversificata",
    },
    {
        "ticker": "VHYL.MI",
        "Nome": "Vanguard High Dividend Yield",
        "Sottoclasse": "Equity Defensive",
        "Ruolo": "Equity income",
        "Uso": "Reddito azionario con profilo difensivo",
    },
    {
        "ticker": "IEAC.MI",
        "Nome": "iShares Core EUR Corp Bond",
        "Sottoclasse": "Bonds ex-Italy Corp IG",
        "Ruolo": "Credito EUR IG",
        "Uso": "Stabilizzatore obbligazionario diversificato",
    },
    {
        "ticker": "IITB.MI",
        "Nome": "iShares Italy Govt Bond",
        "Sottoclasse": "Bonds Italy Gov",
        "Ruolo": "Governativi Italia",
        "Uso": "Esposizione BTP diversificata",
    },
    {
        "ticker": "SGLD.MI",
        "Nome": "Invesco Physical Gold",
        "Sottoclasse": "Gold",
        "Ruolo": "Diversificatore",
        "Uso": "Copertura parziale di stress e inflazione",
    },
]


HOT_TRADING_UNIVERSE = [
    {"ticker": "AAPL", "Nome": "Apple", "Area": "Mega cap USA"},
    {"ticker": "MSFT", "Nome": "Microsoft", "Area": "Mega cap USA"},
    {"ticker": "NVDA", "Nome": "Nvidia", "Area": "Semiconduttori"},
    {"ticker": "AMD", "Nome": "AMD", "Area": "Semiconduttori"},
    {"ticker": "AVGO", "Nome": "Broadcom", "Area": "Semiconduttori"},
    {"ticker": "GOOGL", "Nome": "Alphabet", "Area": "Mega cap USA"},
    {"ticker": "META", "Nome": "Meta Platforms", "Area": "Mega cap USA"},
    {"ticker": "TSLA", "Nome": "Tesla", "Area": "Alta volatilità"},
    {"ticker": "NFLX", "Nome": "Netflix", "Area": "Consumer tech"},
    {"ticker": "ASML.AS", "Nome": "ASML", "Area": "Europa tech"},
    {"ticker": "SAP.DE", "Nome": "SAP", "Area": "Europa tech"},
    {"ticker": "MC.PA", "Nome": "LVMH", "Area": "Europa consumer"},
    {"ticker": "RACE.MI", "Nome": "Ferrari", "Area": "Italia quality"},
    {"ticker": "ENEL.MI", "Nome": "Enel", "Area": "Italia difensivo"},
    {"ticker": "LDO.MI", "Nome": "Leonardo", "Area": "Italia momentum"},
    {"ticker": "STM.MI", "Nome": "STMicroelectronics", "Area": "Italia tech"},
]


def build_allocation_frame(portfolio: pd.DataFrame, assumptions: pd.DataFrame) -> pd.DataFrame:
    total = float(portfolio["market_value_eur"].sum())
    current = portfolio.groupby("sub_class_ips", dropna=False)["market_value_eur"].sum().reset_index()
    current.columns = ["Sottoclasse", "Valore"]
    current["Peso attuale"] = current["Valore"] / total if total else 0.0

    allocation = assumptions.merge(current, on="Sottoclasse", how="left").fillna({"Valore": 0.0, "Peso attuale": 0.0})
    allocation["Drift vs target EUR"] = (allocation["target"] - allocation["Peso attuale"]) * total
    allocation["Sforamento EUR"] = allocation.apply(
        lambda row: max(0.0, (row["Peso attuale"] - row["max"]) * total)
        if row["Peso attuale"] > row["max"]
        else max(0.0, (row["min"] - row["Peso attuale"]) * total)
        if row["Peso attuale"] < row["min"]
        else 0.0,
        axis=1,
    )
    allocation["Stato"] = allocation.apply(
        lambda row: "Sopra max" if row["Peso attuale"] > row["max"] else "Sotto min" if row["Peso attuale"] < row["min"] else "In banda",
        axis=1,
    )
    return allocation.sort_values("Valore", ascending=False)


def _priority_from_gap(gap_pct: float) -> str:
    if gap_pct >= 0.05:
        return "Alta"
    if gap_pct >= 0.02:
        return "Media"
    return "Bassa"


def _priority_score(priority: str) -> int:
    return {"Alta": 0, "Media": 1, "Bassa": 2}.get(priority, 3)


def _position_name(row: pd.Series) -> str:
    return str(row.get("instrument_name") or row.get("isin_or_ticker") or row.get("sub_class_ips") or "").strip()


def build_diagnostics(
    portfolio: pd.DataFrame,
    allocation: pd.DataFrame,
    summary: dict[str, float],
    income_target: float,
    *,
    max_position_weight: float = 0.08,
    home_bias_limit: float = 0.40,
    usd_limit: float = 0.25,
    structured_limit: float = 0.07,
) -> pd.DataFrame:
    total = float(summary["total"])
    rows: list[dict[str, Any]] = []

    def add(area: str, priority: str, status: str, evidence: str, note: str) -> None:
        rows.append({"Area": area, "Priorità": priority, "Stato": status, "Evidenza": evidence, "Lettura advisor": note})

    if total <= 0:
        return pd.DataFrame(rows)

    over = allocation[allocation["Peso attuale"] > allocation["max"]].copy()
    under = allocation[allocation["Peso attuale"] < allocation["min"]].copy()
    for _, row in over.iterrows():
        gap = float(row["Peso attuale"] - row["max"])
        add(
            "Asset allocation",
            _priority_from_gap(gap),
            "Sopra max",
            f"{row['Sottoclasse']}: {row['Peso attuale']:.1%} vs max {row['max']:.1%}",
            f"Ridurre o non incrementare finché non rientra sotto il limite IPS. Eccesso stimato: {row['Sforamento EUR']:,.0f} EUR.",
        )
    for _, row in under.iterrows():
        gap = float(row["min"] - row["Peso attuale"])
        add(
            "Asset allocation",
            _priority_from_gap(gap),
            "Sotto min",
            f"{row['Sottoclasse']}: {row['Peso attuale']:.1%} vs min {row['min']:.1%}",
            f"Valutare incremento graduale se coerente con il profilo rischio. Mancanza minima stimata: {row['Sforamento EUR']:,.0f} EUR.",
        )

    ordered = portfolio.sort_values("market_value_eur", ascending=False).copy()
    ordered["weight"] = ordered["market_value_eur"] / total
    top5_weight = float(ordered.head(5)["weight"].sum())
    top1 = ordered.iloc[0] if not ordered.empty else None
    if top1 is not None and float(top1["weight"]) > max_position_weight:
        add(
            "Concentrazione",
            "Alta" if float(top1["weight"]) > max_position_weight * 1.5 else "Media",
            "Posizione dominante",
            f"{_position_name(top1)} pesa {float(top1['weight']):.1%}",
            "Valutare riduzione o stop a nuovi incrementi per contenere il rischio specifico.",
        )
    if top5_weight > 0.35:
        add(
            "Concentrazione",
            "Media",
            "Top 5 elevate",
            f"Le prime 5 posizioni pesano {top5_weight:.1%}",
            "Il portafoglio dipende molto da poche posizioni. Preferire nuovi acquisti diversificanti.",
        )

    italy_mask = portfolio["sub_class_ips"].astype(str).str.contains("Italy", case=False, na=False) | portfolio["isin_or_ticker"].astype(str).str.startswith("IT")
    italy_weight = float(portfolio.loc[italy_mask, "market_value_eur"].sum() / total)
    if italy_weight > home_bias_limit:
        add(
            "Home bias",
            "Alta" if italy_weight > home_bias_limit + 0.10 else "Media",
            "Italia elevata",
            f"Esposizione Italia stimata {italy_weight:.1%}",
            "Per nuovi investimenti privilegiare strumenti globali o ex-Italia finché il peso rientra.",
        )

    usd_weight = float(portfolio.loc[portfolio.get("currency", pd.Series(index=portfolio.index, dtype=str)).astype(str).str.upper() == "USD", "market_value_eur"].sum() / total)
    if usd_weight > usd_limit:
        add(
            "Valuta",
            "Media",
            "USD elevato",
            f"Esposizione diretta USD {usd_weight:.1%}",
            "Valutare se il rischio cambio è voluto; altrimenti preferire strumenti EUR-hedged o EUR.",
        )

    structured_mask = portfolio["sub_class_ips"].astype(str).str.startswith("Structured")
    structured_weight = float(portfolio.loc[structured_mask, "market_value_eur"].sum() / total)
    if structured_weight > structured_limit:
        add(
            "Strumenti complessi",
            "Alta" if structured_weight > structured_limit * 1.5 else "Media",
            "Structured elevati",
            f"Certificati/leveraged {structured_weight:.1%}",
            "Ridurre complessità e rischio di payoff non lineare prima di aumentare esposizioni rischiose.",
        )

    loss_mask = (portfolio.get("unrealized_gain_pct", pd.Series(index=portfolio.index, dtype=float)) <= -0.40) & (portfolio["market_value_eur"] > total * 0.002)
    for _, row in portfolio.loc[loss_mask].sort_values("unrealized_gain_pct").head(5).iterrows():
        add(
            "Posizioni in perdita",
            "Media",
            "Tesi da rivedere",
            f"{_position_name(row)}: {float(row['unrealized_gain_pct']):.1%}",
            "Rivalutare la tesi: tenere solo se il ruolo futuro è ancora chiaro, non per recuperare il prezzo di carico.",
        )

    income_gap = float(income_target - summary["net_income"])
    if income_target and income_gap > 0:
        add(
            "Reddito",
            "Alta" if income_gap / income_target > 0.25 else "Media",
            "Sotto obiettivo",
            f"Gap reddito netto {income_gap:,.0f} EUR",
            "Coprire il gap solo con strumenti che non peggiorano eccessivamente concentrazione e rischio complessivo.",
        )

    diagnostics = pd.DataFrame(rows)
    if diagnostics.empty:
        return diagnostics
    diagnostics["_sort"] = diagnostics["Priorità"].map(_priority_score)
    return diagnostics.sort_values(["_sort", "Area"]).drop(columns="_sort").reset_index(drop=True)


def build_recommendations(
    portfolio: pd.DataFrame,
    allocation: pd.DataFrame,
    summary: dict[str, float],
    income_target: float,
    *,
    max_position_weight: float = 0.08,
) -> pd.DataFrame:
    total = float(summary["total"])
    rows: list[dict[str, Any]] = []

    def add(action: str, target: str, priority: str, amount: float, reason: str, guardrail: str) -> None:
        rows.append(
            {
                "Azione": action,
                "Oggetto": target,
                "Priorità": priority,
                "Importo indicativo": max(0.0, float(amount)),
                "Motivo": reason,
                "Guardrail": guardrail,
            }
        )

    if total <= 0:
        return pd.DataFrame(rows)

    for _, row in allocation.iterrows():
        current = float(row["Peso attuale"])
        target = float(row["target"])
        if current > float(row["max"]):
            amount = (current - target) * total
            add(
                "Ridurre",
                str(row["Sottoclasse"]),
                _priority_from_gap(current - float(row["max"])),
                amount,
                f"Peso {current:.1%}, sopra max {float(row['max']):.1%}.",
                "Riduzione graduale verso target; evitare vendite automatiche solo per rumore di mercato.",
            )
        elif current < float(row["min"]):
            amount = (target - current) * total
            add(
                "Incrementare",
                str(row["Sottoclasse"]),
                _priority_from_gap(float(row["min"]) - current),
                amount,
                f"Peso {current:.1%}, sotto min {float(row['min']):.1%}.",
                "Usare nuovi versamenti o switch da aree sopra max.",
            )

    holdings = portfolio.copy()
    holdings["weight"] = holdings["market_value_eur"] / total
    concentrated = holdings[holdings["weight"] > max_position_weight].sort_values("weight", ascending=False)
    for _, row in concentrated.head(5).iterrows():
        amount = float(row["market_value_eur"] - max_position_weight * total)
        add(
            "Ridurre / congelare",
            _position_name(row),
            "Alta" if float(row["weight"]) > max_position_weight * 1.5 else "Media",
            amount,
            f"Peso posizione {float(row['weight']):.1%}, sopra soglia {max_position_weight:.1%}.",
            "Preferire riduzione verso strumenti core diversificati se la tesi specifica non è forte.",
        )

    leveraged = holdings[holdings["sub_class_ips"].astype(str) == "Structured / Leveraged ETF"]
    for _, row in leveraged.iterrows():
        add(
            "Disinvestire / residualizzare",
            _position_name(row),
            "Alta",
            float(row["market_value_eur"]),
            "Strumento leveraged/short non coerente con allocazione strategica di lungo periodo.",
            "Mantenere solo se esiste una tesi tattica esplicita, dimensionata e con stop predefinito.",
        )

    income_gap = float(income_target - summary["net_income"])
    if income_target and income_gap > 0:
        candidates = allocation[
            (allocation["Peso attuale"] < allocation["max"])
            & (allocation["yield"] >= 0.032)
            & (allocation["risk"] <= 7)
            & ~allocation["Sottoclasse"].astype(str).str.startswith("Structured")
        ].copy()
        candidates["Capienza max EUR"] = (candidates["max"] - candidates["Peso attuale"]) * total
        candidates = candidates[candidates["Capienza max EUR"] > 1000].sort_values(["yield", "risk"], ascending=[False, True])
        for _, row in candidates.head(3).iterrows():
            tax = 0.125 if row["Sottoclasse"] == "Bonds Italy Gov" else 0.26
            estimated_net_yield = float(row["yield"]) * (1 - tax)
            needed = income_gap / estimated_net_yield if estimated_net_yield else 0.0
            amount = min(float(row["Capienza max EUR"]), needed)
            add(
                "Valutare incremento income",
                str(row["Sottoclasse"]),
                "Media",
                amount,
                f"Yield lordo ipotizzato {float(row['yield']):.1%}; capienza fino a max {float(row['Capienza max EUR']):,.0f} EUR.",
                "Non inseguire solo il rendimento: controllare duration, credito, liquidità e tassazione.",
            )

    recommendations = pd.DataFrame(rows)
    if recommendations.empty:
        return recommendations
    recommendations["_sort"] = recommendations["Priorità"].map(_priority_score)
    return recommendations.sort_values(["_sort", "Importo indicativo"], ascending=[True, False]).drop(columns="_sort").reset_index(drop=True)


def build_watchlist(portfolio: pd.DataFrame, allocation: pd.DataFrame) -> pd.DataFrame:
    current_by_sub = allocation.set_index("Sottoclasse")
    holdings_by_ticker = portfolio.copy()
    holdings_by_ticker["ticker_key"] = holdings_by_ticker.get("ticker", holdings_by_ticker.get("isin_or_ticker", "")).astype(str).str.upper()

    rows = []
    for item in INVESTABLE_WATCHLIST:
        subclass = item["Sottoclasse"]
        allocation_row = current_by_sub.loc[subclass] if subclass in current_by_sub.index else None
        ticker_mask = holdings_by_ticker["ticker_key"] == item["ticker"].upper()
        current_value = float(holdings_by_ticker.loc[ticker_mask, "market_value_eur"].sum())
        if allocation_row is None:
            ips_state = "Da configurare"
            drift = 0.0
            weight = 0.0
        else:
            ips_state = str(allocation_row["Stato"])
            drift = float(allocation_row["Drift vs target EUR"])
            weight = float(allocation_row["Peso attuale"])

        if ips_state == "Sotto min" or drift > 5000:
            priority = "Candidato incremento"
        elif ips_state == "Sopra max" or drift < -5000:
            priority = "Non incrementare"
        else:
            priority = "Monitorare"

        rows.append(
            {
                **item,
                "Valore già in portafoglio": current_value,
                "Peso sottoclasse": weight,
                "Stato IPS": ips_state,
                "Priorità": priority,
            }
        )
    return pd.DataFrame(rows)


def _historical_return(close: pd.Series, periods: int) -> float | None:
    clean = close.dropna()
    if len(clean) < 2:
        return None
    start_index = max(0, len(clean) - periods - 1)
    start = float(clean.iloc[start_index])
    end = float(clean.iloc[-1])
    if start == 0:
        return None
    return end / start - 1


def _rsi(close: pd.Series, period: int = 14) -> float | None:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = -delta.clip(upper=0).rolling(period).mean()
    rs = gain / loss.replace(0, pd.NA)
    rsi = 100 - (100 / (1 + rs))
    clean = rsi.dropna()
    if clean.empty:
        return None
    return float(clean.iloc[-1])


def _bounded(value: float, lower: float, upper: float) -> float:
    return min(max(value, lower), upper)


def _hot_signal_from_history(
    ticker: str,
    name: str,
    area: str,
    history: pd.DataFrame,
    budget_eur: float,
    max_loss_pct: float,
    max_positions: int,
) -> dict[str, Any] | None:
    if history is None or history.empty or "Close" not in history:
        return None
    close = history["Close"].dropna()
    if len(close) < 70:
        return None

    returns = close.pct_change().dropna()
    last = float(close.iloc[-1])
    ma20 = float(close.rolling(20).mean().iloc[-1])
    ma50 = float(close.rolling(50).mean().iloc[-1])
    prev_high_20 = float(close.shift(1).rolling(20).max().iloc[-1])
    prev_low_20 = float(close.shift(1).rolling(20).min().iloc[-1])
    rsi = _rsi(close)
    ret_5 = _historical_return(close, 5)
    ret_20 = _historical_return(close, 20)
    ret_60 = _historical_return(close, 60)

    score = 0
    reasons: list[str] = []

    if last > ma20:
        score += 12
        reasons.append("prezzo sopra media 20g")
    else:
        score -= 12
        reasons.append("prezzo sotto media 20g")

    if ma20 > ma50:
        score += 16
        reasons.append("trend 20g sopra 50g")
    else:
        score -= 16
        reasons.append("trend 20g sotto 50g")

    if ret_20 is not None and ret_20 > 0:
        score += 10
        reasons.append("momentum 1M positivo")
    elif ret_20 is not None:
        score -= 10
        reasons.append("momentum 1M negativo")

    if ret_60 is not None and ret_60 > 0:
        score += 8
    elif ret_60 is not None:
        score -= 8

    if last >= prev_high_20 * 0.995:
        score += 22
        reasons.append("breakout area massimi 20g")
    if last <= prev_low_20 * 1.005:
        score -= 22
        reasons.append("breakdown area minimi 20g")

    if rsi is not None:
        if 52 <= rsi <= 70:
            score += 8
            reasons.append("RSI costruttivo")
        elif rsi < 40:
            score -= 8
            reasons.append("RSI debole")
        elif rsi > 75:
            score -= 10
            reasons.append("RSI tirato")

    if ret_5 is not None and ret_20 is not None and ret_5 < 0 < ret_20:
        reasons.append("pullback dentro trend positivo")

    if score >= 35:
        direction = "Long"
        signal = "BUY tecnico"
    elif score <= -35:
        direction = "Short/Avoid"
        signal = "SELL/SHORT tecnico"
    else:
        direction = "No trade"
        signal = "Neutrale"

    abs_score = abs(score)
    confidence = "Alta" if abs_score >= 65 else "Media" if abs_score >= 45 else "Bassa"
    daily_vol = float(returns.tail(20).std()) if len(returns) >= 20 else 0.03
    stop_pct = _bounded(2.2 * daily_vol * math.sqrt(5), 0.06, 0.18)
    sleeve_loss_budget = budget_eur * max_loss_pct
    risk_per_trade = sleeve_loss_budget / max(1, max_positions)
    equal_weight_size = budget_eur / max(1, max_positions)
    size_by_risk = risk_per_trade / stop_pct if stop_pct else equal_weight_size
    suggested_size = 0.0 if direction == "No trade" else min(equal_weight_size, size_by_risk, budget_eur)

    if direction == "Short/Avoid":
        stop_price = last * (1 + stop_pct)
        target_price = last * (1 - stop_pct * 1.5)
    elif direction == "Long":
        stop_price = last * (1 - stop_pct)
        target_price = last * (1 + stop_pct * 1.5)
    else:
        stop_price = None
        target_price = None

    return {
        "ticker": ticker,
        "Nome": name,
        "Area": area,
        "Segnale": signal,
        "Direzione": direction,
        "Score": int(score),
        "Confidenza": confidence,
        "Ultimo": last,
        "5D": ret_5,
        "1M": ret_20,
        "3M": ret_60,
        "RSI 14": rsi,
        "Vol 20g ann.": daily_vol * math.sqrt(252),
        "Stop tecnico": stop_pct,
        "Size max": suggested_size,
        "Rischio stimato": suggested_size * stop_pct,
        "Stop price": stop_price,
        "Target tattico": target_price,
        "Motivo": "; ".join(reasons[:4]),
        "Aggiornato": str(close.index[-1].date()),
    }


def fetch_hot_trading_signals(
    tickers: list[str],
    portfolio_tickers: list[str],
    budget_eur: float,
    max_loss_pct: float,
    max_positions: int,
) -> tuple[pd.DataFrame, str | None]:
    try:
        import yfinance as yf
    except Exception:
        return pd.DataFrame(), "Per attivare Hot Trading installa la dipendenza yfinance."

    universe_lookup = {item["ticker"].upper(): item for item in HOT_TRADING_UNIVERSE}
    excluded = {ticker.upper() for ticker in portfolio_tickers if ticker}
    unique_tickers = []
    for ticker in tickers:
        normalized = ticker.strip().upper()
        if normalized and normalized not in excluded and normalized not in unique_tickers:
            unique_tickers.append(normalized)

    rows = []
    for ticker in unique_tickers:
        meta = universe_lookup.get(ticker, {"ticker": ticker, "Nome": ticker, "Area": "Custom"})
        try:
            history = yf.Ticker(ticker).history(period="6mo", auto_adjust=True)
        except Exception:
            continue
        row = _hot_signal_from_history(
            ticker=ticker,
            name=str(meta["Nome"]),
            area=str(meta["Area"]),
            history=history,
            budget_eur=budget_eur,
            max_loss_pct=max_loss_pct,
            max_positions=max_positions,
        )
        if row:
            rows.append(row)

    if not rows:
        return pd.DataFrame(), "Non sono disponibili segnali Hot Trading in questo momento."

    signals = pd.DataFrame(rows)
    signals["_rank"] = signals["Score"].abs()
    return signals.sort_values(["_rank", "Score"], ascending=[False, False]).drop(columns="_rank").reset_index(drop=True), None


def fetch_market_snapshot(tickers: list[str]) -> tuple[pd.DataFrame, str | None]:
    try:
        import yfinance as yf
    except Exception:
        return pd.DataFrame(), "Per attivare il monitor mercati installa la dipendenza yfinance."

    rows = []
    for ticker in tickers:
        try:
            history = yf.Ticker(ticker).history(period="1y", auto_adjust=True)
        except Exception:
            continue
        if history is None or history.empty or "Close" not in history:
            continue
        close = history["Close"].dropna()
        if close.empty:
            continue
        returns = close.pct_change().dropna()
        ma50 = close.rolling(50).mean().dropna()
        ytd = None
        year_values = close[close.index >= pd.Timestamp(datetime.now().year, 1, 1, tz=close.index.tz)]
        if len(year_values) >= 2 and float(year_values.iloc[0]) != 0:
            ytd = float(year_values.iloc[-1] / year_values.iloc[0] - 1)
        rows.append(
            {
                "ticker": ticker,
                "Ultimo": float(close.iloc[-1]),
                "1M": _historical_return(close, 21),
                "3M": _historical_return(close, 63),
                "YTD": ytd,
                "1Y": _historical_return(close, 252),
                "Vol 3M ann.": float(returns.tail(63).std() * math.sqrt(252)) if len(returns) >= 10 else None,
                "Trend 50g": float(close.iloc[-1] / ma50.iloc[-1] - 1) if not ma50.empty and float(ma50.iloc[-1]) != 0 else None,
                "Aggiornato": str(close.index[-1].date()),
            }
        )

    if not rows:
        return pd.DataFrame(), "Non sono disponibili dati di mercato in questo momento."
    return pd.DataFrame(rows), None
