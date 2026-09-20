"""临时调试9：打印迫近攻击技能定义。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

data = json.loads(Path("data/skills/迫近攻击.json").read_text(encoding="utf-8"))
print(json.dumps(data, ensure_ascii=False, indent=1))
