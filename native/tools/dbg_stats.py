"""dbg_stats — 对比 py StatsCalc 与 rust species_db 公式（形态变换用）。

用法：env\\python.exe native/tools/dbg_stats.py <spec> <A|B> <sprite_idx> <target_species>
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.stdout.reconfigure(encoding="utf-8")

from backend.common.formulas import StatsCalc  # noqa: E402
from backend.common.sprite_db import SpriteDB  # noqa: E402

spec = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
pi = 0 if sys.argv[2].upper() == "A" else 1
sprite = spec["players"][pi]["sprites"][int(sys.argv[3])]
target = sys.argv[4] if len(sys.argv) > 4 else sprite["name"]
nature = sprite.get("nature")
iv = dict(sprite.get("iv") or {})

root = Path(__file__).resolve().parents[2]
db = SpriteDB(root)
sp = db.get(target)
print("species:", sp.name, sp.form, sp.number, "| nature:", nature, "iv:", iv)
res = StatsCalc.compute(sp, nature=nature, iv=iv)
print("py final:  ", dict(res.final_stats))


def half_round(x: float) -> int:
    return int(x + 0.5) if x >= 0 else -int(-x + 0.5)


TABLE = {
    '聪明': ('sp_atk', 'atk'), '专注': ('sp_atk', 'def'), '偏执': ('sp_atk', 'sp_def'),
    '冷静': ('sp_atk', 'speed'), '理性': ('sp_atk', 'hp'), '固执': ('atk', 'sp_atk'),
    '大胆': ('atk', 'def'), '调皮': ('atk', 'sp_def'), '勇敢': ('atk', 'speed'),
    '逞强': ('atk', 'hp'), '警惕': ('sp_def', 'atk'), '害羞': ('sp_def', 'sp_atk'),
    '温顺': ('sp_def', 'def'), '慎重': ('sp_def', 'speed'), '焦虑': ('sp_def', 'hp'),
    '稳重': ('def', 'atk'), '天真': ('def', 'sp_atk'), '悠闲': ('def', 'speed'),
    '懒散': ('def', 'sp_def'), '坦率': ('def', 'hp'), '胆小': ('speed', 'atk'),
    '开朗': ('speed', 'sp_atk'), '急躁': ('speed', 'def'), '莽撞': ('speed', 'sp_def'),
    '热情': ('speed', 'hp'), '沉默': ('hp', 'atk'), '平和': ('hp', 'sp_atk'),
    '忧郁': ('hp', 'def'), '粗心': ('hp', 'sp_def'), '踏实': ('hp', 'speed'),
}
base = {"hp": sp.hp, "atk": sp.atk, "sp_atk": sp.sp_atk, "def": sp.def_,
        "sp_def": sp.sp_def, "speed": sp.speed}
coeffs = {k: 1.0 for k in base}
if nature and nature in TABLE:
    plus, minus = TABLE[nature]
    coeffs[plus] = 1.20
    coeffs[minus] = 0.90
out = {}
for k, b in base.items():
    L = (b + iv.get(k, 0) * 3) / 100.0
    initial = half_round(170 * L + 70) if k == "hp" else math.floor(110 * L + 10)
    out[k] = half_round(initial * coeffs[k] + 100) if k == "hp" else half_round(initial * coeffs[k] + 50)
print("rust final:", out)
