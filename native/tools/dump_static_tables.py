"""dump_static_tables — 从 Python oracle 导出静态数据表供 Rust 引擎加载。

用法（项目根）：env\\python.exe native/tools/dump_static_tables.py
输出：native/static_tables.json（印记模板 / 异常模板 / 属性克制表）
"""

from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.engine.abnormal_config import ABNORMAL_TEMPLATES
from backend.engine.devotion_config import DEVOTION_TYPES
from backend.engine.mark_config import MARK_TEMPLATES, NEGATIVE_MARK_NAMES, POSITIVE_MARK_NAMES
from backend.sim.resolver import _TYPE_CHART


def main() -> None:
    out = {
        "mark_templates": {k: dataclasses.asdict(v) for k, v in MARK_TEMPLATES.items()},
        "abnormal_templates": {k: dataclasses.asdict(v) for k, v in ABNORMAL_TEMPLATES.items()},
        "type_chart": _TYPE_CHART,
        "positive_marks": sorted(POSITIVE_MARK_NAMES),
        "negative_marks": sorted(NEGATIVE_MARK_NAMES),
        # 顺序敏感：py `random.choice(list(DEVOTION_TYPES.keys()))` 用插入序，
        # 必须原样导出（不能 sorted，否则随机奉献抽取结果不一致）
        "devotion_types": list(DEVOTION_TYPES.keys()),
        "devotion_config": DEVOTION_TYPES,
    }
    dest = Path(__file__).resolve().parents[1] / "static_tables.json"
    dest.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"wrote {dest} ({len(MARK_TEMPLATES)} marks, {len(ABNORMAL_TEMPLATES)} abnormals)")


if __name__ == "__main__":
    main()
