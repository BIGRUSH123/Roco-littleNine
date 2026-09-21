"""bloodline — 血脉判定（混血，游戏内文本 3015）。

「非本系血脉的精灵，包括非本系的系别血脉、首领血脉、奇异血脉、污染血脉等。」

数据面：`backend/engine/snapshot.py:build_ctx()` 预计算为
`Ctx.is_mixed_blood_self/opp`；条件 `is_mixed_blood`（`backend/vm/cond.py`）
只读寄存器。本模块是纯函数，供引擎任意位置复用（不依赖 battle 状态）。

血脉取值见 `backend/common/constants.BLOODLINES`：
  系别血脉（`普通`/`火`/`水`/…）或特殊血脉（`首领`/`污染`/`奇异`）。
"""

from __future__ import annotations

from backend.common.constants import SPECIAL_BLOODLINES

#: 特殊血脉（首领 / 污染 / 奇异）——持有者一律视为混血
_SPECIAL: frozenset[str] = frozenset(SPECIAL_BLOODLINES)


def is_mixed_blood(sprite) -> bool:
    """精灵是否混血。

    规则（对齐 3015 原文，逐条判定）：
      - 血脉是特殊血脉（首领/污染/奇异）→ 混血；
      - 血脉是系别但不在自身种族系别里 → 混血；
      - 血脉为空 → 非混血（拿不到血脉时不误判）；
      - 血脉 = 自身某一系别 → 非混血（本系血脉）。
    """
    if sprite is None:
        return False
    bloodline = (getattr(sprite, 'bloodline', '') or '').strip()
    if not bloodline:
        return False
    if bloodline in _SPECIAL:
        return True
    species = getattr(sprite, 'species', None)
    elements = getattr(species, 'elements', ()) or ()
    return bloodline not in elements
