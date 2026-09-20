"""audit_ctx_fields — 审计 rust Ctx 字段是否在 snapshot.rs 中赋值。

用法：env\\python.exe native/tools/audit_ctx_fields.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

src = Path(__file__).resolve().parents[1] / "roco-core" / "src"
ctx_rs = (src / "vm_ctx.rs").read_text(encoding="utf-8")
snap_rs = (src / "snapshot.rs").read_text(encoding="utf-8")

# Ctx struct 字段（pub name: type,）
m = re.search(r"pub struct Ctx \{(.*?)\n\}", ctx_rs, re.S)
if not m:
    print("Ctx struct not found")
    sys.exit(1)
body = m.group(1)
fields = re.findall(r"pub (\w+):", body)

# event 字段
m2 = re.search(r"pub struct EventContext \{(.*?)\n\}", ctx_rs, re.S)
event_fields = re.findall(r"pub (\w+):", m2.group(1)) if m2 else []

missing = [
    f for f in fields
    if not re.search(rf"\b{f}\s*[,:)]", snap_rs) and f not in ("event",)
]
missing_event = [f for f in event_fields if not re.search(rf"event\.{f}\s*=", snap_rs)]

print(f"Ctx 字段 {len(fields)} 个；snapshot 未赋值 {len(missing)} 个：")
for f in missing:
    print(f"  - {f}")
print(f"\nEventContext 字段 {len(event_fields)} 个；snapshot 未设置 {len(missing_event)} 个：")
for f in missing_event:
    print(f"  - {f}")
