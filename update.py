"""
МХБ багцын хяналтын самбар.

Өдөр бүр ажиллаж: open.mse.mn-ээс үнийн өгөгдөл татах -> data/prices.csv-д нэмэх
-> багцын байдлыг тооцох -> docs/index.html хуудсыг шинэчлэх.

Хэрэглээ:
  python update.py              # татаад шинэчилнэ
  python update.py --no-fetch   # татахгүй, хуучин өгөгдлөөр зөвхөн хуудсыг зурна
"""
from __future__ import annotations

import argparse
import json
import math
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

import mse_scraper as mse

ROOT = Path(__file__).parent
DATA = ROOT / "data"
DOCS = ROOT / "docs"
MN_MONTHS = ["1 дүгээр", "2 дугаар", "3 дугаар", "4 дүгээр", "5 дугаар", "6 дугаар",
             "7 дугаар", "8 дугаар", "9 дүгээр", "10 дугаар", "11 дүгээр", "12 дугаар"]


def fmt(n, unit="₮"):
    if n is None or (isinstance(n, float) and math.isnan(n)):
        return "—"
    return f"{n:,.0f}{unit}"


def money(v, suffix="₮"):
    """Дүнг масштаб тайлах боломжтой span дотор ороож гаргана."""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "—"
    return f'<span class="k" data-v="{v:.2f}" data-suffix="{suffix}">{fmt(v, suffix)}</span>'


def qty(amount, price):
    """Авах ширхэг. Коэффициентээс хамаарч хөтөч дээр дахин тооцогдоно."""
    if not price:
        return "—"
    return f'<span class="k" data-amt="{amount:.2f}" data-price="{price:.4f}">{int(amount // price)}</span>'


def pct(n, sign=False):
    if n is None or (isinstance(n, float) and math.isnan(n)):
        return "—"
    return f"{n:+.1f}%" if sign else f"{n:.1f}%"


# ------------------------------------------------------------------ өгөгдөл
def refresh_prices(symbols: list[str]) -> pd.DataFrame:
    """Симбол бүрийн бүх түүхийг татаж, өмнөх өгөгдөлтэй нэгтгэнэ."""
    DATA.mkdir(exist_ok=True)
    path = DATA / "prices.csv"
    old = pd.read_csv(path) if path.exists() else pd.DataFrame()

    secs = mse.parse_securities(mse.fetch("/securities", use_cache=False))
    code_of = dict(zip(secs.symbol.str.upper(), secs.company_code))
    frames = []
    for sym in symbols:
        code = code_of.get(sym.upper())
        if code is None:
            print(f"  ! {sym}: жагсаалтад олдсонгүй")
            continue
        try:
            df = mse.parse_prices(mse.fetch(f"/securities/{code}/tab/tradeinfo",
                                            use_cache=False), code)
            if df.empty:
                print(f"  ! {sym}: үнийн түүх хоосон")
                continue
            df.insert(1, "symbol", sym)
            frames.append(df)
            print(f"  {sym}: {len(df)} мөр")
        except Exception as e:
            print(f"  ! {sym}: {e}")

    if not frames:
        print("Шинэ өгөгдөл татагдсангүй, хуучнаар үргэлжлүүлнэ.")
        return old
    new = pd.concat([old, pd.concat(frames, ignore_index=True)], ignore_index=True)
    new = new.drop_duplicates(subset=["symbol", "date"], keep="last")
    new = new.sort_values(["symbol", "date"])
    new.to_csv(path, index=False, encoding="utf-8-sig")
    return new


def load_prices() -> pd.DataFrame:
    path = DATA / "prices.csv"
    if not path.exists():
        raise SystemExit("data/prices.csv алга. Эхлээд --no-fetch хийхгүйгээр ажиллуулна уу.")
    df = pd.read_csv(path)
    df.columns = [c.strip().lstrip("\ufeff") for c in df.columns]
    df["date"] = pd.to_datetime(df["date"])
    return df


