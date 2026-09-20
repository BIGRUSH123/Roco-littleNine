"""dbg_py_obs — 打印指定特性观察者编译后的 then。

用法：env\\python.exe native/tools/dbg_py_obs.py <特性名>
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from backend.engine.trait_loader import TraitLoader, _trait_file_cache  # noqa: E402
from backend.engine.observer import ObserverRegistry  # noqa: E402

name = sys.argv[1]
loader = TraitLoader(ObserverRegistry(), data_dir=str(ROOT / "data" / "traits"))
from backend.common.models import SpeciesStats  # noqa: E402

ids = json_load = __import__("json").loads((ROOT / "data" / "traits" / "_ids.json").read_text(encoding="utf-8"))
tid = ids.get("ids", {}).get(name)
data = loader._load_trait_data(tid, name)
if data is None:
    print("trait data not found:", name)
    raise SystemExit(0)
print("trait effects raw:")
for e in data.get("effects", []):
    print("  ", e)

# 编译观察者（走 _index 的 compile 路径）
from backend.engine.trait_loader import TraitToObserver  # noqa: E402

compiled = TraitToObserver().compile(data)
for obs in compiled:
    print("compiled cond:", obs.cond)
    print("compiled then:")
    for eff in obs.then:
        print("   ", type(eff).__name__, eff)
