"""
МХБ (open.mse.mn) нээлттэй өгөгдлийн scraper.

Татах өгөгдөл:
  1. securities.csv  - бүртгэлтэй хувьцаа, сангийн жагсаалт (код, симбол, нэр, ангилал)
  2. prices.csv      - өдөр тутмын OHLCV түүх (1999 оноос хойш)
  3. financials.csv  - хамгийн сүүлийн улирлын санхүүгийн үзүүлэлт (сонголтоор)
  4. liquidity.csv   - хөрвөх чадварын товч үзүүлэлт (шүүлтэд ашиглана)

Хэрэглээ:
  pip install requests beautifulsoup4 pandas
  python mse_scraper.py --symbols APU TDB GOV --financials
  python mse_scraper.py --all --classes I --financials
  python mse_scraper.py --all --since 2015-01-01

Анхааруулга: албан ёсны API биш. Сайтын бүтэц өөрчлөгдвөл parser-ийг шинэчлэх
шаардлагатай. Серверт ачаалал өгөхгүйн тулд хүсэлт хооронд завсарлага авна.
"""
from __future__ import annotations

import argparse
import html
import json
import re
import time
from pathlib import Path

import pandas as pd
import requests
from bs4 import BeautifulSoup

# Байгууллагын прокситой сүлжээнд Windows-ийн гэрчилгээний санг ашиглах
# (pip install truststore). Суусан бол автоматаар идэвхжинэ.
try:
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

BASE_URL = "https://open.mse.mn"
HEADERS = {"User-Agent": "Mozilla/5.0 (research scraper; personal portfolio analysis)"}
DELAY_SEC = 1.0          # хүсэлт хоорондын завсарлага
CACHE_DIR = Path(".mse_cache")

# Ангиллын таб -> ангилал
CLASS_TABS = {"class1": "I", "class2": "II", "class3": "III"}

# Санхүүгийн табын шошго -> баганын нэр (банк, ББСБ, даатгалын ялгаатай нэршлийг хамруулсан)
FIN_LABELS = {
    "Нийт хөрөнгө": "total_assets",
    "Өр төлбөрийн нийт дүн": "total_liabilities",
    "Эзэмшигчдийн өмчийн дүн": "equity",
    "Өөрийн хөрөнгийн дүн": "equity",
    "Нийт гаргасан хувьцаа": "shares_outstanding",
    "Нийт борлуулалтын орлого": "revenue",
    "Хүүгийн орлого": "revenue",
    "Даатгалын хураамжийн орлого": "revenue",
    "Нийт ашиг": "gross_profit",
    "Цэвэр ашиг": "net_profit",
    "Татварын дараах ашиг, алдагдал": "net_profit",
    "Тайлант үеийн цэвэр ашиг, алдагдал": "net_profit",
    "Нэгж хувьцааны дансны үнэ": "bvps",
    "Нийт хөрөнгийн өгөөж /ROA/": "roa",
    "Хувь нийлүүлсэн хөрөнгийн өгөөж /ROE/": "roe",
    "Нэгж хувьцааны өгөөж /EPS/": "eps",
    "Үнэ ашгийн харьцаа (P/E Ratio)": "pe",
}
SECTOR_MARKERS = [("Хадгаламж", "bank"), ("Даатгалын хураамжийн орлого", "insurance"),
                  ("Хүүгийн орлого", "nbfi")]


# ---------------------------------------------------------------- HTTP
_session = requests.Session()
_session.headers.update(HEADERS)


def fetch(path: str, use_cache: bool = True, retries: int = 3) -> str:
    """Хуудсыг татна. Давтан ажиллуулахад серверт очихгүйн тулд кэшлэнэ."""
    cache_file = CACHE_DIR / (re.sub(r"[^\w]+", "_", path).strip("_") + ".html")
    if use_cache and cache_file.exists():
        return cache_file.read_text(encoding="utf-8")
    for attempt in range(1, retries + 1):
        try:
            r = _session.get(BASE_URL + path, timeout=30)
            r.raise_for_status()
            CACHE_DIR.mkdir(exist_ok=True)
            cache_file.write_text(r.text, encoding="utf-8")
            time.sleep(DELAY_SEC)
            return r.text
        except requests.RequestException as e:
            if attempt == retries:
                raise
            print(f"  ! {path}: {e} - дахин оролдож байна ({attempt}/{retries})")
            time.sleep(DELAY_SEC * 2 ** attempt)
    raise RuntimeError("unreachable")


