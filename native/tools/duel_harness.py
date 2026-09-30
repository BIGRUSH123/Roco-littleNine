"""兼容入口：实现已拆到 `native/tools/duel/`（`core` = 引擎侧，`cli` = 命令行）。

保留本文件是为了让文档里的
`env\\python.exe native/tools/duel_harness.py <子命令>` 与其它工具里的
`import duel_harness as H; H._build_battle(...)` 照旧可用。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from duel.cli import main  # noqa: E402
from duel.core import *  # noqa: F401,F403
from duel.core import (  # noqa: F401  私有名也要转发（别的工具在用）
    _ACTION_ALIASES,
    _BENCH_KEYS,
    _NAME_KEYS,
    _REASON_KEYS,
    _THEN_KEYS,
    _VARIANT_KEYS,
    _answer_path,
    _bloodline_note,
    _build_battle,
    _cfg_from_session,
    _damage_section,
    _default_bench,
    _dispatch_path,
    _effect_brief,
    _extract_json,
    _history_path,
    _item_block_reason,
    _item_by_name,
    _item_menu,
    _kind,
    _label,
    _last_turn_events,
    _legal_menu,
    _load_team_spec,
    _neutralize,
    _normalize_note,
    _note_path,
    _pending_path,
    _pick,
    _post_item_skills_fn,
    _prompt_battle,
    _prompt_path,
    _prompt_sha,
    _read_history,
    _read_note,
    _read_session,
    _render,
    _replay,
    _resolve_answer,
    _resolve_skill,
    _resolve_switch,
    _session_path,
    _side_block,
    _skill_blocked,
    _skill_brief,
    _skill_line,
    _skill_names,
    _static_post_item_skills,
    _StrictAgent,
    _team_sheet,
)

if __name__ == "__main__":
    main()