# ------------------------------------------------------------------ тооцоо
def build_state(cfg: dict, px: pd.DataFrame) -> dict:
    uni, hold = cfg["universe"], cfg["holdings"]
    last = px.sort_values("date").groupby("symbol").last()
    asof = px["date"].max()

    rows = []
    for sym, u in uni.items():
        h = hold.get(sym, {"shares": 0, "cost": 0.0})
        price = last["close"].get(sym)
        value = (h["shares"] * price) if price is not None and h["shares"] else 0.0
        s = px[px.symbol == sym].sort_values("date")
        prev = s["close"].iloc[-2] if len(s) > 1 else None
        year = s[s["date"] > asof - pd.Timedelta(days=365)]
        traded = s[s["volume"] > 0]
        rows.append({
            "symbol": sym,
            "target": u["target_weight"],
            "shares": h["shares"],
            "cost": h["cost"],
            "price": price,
            "value": value,
            "day_chg": (price / prev - 1) * 100 if prev else None,
            "pnl_pct": (value / h["cost"] - 1) * 100 if h["cost"] else None,
            "dps": u.get("dividend_per_share", 0),
            "div_year": u.get("dividend_per_share", 0) * h["shares"],
            "yield_now": (u.get("dividend_per_share", 0) / price * 100) if price else None,
            "hi52": year["high"].max() if len(year) else None,
            "lo52": year["low"].min() if len(year) else None,
            "med_turnover": traded["turnover"].tail(120).median() if len(traded) else 0,
            "last_trade": traded["date"].max() if len(traded) else None,
        })
    df = pd.DataFrame(rows).set_index("symbol")

    stock_value = df["value"].sum()
    df["weight"] = df["value"] / stock_value * 100 if stock_value else 0
    df["drift"] = df["weight"] - df["target"]

    # энэ сарын худалдан авалт: зорилтоосоо хамгийн их хоцорсон нь
    cash = cfg["monthly_budget"] * (1 - cfg["fee_rate"])
    total_after = stock_value + cash
    gap = (df["target"] / 100 * total_after - df["value"]).clip(lower=0)
    picks = gap.sort_values(ascending=False).head(cfg["buys_per_month"])
    picks = picks[picks > 0]
    buys = []
    if picks.sum() > 0:
        for sym, g in picks.items():
            amount = cash * g / picks.sum()
            price = df.loc[sym, "price"]
            buys.append({
                "symbol": sym,
                "amount": amount,
                "price": price,
                "shares": int(amount // price) if price else None,
                "limit": price * 1.01 if price else None,
                "thin": df.loc[sym, "med_turnover"] and amount > df.loc[sym, "med_turnover"] * 0.25,
            })

    # анхааруулга
    a, alerts = cfg["alerts"], []
    for sym, r in df.iterrows():
        if r["day_chg"] is not None and abs(r["day_chg"]) >= a["daily_move_pct"]:
            alerts.append(f"{sym} нэг өдөрт {pct(r['day_chg'], True)} хөдөлсөн. Мэдээ гарсан эсэхийг шалгах.")
        if r["last_trade"] is not None and (asof - r["last_trade"]).days >= a["stale_days"]:
            alerts.append(f"{sym} сүүлийн {(asof - r['last_trade']).days} хоног арилжаагүй. Хөрвөх чадвар султай.")
        if r["price"] and r["lo52"] and r["price"] <= r["lo52"] * 1.02 and r["target"] > 0:
            alerts.append(f"{sym} жилийн доод үнэдээ ойрхон ({fmt(r['price'])}).")
    if date.today().month in a["dividend_season_months"]:
        alerts.append("Ногдол ашгийн улирал. ТУЗ-ийн шийдвэр, бүртгэлийн өдрийг шалгах хэрэгтэй. "
                      "Бүртгэлийн өдрөөс хойш авсан хувьцаа тухайн жилийн ногдол ашиг авахгүй.")
    days_late = (pd.Timestamp(date.today()) - asof).days
    if days_late > 4:
        alerts.append(f"Өгөгдөл {days_late} хоногийн өмнөх байна. Татагч ажиллаж байгаа эсэхийг шалга.")

    other = cfg.get("other_assets", [])
    other_value = sum(o["value"] for o in other)
    return {
        "df": df, "asof": asof, "buys": buys, "alerts": alerts,
        "stock_value": stock_value, "other": other, "other_value": other_value,
        "total": stock_value + other_value,
        "cost": df["cost"].sum(),
        "div_year": (df["div_year"]).sum(),
        "drift_total": df["drift"].abs().sum() / 2,
    }


# ------------------------------------------------------------------ хуудас
CSS = """
:root{
  --paper:#EEF1F0; --card:#FFFFFF; --ink:#16202B; --muted:#5C6B7A;
  --line:#D7DEDC; --up:#2E6E4E; --down:#A4433B; --brass:#B8862F; --rule:#C3CCC9;
}
@media (prefers-color-scheme:dark){
  :root{--paper:#11181E; --card:#18212A; --ink:#E7EDEB; --muted:#93A3AE;
        --line:#26323C; --up:#63B58A; --down:#D9847C; --brass:#D9AC5A; --rule:#2C3944;}
}
*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);
  font-family:"IBM Plex Sans","Segoe UI",system-ui,sans-serif;
  font-variant-numeric:tabular-nums;line-height:1.5;}
main{max-width:680px;margin:0 auto;padding:28px 18px 72px}
h1{font-size:1.05rem;font-weight:600;margin:0;letter-spacing:.01em}
h2{font-size:.95rem;font-weight:600;margin:38px 0 10px;padding-bottom:6px;
   border-bottom:1px solid var(--rule)}
.sub{color:var(--muted);font-size:.8rem;margin:2px 0 26px}
.buy{background:var(--card);border:1px solid var(--line);border-left:3px solid var(--brass);
     padding:14px 16px;margin-bottom:8px;display:flex;justify-content:space-between;gap:14px;align-items:baseline}
.buy .s{font-size:1.25rem;font-weight:600}
.buy .r{text-align:right;font-size:.85rem;color:var(--muted)}
.buy .amt{font-size:1rem;color:var(--ink);font-weight:500}
table{width:100%;border-collapse:collapse;font-size:.85rem}
th{text-align:right;font-weight:500;color:var(--muted);padding:6px 4px;border-bottom:1px solid var(--rule);font-size:.75rem}
th:first-child,td:first-child{text-align:left}
td{padding:9px 4px;border-bottom:1px solid var(--line);text-align:right}
.up{color:var(--up)} .down{color:var(--down)}
.bar{display:block;height:4px;background:var(--line);position:relative;margin-top:5px;width:100%}
.bar i{position:absolute;left:0;top:0;height:100%;background:var(--brass)}
.bar u{position:absolute;top:-2px;height:8px;width:2px;background:var(--ink);opacity:.75}
.note{background:var(--card);border:1px solid var(--line);padding:12px 14px;margin-bottom:6px;font-size:.85rem}
.tot{display:flex;flex-wrap:wrap;gap:26px;margin:18px 0 4px}
.tot div span{display:block;color:var(--muted);font-size:.75rem}
.tot div b{font-size:1.15rem;font-weight:600}
.scale{margin-top:28px;font-size:.8rem;color:var(--muted);display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.scale input{width:90px;padding:5px 7px;border:1px solid var(--line);background:var(--card);
  color:var(--ink);font:inherit;font-size:.8rem;border-radius:2px}
.scale input:focus{outline:2px solid var(--brass);outline-offset:1px}
footer{margin-top:44px;color:var(--muted);font-size:.75rem;border-top:1px solid var(--rule);padding-top:12px}
"""


def render(state: dict, cfg: dict) -> str:
    df, asof = state["df"], state["asof"]
    today = date.today()
    head = (f'<link rel="preconnect" href="https://fonts.googleapis.com">'
            f'<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&display=swap" rel="stylesheet">')

    # hero: энэ сарын худалдан авалт
    if state["buys"]:
        hero = ""
        for b in state["buys"]:
            warn = " · гүйлгээ багатай, хэсэгчлэн ав" if b["thin"] else ""
            hero += (f'<div class="buy"><div><div class="s">{b["symbol"]}</div>'
                     f'<div class="r">{qty(b["amount"], b["price"])} ш · дээд үнэ {fmt(b["limit"])}{warn}</div></div>'
                     f'<div class="amt">{money(b["amount"])}</div></div>')
    else:
        hero = '<div class="note">Энэ сар нэмэх шаардлагагүй. Багц зорилтот жиндээ байна.</div>'

    # багцын хүснэгт
    body = ""
    for sym, r in df.sort_values("value", ascending=False).iterrows():
        if r["shares"] == 0 and r["target"] == 0:
            continue
        cls = "up" if (r["pnl_pct"] or 0) >= 0 else "down"
        w, t = r["weight"], r["target"]
        bar = (f'<span class="bar"><i style="width:{min(w,100):.0f}%"></i>'
               f'<u style="left:{min(t,100):.0f}%"></u></span>')
        body += (f'<tr><td>{sym}{bar}</td><td>{fmt(r["price"],"")}</td>'
                 f'<td class="{cls}">{pct(r["pnl_pct"],True)}</td>'
                 f'<td>{money(r["value"])}</td>'
                 f'<td>{w:.1f} / {t:.0f}</td>'
                 f'<td>{pct(r["yield_now"])}</td></tr>')

    alerts = "".join(f'<div class="note">{a}</div>' for a in state["alerts"]) \
        or '<div class="note">Онцгой зүйл алга.</div>'

    other = "".join(
        f'<tr><td>{o["name"]}</td><td colspan="4">{o.get("note","")}</td>'
        f'<td>{fmt(o["value"])}</td></tr>' for o in state["other"])

    pnl = state["stock_value"] - state["cost"]
    return f"""<!doctype html><html lang="mn"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Багцын байдал</title>{head}<style>{CSS}</style></head><body><main>
<h1>{MN_MONTHS[today.month-1]} сард юу авах вэ</h1>
<p class="sub">Сарын {money(cfg['monthly_budget'])} · үнэ {asof:%Y-%m-%d}-ний хаалтаар</p>
{hero}

<h2>Багц</h2>
<div class="tot">
  <div><span>Хувьцаа</span><b>{money(state['stock_value'])}</b></div>
  <div><span>Ашиг</span><b class="{'up' if pnl>=0 else 'down'}">{money(pnl)}</b></div>
  <div><span>Жилийн ногдол ашиг</span><b>{money(state['div_year'])}</b></div>
  <div><span>Зорилтоос хазайлт</span><b>{state['drift_total']:.1f}%</b></div>
</div>
<table><thead><tr><th>Хувьцаа</th><th>Үнэ</th><th>Ашиг</th><th>Үнэ цэнэ</th>
<th>Жин / зорилт</th><th>Ног.ашиг</th></tr></thead><tbody>{body}</tbody></table>

<h2>Анхаарах зүйл</h2>
{alerts}

<h2>Бусад хөрөнгө</h2>
<table><tbody>{other}
<tr><td><b>Нийт хөрөнгө</b></td><td colspan="4"></td><td><b>{money(state['total'])}</b></td></tr>
</tbody></table>

<div class="scale">
  <label for="k">Хувийн коэффициент</label>
  <input id="k" type="number" step="any" min="0" placeholder="1" inputmode="decimal">
  <span id="khint">Зөвхөн энэ төхөөрөмж дээр хадгалагдана</span>
</div>

<script>
(function(){{
  var box=document.getElementById('k'), hint=document.getElementById('khint');
  function get(){{ try{{ return parseFloat(localStorage.getItem('mse-scale'))||1; }}catch(e){{ return 1; }} }}
  function nf(v){{ return Math.round(v).toLocaleString('en-US'); }}
  function apply(k){{
    document.querySelectorAll('.k').forEach(function(el){{
      if(el.dataset.amt){{
        var p=parseFloat(el.dataset.price);
        el.textContent = p ? nf(Math.floor(parseFloat(el.dataset.amt)*k/p)) : '—';
      }} else {{
        el.textContent = nf(parseFloat(el.dataset.v)*k) + (el.dataset.suffix||'');
      }}
    }});
    hint.textContent = k===1 ? 'Зөвхөн энэ төхөөрөмж дээр хадгалагдана'
                             : 'Бодит дүнгээр харуулж байна';
  }}
  var k=get(); if(k!==1) box.value=k; apply(k);
  box.addEventListener('input', function(){{
    var v=parseFloat(box.value)||1;
    try{{ localStorage.setItem('mse-scale', v); }}catch(e){{}}
    apply(v);
  }});
}})();
</script>

<footer>Шинэчлэгдсэн {datetime.now():%Y-%m-%d %H:%M} · өгөгдөл open.mse.mn.
Зөвлөгөө биш, зөвхөн өөрийн хяналтад зориулсан тооцоолол.</footer>
</main></body></html>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-fetch", action="store_true")
    args = ap.parse_args()
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    symbols = sorted(set(cfg["universe"]) | set(cfg["holdings"]))

    if not args.no_fetch:
        print("Өгөгдөл татаж байна...")
        refresh_prices(symbols)
    px = load_prices()
    state = build_state(cfg, px)
    DOCS.mkdir(exist_ok=True)
    (DOCS / "index.html").write_text(render(state, cfg), encoding="utf-8")
    print(f"Бэлэн: docs/index.html · {state['asof']:%Y-%m-%d} · "
          f"хувьцаа {state['stock_value']:,.0f}₮")


if __name__ == "__main__":
    main()