def to_number(text: str) -> float | None:
    cleaned = re.sub(r"[,\s]", "", text or "")
    if cleaned in ("", "-", "N/A"):
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


# ---------------------------------------------------------------- parsers
def parse_securities(page: str) -> pd.DataFrame:
    soup = BeautifulSoup(page, "html.parser")
    rows, seen = [], set()

    def read(section, classification, status):
        if section is None:
            return
        for tr in section.select("table tr"):
            tds = tr.find_all("td")
            if len(tds) < 3:
                continue
            a = tds[1].find("a")
            m = re.search(r"/securities/(\d+)", a.get("href", "")) if a else None
            if not m:
                continue
            code = int(m.group(1))
            if code in seen:
                continue
            seen.add(code)
            rows.append({"company_code": code, "symbol": a.get_text(strip=True),
                         "name": tds[2].get_text(strip=True),
                         "classification": classification, "status": status})

    stocks = soup.find(id="stocks")
    for tab_id, cls in CLASS_TABS.items():
        read(stocks.find(id=tab_id) if stocks else soup.find(id=tab_id), cls, "active")
    read(stocks.find(id="delisted") if stocks else None, None, "delisted")
    read(soup.find(id="funds"), "fund", "active")
    return pd.DataFrame(rows)


def parse_prices(page: str, company_code: int) -> pd.DataFrame:
    m = re.search(r"data-trading-histories='(\[[\s\S]*?\])'", page)
    if not m:
        return pd.DataFrame()
    try:
        raw = json.loads(html.unescape(m.group(1)))
    except json.JSONDecodeError:
        return pd.DataFrame()
    df = pd.DataFrame(raw)
    if df.empty or "dates" not in df:
        return pd.DataFrame()
    df = df.rename(columns={
        "dates": "date", "OpeningPrice": "open", "ClosingPrice": "close",
        "HighPrice": "high", "LowPrice": "low", "VWAP": "vwap", "Volume": "volume",
        "Turnover": "turnover", "Trades": "trades", "PreviousClose": "prev_close",
    })
    cols = ["date", "open", "high", "low", "close", "vwap", "volume",
            "turnover", "trades", "prev_close"]
    df = df[[c for c in cols if c in df.columns]].copy()
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df.insert(0, "company_code", company_code)
    return df.drop_duplicates("date", keep="last").sort_values("date")


def _parse_finance_block(block, company_code: int) -> dict:
    heading = block.find("h5").get_text(" ", strip=True) if block.find("h5") else ""
    pm = re.search(r"(\d{4})\s*Он\s*(\d)\s*Улирал", heading)
    rec = {"company_code": company_code,
           "year": int(pm.group(1)) if pm else None,
           "quarter": int(pm.group(2)) if pm else None}
    labels = set()
    for li in block.find_all("li"):
        label = li.get_text().split(":")[0].strip()
        labels.add(label)
        if label in FIN_LABELS and li.find("b"):
            rec[FIN_LABELS[label]] = to_number(li.find("b").get_text())
    rec["sector_kind"] = next((k for mk, k in SECTOR_MARKERS if mk in labels), "general")
    return rec


def parse_financials(page: str, company_code: int) -> dict | None:
    """Хуудсан дахь эхний (=хамгийн сүүлийн) улирлын тайлан."""
    soup = BeautifulSoup(page, "html.parser")
    block = soup.select_one(".finance-report-item")
    return _parse_finance_block(block, company_code) if block is not None else None


def parse_financials_history(page: str, company_code: int) -> list[dict]:
    """/financials/filter?year=<жил> хариунд байгаа БҮХ улирлын тайлан."""
    soup = BeautifulSoup(page, "html.parser")
    return [_parse_finance_block(b, company_code) for b in soup.select(".finance-report-item")]


def fetch_financials_year(code: int, year: int, use_cache: bool = True) -> str:
    """Тухайн жилийн бүх улирлын тайланг агуулсан HTML хэсгийг буцаана.

    /securities/<код>/financials/filter?year=<жил> нь тухайн ОНЫ (тайлангийн
    үеийн, файлласан огнооны биш) бүх улирлыг нэг JSON хариунд буцаадаг.
    """
    raw = fetch(f"/securities/{code}/financials/filter?year={year}", use_cache=use_cache)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return ""
    return data.get("html", "") if data.get("success") else ""


