"""
Арилжааны бүртгэлээс эзэмшил, бодит өгөөж (XIRR) болон жишигтэй харьцуулалт.

trades.csv    date,symbol,shares,price,fee      (зарсан бол shares сөрөг)
dividends.csv date,symbol,amount                (гарт орсон цэвэр дүн)
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).parent


def _read(name: str) -> pd.DataFrame:
    path = ROOT / name
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path)
    df.columns = [c.strip().lstrip("\ufeff") for c in df.columns]
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date")


def load_trades() -> pd.DataFrame:
    return _read("trades.csv")


def load_dividends() -> pd.DataFrame:
    return _read("dividends.csv")


def positions(trades: pd.DataFrame) -> pd.DataFrame:
    """Дундаж өртгийн аргаар эзэмшил, өртөг, борлуулалтын ашгийг тооцно."""
    rows = {}
    for t in trades.itertuples():
        p = rows.setdefault(t.symbol, {"shares": 0.0, "cost": 0.0, "realised": 0.0,
                                       "fees": 0.0, "first": t.date})
        p["fees"] += t.fee
        if t.shares >= 0:
            p["shares"] += t.shares
            p["cost"] += t.shares * t.price + t.fee
        else:
            sold = -t.shares
            unit = p["cost"] / p["shares"] if p["shares"] else 0.0
            p["realised"] += sold * t.price - t.fee - sold * unit
            p["cost"] -= sold * unit
            p["shares"] -= sold
    df = pd.DataFrame(rows).T
    if df.empty:
        return df
    df.index.name = "symbol"
    df["avg_cost"] = np.where(df["shares"] > 0, df["cost"] / df["shares"], np.nan)
    return df


def xirr(flows: list[tuple[pd.Timestamp, float]]) -> float | None:
    """Мөнгөн урсгалын жилийн өгөөж. Хоёр хуваах аргаар шийднэ."""
    if len(flows) < 2 or not (any(f < 0 for _, f in flows) and any(f > 0 for _, f in flows)):
        return None
    t0 = min(d for d, _ in flows)
    years = [((d - t0).days / 365.0, f) for d, f in flows]

    def npv(r):
        return sum(f / (1 + r) ** y for y, f in years)

    lo, hi = -0.95, 10.0
    if npv(lo) * npv(hi) > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        if npv(lo) * npv(mid) <= 0:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


def benchmark_series(px: pd.DataFrame, symbols: list[str]) -> pd.Series:
    """Жигд жинтэй жишиг индекс. Өдрийн дундаж өгөөжийг хуримтлуулна."""
    wide = px[px.symbol.isin(symbols)].pivot_table(index="date", columns="symbol",
                                                   values="close").sort_index().ffill()
    if wide.empty:
        return pd.Series(dtype=float)
    ret = wide.pct_change().mean(axis=1, skipna=True).fillna(0)
    return (1 + ret).cumprod()


def evaluate(px: pd.DataFrame, cfg: dict) -> dict | None:
    """Багцын бодит үр дүнг жишигтэй харьцуулна (хоёулаа ногдол ашиггүй үнээр)."""
    trades, divs = load_trades(), load_dividends()
    if trades.empty:
        return None

    last = px.sort_values("date").groupby("symbol")["close"].last()
    manual = cfg.get("manual_prices", {})
    pos = positions(trades)
    pos["price"] = [last.get(s, manual.get(s)) for s in pos.index]
    pos["value"] = pos["shares"] * pos["price"].astype(float)

    invested = float((trades[trades.shares > 0].shares * trades[trades.shares > 0].price
                      + trades[trades.shares > 0].fee).sum())
    value = float(pos["value"].sum())
    div_total = float(divs["amount"].sum()) if not divs.empty else 0.0
    today = px["date"].max()

    trade_flows = [(t.date, -(t.shares * t.price) - t.fee) for t in trades.itertuples()]
    flows = trade_flows + [(d.date, d.amount) for d in divs.itertuples()] + [(today, value)]

    # жишиг: ижил мөнгийг ижил өдөр жигд жинтэй сагсанд оруулсан бол
    bench_syms = [s for s, u in cfg["universe"].items() if u["target_weight"] > 0]
    idx = benchmark_series(px, bench_syms)
    units, bench_flows = 0.0, []
    for t in trades.itertuples():
        level = idx.asof(t.date)
        cash = t.shares * t.price + t.fee
        if level and not np.isnan(level):
            units += cash / level          # ижил мөнгөөр жишиг сагс авсан бол
        bench_flows.append((t.date, -cash))
    bench_value = float(units * idx.iloc[-1]) if len(idx) else float("nan")
    bench_flows.append((today, bench_value))

    return {
        "positions": pos, "invested": invested, "value": value,
        "dividends": div_total, "realised": float(pos["realised"].sum()),
        "fees": float(pos["fees"].sum()),
        "xirr": xirr(flows),
        "xirr_price_only": xirr(trade_flows + [(today, value)]),
        "bench_value": bench_value, "bench_xirr": xirr(bench_flows),
        "first_trade": trades["date"].min(), "asof": today,
        "n_trades": len(trades),
    }
