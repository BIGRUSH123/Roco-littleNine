# -*- coding: utf-8 -*-
"""兼容入口：实现已拆到 `native/tools/duel/`（`core` = 引擎侧，`cli` = 命令行）。

保留本文件是为了让文档里的
`env\\python.exe native/tools/duel_harness.py <子命令>` 与其它工具里的
`import duel_harness as H; H._build_battle(...)` 照旧可用。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from duel.core import *  # noqa: F401,F403
from duel.core import (  # noqa: F401  私有名也要转发（别的工具在用）
    _ACTION_ALIASES, _NAME_KEYS, _VARIANT_KEYS, _THEN_KEYS, _BENCH_KEYS, _REASON_KEYS, _item_by_name, _load_team_spec, _neutralize, _build_battle, _kind, _skill_brief, _effect_brief, _skill_line, _bloodline_note, _side_block, _last_turn_events, _render, _team_sheet, _skill_blocked, _item_block_reason, _static_post_item_skills, _item_menu, _legal_menu, _extract_json, _pick, _skill_names, _resolve_skill, _resolve_switch, _resolve_answer, _StrictAgent, _session_path, _history_path, _read_session, _read_history, _cfg_from_session, _replay, _prompt_battle, _post_item_skills_fn, _default_bench, _answer_path, _pending_path, _prompt_path, _prompt_sha, _dispatch_path, _note_path, _read_note, _normalize_note, _label, _damage_section
)
from duel.cli import main  # noqa: E402

if __name__ == "__main__":
    main()
