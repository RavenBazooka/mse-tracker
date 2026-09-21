"""
Санхүүгийн тайлан болон компанийн мэдэгдлийн хяналт.

open.mse.mn зөвхөн хамгийн сүүлийн улирлын тайланг харуулдаг тул өдөр бүр уншиж
data/financials.csv-д хуримтлуулна. Хугацаа өнгөрөх тусам түүх бүрдэж, өөрчлөлтийг
харьцуулах боломжтой болно.

Ногдол ашгийн мэдэгдэл нь компанийн хуудасны "Мэдээний" хүснэгтэд гарна.
"""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd
from bs4 import BeautifulSoup

import mse_scraper as mse

ROOT = Path(__file__).parent
DATA = ROOT / "data"

DIVIDEND_WORDS = ("ногдол ашиг",)
MEETING_WORDS = ("хувьцаа эзэмшигчдийн хурал", "хувьцаа эзэмшигчдийн ээлжит")


def _load(name: str) -> pd.DataFrame:
    path = DATA / name
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path)
    df.columns = [c.strip().lstrip("\ufeff") for c in df.columns]
    return df


# ------------------------------------------------------------------ мэдээ
def parse_company_news(page: str) -> list[dict]:
    """Компанийн хуудасны мэдээний хүснэгт."""
    soup = BeautifulSoup(page, "html.parser")
    out = []
    for table in soup.select("table"):
        head = table.find("th")
        if not (head and "Мэдээ" in table.get_text()[:400]):
            continue
        for tr in table.select("tr"):
            tds = tr.find_all("td")
            if len(tds) < 3:
                continue
            a = tds[1].find("a")
            title = (a or tds[1]).get_text(" ", strip=True)
            date = tds[2].get_text(strip=True)[:10]
            if not title or not re.match(r"\d{4}-\d{2}-\d{2}", date):
                continue
            href = a.get("href", "") if a else ""
            out.append({"date": date, "title": title, "url": href})
        if out:
            break
    return out


def classify(title: str) -> str:
    low = title.lower()
    if any(w in low for w in DIVIDEND_WORDS):
        return "ногдол ашиг"
    if any(w in low for w in MEETING_WORDS):
        return "хувьцаа эзэмшигчдийн хурал"
    return "бусад"


def extract_dps(title: str) -> float | None:
    """Гарчгаас нэгж хувьцаанд ногдох дүнг сугалах оролдлого."""
    m = re.search(r"([\d\s,\.]+)\s*(?:төгрөг|төг)", title)
    if not m:
        return None
    try:
        return float(re.sub(r"[\s,]", "", m.group(1)))
    except ValueError:
        return None


# ------------------------------------------------------------------ татах
def refresh(symbols: list[str], code_of: dict[str, int]) -> None:
    """Тайлан болон мэдээг татаж, хуримтлуулсан файлуудад нэмнэ."""
    DATA.mkdir(exist_ok=True)
    fin_rows, news_rows = [], []
    for sym in symbols:
        code = code_of.get(sym.upper())
        if code is None:
            continue
        try:
            rec = mse.parse_financials(
                mse.fetch(f"/securities/{code}/tab/financials", use_cache=False), code)
            if rec and rec.get("year"):
                rec["symbol"] = sym
                fin_rows.append(rec)
        except Exception as e:
            print(f"  ! {sym} тайлан: {e}")
        try:
            for n in parse_company_news(mse.fetch(f"/securities/{code}", use_cache=False)):
                n["symbol"] = sym
                n["kind"] = classify(n["title"])
                n["dps"] = extract_dps(n["title"])
                news_rows.append(n)
        except Exception as e:
            print(f"  ! {sym} мэдээ: {e}")

    _merge("financials.csv", fin_rows, ["symbol", "year", "quarter"])
    _merge("news.csv", news_rows, ["symbol", "date", "title"])


def _merge(name: str, rows: list[dict], keys: list[str]) -> None:
    if not rows:
        return
    old, new = _load(name), pd.DataFrame(rows)
    merged = pd.concat([old, new], ignore_index=True) if not old.empty else new
    merged = merged.drop_duplicates(subset=keys, keep="last").sort_values(keys)
    merged.to_csv(DATA / name, index=False, encoding="utf-8-sig")
    added = len(merged) - len(old)
    if added > 0:
        print(f"  {name}: {added} шинэ мөр")


# ------------------------------------------------------------------ дүгнэлт
def summary(symbols: list[str]) -> dict:
    """Хуудсанд харуулах санхүүгийн үзүүлэлт, мэдэгдэл, анхааруулга."""
    fin, news = _load("financials.csv"), _load("news.csv")
    rows, alerts = [], []

    if not fin.empty:
        fin = fin[fin.symbol.isin(symbols)].copy()
        fin["period"] = fin["year"] * 10 + fin["quarter"]
        for sym, g in fin.sort_values("period").groupby("symbol"):
            cur = g.iloc[-1]
            # Өмнөх оны мөн улирал. Тайлан хуримтлагдсан байдлаар гардаг тул
            # зэргэлдээ улирлыг бус, жилийн өмнөхийг харьцуулна.
            prev = g[g.period == cur.period - 10]
            prev = prev.iloc[-1] if len(prev) else None
            debt = (cur.total_liabilities / cur.total_assets * 100
                    if cur.total_assets else None)
            profit_chg = ((cur.net_profit / prev.net_profit - 1) * 100
                          if prev is not None and prev.net_profit else None)
            rows.append({
                "symbol": sym, "period": f"{int(cur.year)} Q{int(cur.quarter)}",
                "roe": cur.roe, "eps": cur.eps, "debt": debt,
                "profit_chg": profit_chg, "equity": cur.equity,
                "compared": f"{int(prev.year)} Q{int(prev.quarter)}" if prev is not None else None,
            })
            if pd.notna(cur.equity) and cur.equity <= 0:
                alerts.append(f"{sym}: өөрийн хөрөнгө сөрөг байна ({cur.year} Q{int(cur.quarter)}). "
                              "Урт хугацааны эзэмшилд эргэлзээтэй.")
            elif debt and debt > 95 and cur.sector_kind == "general":
                alerts.append(f"{sym}: өр төлбөр нийт хөрөнгийн {debt:.0f}% болсон.")
            if profit_chg is not None and profit_chg < -30:
                alerts.append(f"{sym}: цэвэр ашиг жилийн өмнөхөөс {profit_chg:.0f}% буурсан.")

    notices = []
    if not news.empty:
        n = news[news.symbol.isin(symbols) & (news.kind != "бусад")].copy()
        n = n.sort_values("date", ascending=False).head(8)
        notices = n.to_dict("records")
        recent = n[n.date >= str(pd.Timestamp.today().date() - pd.Timedelta(days=45))]
        for r in recent[recent.kind == "ногдол ашиг"].to_dict("records"):
            dps = f", нэгж хувьцаанд {r['dps']:.0f}₮" if r.get("dps") else ""
            alerts.append(f"{r['symbol']}: ногдол ашгийн мэдэгдэл гарсан ({r['date']}{dps}). "
                          "Бүртгэлийн өдрийг шалгаж, config-оо шинэчлэх.")

    return {"rows": rows, "notices": notices, "alerts": alerts}
