"""
Санхүүгийн тайлангийн архивыг татах (нэг удаагийн ажил).

open.mse.mn/securities/<код>/financials/filter?year=<жил> хаяг тухайн ОНЫ
(тайлангийн үеийн жил, файлласан огнооны биш) БҮХ улирлын тайланг нэг JSON
хариунд буцаадаг (html талбарт хэд хэдэн .finance-report-item блок). Үүнийг
сүүлийн N жилээр давтан татаж data/financials.csv-д хуримтлуулна.

    python backfill.py --inspect KHAN    # эх сурвалжийг харах
    python backfill.py --symbols KHAN    # зөвхөн нэг хувьцаагаар турших
    python backfill.py                   # config.json-ы бүх хувьцаагаар (сүүлийн 5 жил)
"""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

import pandas as pd

import monitor
import mse_scraper as mse

ROOT = Path(__file__).parent


def mse_code(symbol: str) -> int:
    secs = mse.parse_securities(mse.fetch("/securities", use_cache=True))
    codes = dict(zip(secs.symbol.str.upper(), secs.company_code))
    if symbol.upper() not in codes:
        raise SystemExit(f"{symbol} олдсонгүй.")
    return int(codes[symbol.upper()])


def fetch_symbol(symbol: str, code: int, years: list[int]) -> list[dict]:
    rows = []
    for year in years:
        html = mse.fetch_financials_year(code, year, use_cache=False)
        if not html:
            continue
        for rec in mse.parse_financials_history(html, code):
            if rec.get("year"):
                rec["symbol"] = symbol
                rows.append(rec)
    return rows


def inspect(symbol: str) -> None:
    """Нэг жилийн хариуг хэвлэж, бүтэц зөв задарч байгааг харуулна."""
    code = mse_code(symbol)
    year = date.today().year
    html = mse.fetch_financials_year(code, year, use_cache=False)
    print(f"--- {symbol} (код {code}), жил {year}, HTML хэмжээ {len(html):,} тэмдэгт")
    rows = mse.parse_financials_history(html, code)
    print(f"{len(rows)} улирал олдлоо:")
    for r in rows:
        print(f"  {r['year']} Q{r['quarter']}: нийт хөрөнгө={r.get('total_assets')}, "
              f"цэвэр ашиг={r.get('net_profit')}, ROE={r.get('roe')}")


def find_duplicates(rows: list[dict]) -> list[str]:
    """Өөр өөр улирлын (total_assets, net_profit) хос ижил гарвал зөв ялгараагүй.

    0 буюу хоосон утгыг тооцохгүй, учир нь зарим улиралд дутуу мэдээлэл
    бодитоор ил гардаг (доор README-д тайлбарласан).
    """
    df = pd.DataFrame(rows)
    problems = []
    if df.empty or "total_assets" not in df:
        return problems
    key = ["total_assets", "net_profit"]
    seen = df.dropna(subset=key)
    seen = seen[(seen["total_assets"] != 0) & (seen["net_profit"] != 0)]
    dup = seen[seen.duplicated(subset=key, keep=False)]
    for _, g in dup.groupby(key):
        periods = sorted(set(zip(g.symbol, g.year, g.quarter)))
        if len(periods) > 1:
            problems.append(f"{periods} мөрүүдийн total_assets/net_profit ижил байна")
    return problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inspect", metavar="SYMBOL", help="эх сурвалжийг харах")
    ap.add_argument("--years", type=int, default=5)
    ap.add_argument("--symbols", nargs="+", help="өгөхгүй бол config.json-оос авна")
    args = ap.parse_args()

    if args.inspect:
        inspect(args.inspect)
        return

    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    symbols = args.symbols or sorted(cfg["universe"])
    this_year = date.today().year
    years = list(range(this_year - args.years + 1, this_year + 1))

    all_rows = []
    for sym in symbols:
        code = mse_code(sym)
        rows = fetch_symbol(sym, code, years)
        print(f"{sym}: {len(rows)} улирал")
        all_rows.extend(rows)

    if not all_rows:
        raise SystemExit("Тайлан уншигдсангүй.")

    problems = find_duplicates(all_rows)
    if problems:
        print("\nАЛДАА: давхардсан утга илэрлээ, ХАДГАЛАХГҮЙ:")
        for p in problems:
            print(" ", p)
        raise SystemExit(1)

    monitor._merge("financials.csv", all_rows, ["symbol", "year", "quarter"])
    df = pd.read_csv(ROOT / "data" / "financials.csv")
    print(f"\nНийт {len(df)} мөр, {df.symbol.nunique()} хувьцаа.")


if __name__ == "__main__":
    main()
