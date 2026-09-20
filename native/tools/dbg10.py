"""临时调试8b：从 spec 读取 B0 的特性 JSON。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

spec = json.loads(Path("native/gate_specs/spec_0001.json").read_text(encoding="utf-8"))
ability = spec["players"][1]["sprites"][0]["ability"]
print("ability:", ability)
data = json.loads((Path("data/traits") / f"{ability}.json").read_text(encoding="utf-8"))
print(json.dumps(data, ensure_ascii=False, indent=1)[:2500])
