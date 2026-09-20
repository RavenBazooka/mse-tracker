"""
Бодит тоог масштаблаж, репод байршуулах config.json үүсгэнэ.

  python scale_config.py 7.4

config_real.json (бодит тоо, репод БАЙРШУУЛАХГҮЙ) -> config.json (масштаблагдсан).
Коэффициентээ хэнд ч хэлэхгүй, хуудас нээхдээ нэг удаа оруулна.
"""
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent
SRC, OUT = ROOT / "config_real.json", ROOT / "config.json"

if len(sys.argv) != 2:
    raise SystemExit("Хэрэглээ: python scale_config.py <коэффициент>   жишээ: 7.4")
k = float(sys.argv[1])
if k <= 0:
    raise SystemExit("Коэффициент эерэг тоо байх ёстой.")
if not SRC.exists():
    raise SystemExit("config_real.json олдсонгүй. config.json-ыг хувилж, бодит тоогоо бичнэ үү.")

c = json.loads(SRC.read_text(encoding="utf-8"))
c["monthly_budget"] = round(c["monthly_budget"] / k, 2)
for h in c["holdings"].values():
    h["shares"] = round(h["shares"] / k, 4)
    h["cost"] = round(h["cost"] / k, 2)
for o in c.get("other_assets", []):
    o["value"] = round(o["value"] / k, 2)
c.pop("_scale", None)

OUT.write_text(json.dumps(c, ensure_ascii=False, indent=2), encoding="utf-8")


def scale_csv(src: str, dst: str, columns: list[str]):
    """Арилжаа, ногдол ашгийн бүртгэлийг масштаблана. Үнэ нь нийтийн мэдээлэл тул хэвээр."""
    s = ROOT / src
    if not s.exists():
        return
    with s.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        for col in columns:
            if r.get(col) not in (None, ""):
                r[col] = f"{float(r[col]) / k:.4f}"
    with (ROOT / dst).open("w", encoding="utf-8-sig", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=rows[0].keys())
        wr.writeheader()
        wr.writerows(rows)
    print(f"{dst} бэлэн ({len(rows)} мөр).")


scale_csv("trades_real.csv", "trades.csv", ["shares", "fee"])
scale_csv("dividends_real.csv", "dividends.csv", ["amount"])
print(f"config.json бэлэн. Хуудсан дээр коэффициент {k} гэж оруулна. "
      f"config_real.json-ыг репод байршуулахгүй.")