# ---------------------------------------------------------------- analytics
def liquidity_summary(prices: pd.DataFrame, lookback_days: int = 365) -> pd.DataFrame:
    """Сүүлийн нэг жилийн хөрвөх чадвар: арилжаатай өдрийн хувь, дундаж гүйлгээ."""
    if prices.empty:
        return pd.DataFrame()
    p = prices.copy()
    p["date"] = pd.to_datetime(p["date"])
    end = p["date"].max()
    recent = p[p["date"] > end - pd.Timedelta(days=lookback_days)]
    session_days = recent["date"].nunique()   # зах зээл дээрх нийт арилжааны өдөр (ойролцоо)
    traded = recent[recent["volume"] > 0]
    g = traded.groupby("symbol")
    out = pd.DataFrame({
        "days_traded": g["date"].nunique(),
        "avg_daily_turnover": g["turnover"].mean(),
        "median_daily_turnover": g["turnover"].median(),
        "last_close": p.sort_values("date").groupby("symbol")["close"].last(),
    })
    out["pct_days_traded"] = out["days_traded"] / session_days * 100
    return out.sort_values("median_daily_turnover", ascending=False).round(1)


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="МХБ нээлттэй өгөгдөл татагч")
    sel = ap.add_mutually_exclusive_group(required=True)
    sel.add_argument("--symbols", nargs="+", help="симболууд, жнь: APU TDB")
    sel.add_argument("--all", action="store_true", help="бүх идэвхтэй хувьцаа")
    ap.add_argument("--classes", nargs="+", default=["I", "II", "III"],
                    help="--all үед ангилал шүүх (I II III fund)")
    ap.add_argument("--since", help="энэ огнооноос хойших үнэ (YYYY-MM-DD)")
    ap.add_argument("--financials", action="store_true", help="санхүүгийн үзүүлэлт татах")
    ap.add_argument("--no-cache", action="store_true", help="кэш ашиглахгүй")
    ap.add_argument("--out", default="mse_data", help="гаралтын хавтас")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(exist_ok=True)
    use_cache = not args.no_cache

    print("Жагсаалт татаж байна...")
    # Жагсаалт өдөр бүр өөрчлөгдөж болох тул кэшгүй татна
    secs = parse_securities(fetch("/securities", use_cache=False))
    if secs.empty:
        raise SystemExit("Жагсаалт хоосон байна - сайтын бүтэц өөрчлөгдсөн байж магадгүй.")
    secs.to_csv(out / "securities.csv", index=False, encoding="utf-8-sig")

    if args.all:
        targets = secs[(secs.status == "active") & secs.classification.isin(args.classes)]
    else:
        wanted = [s.upper() for s in args.symbols]
        targets = secs[secs.symbol.str.upper().isin(wanted)]
        missing = set(wanted) - set(targets.symbol.str.upper())
        if missing:
            print(f"  ! Олдсонгүй: {', '.join(sorted(missing))}")
    print(f"{len(targets)} үнэт цаас боловсруулна.")

    price_frames, fin_rows = [], []
    for i, row in enumerate(targets.itertuples(), 1):
        code, sym = row.company_code, row.symbol
        print(f"[{i}/{len(targets)}] {sym}")
        try:
            df = parse_prices(fetch(f"/securities/{code}/tab/tradeinfo", use_cache), code)
            if not df.empty:
                df.insert(1, "symbol", sym)
                price_frames.append(df)
            if args.financials:
                fin = parse_financials(fetch(f"/securities/{code}/tab/financials", use_cache), code)
                if fin:
                    fin["symbol"] = sym
                    fin_rows.append(fin)
        except Exception as e:  # нэг компанийн алдаа бүх ажлыг зогсоохгүй
            print(f"  ! {sym}: {e}")

    prices = pd.concat(price_frames, ignore_index=True) if price_frames else pd.DataFrame()
    if args.since and not prices.empty:
        prices = prices[pd.to_datetime(prices["date"]) >= pd.Timestamp(args.since)]
    prices.to_csv(out / "prices.csv", index=False, encoding="utf-8-sig")
    liquidity_summary(prices).to_csv(out / "liquidity.csv", encoding="utf-8-sig")
    if fin_rows:
        pd.DataFrame(fin_rows).to_csv(out / "financials.csv", index=False, encoding="utf-8-sig")

    print(f"\nДууслаа -> {out.resolve()}")
    print(f"  prices.csv: {len(prices):,} мөр")


if __name__ == "__main__":
    main()
