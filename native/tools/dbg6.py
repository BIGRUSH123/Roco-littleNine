"""临时调试4：全局搜索中毒 persistent 技能。"""

from __future__ import annotations

import glob
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.stdout.reconfigure(encoding="utf-8")

hits = []
for f in glob.glob("data/skills/*.json"):
    try:
        data = json.loads(Path(f).read_text(encoding="utf-8"))
    except Exception:
        continue
    for e in data.get("effects", []):
        if e.get("op") == "abnormal" and e.get("name") == "中毒":
            if e.get("scope") == "persistent" or e.get("value", {}).get("scale") == 2 or e.get("stacks") == 2:
                hits.append((f, json.dumps(e, ensure_ascii=False)[:260]))
for f, e in hits[:8]:
    print(f, e)
print("total:", len(hits))
