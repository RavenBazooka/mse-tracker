"""
Бодит тоог масштаблаж, репод байршуулах config.json үүсгэнэ.

  python scale_config.py 7.4

config_real.json (бодит тоо, репод БАЙРШУУЛАХГҮЙ) -> config.json (масштаблагдсан).
Коэффициентээ хэнд ч хэлэхгүй, хуудас нээхдээ нэг удаа оруулна.
"""
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
print(f"config.json бэлэн. Хуудсан дээр коэффициент {k} гэж оруулна. "
      f"config_real.json-ыг репод байршуулахгүй.")
