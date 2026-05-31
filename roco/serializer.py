"""roco.serializer — 导入导出 CLI + 公开 API

Usage:
    python -m roco.serializer export team --team A -o name
    python -m roco.serializer import team name
    python -m roco.serializer export match -o name
    python -m roco.serializer import match name
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_EXPORT_DIR = Path("exports")


def _ensure_dir() -> Path:
    _EXPORT_DIR.mkdir(exist_ok=True)
    return _EXPORT_DIR


# ═══════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════

def export_match(battle, name: str, output_dir: str | Path | None = None) -> Path:
    """Export a match to JSON + text files.

    Returns the path to the JSON file.
    """
    from backend.engine.serializer import battle_to_dict

    out = Path(output_dir) if output_dir else _ensure_dir()
    data = battle_to_dict(battle)

    # JSON
    json_path = out / f"{name}.roco-match.json"
    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    # Text (log)
    txt_path = out / f"{name}.roco-match.txt"
    lines = []
    lines.append(f"对局: {battle.player_a.name} vs {battle.player_b.name}")
    lines.append(f"回合: {battle.turn}")
    lines.append(f"天气: {battle.globals.weather or '无'}")
    lines.append(f"结果: {battle.winner or '进行中'}")
    lines.append("")
    for rec in battle.log:
        lines.append(rec.to_message())
        lines.append("")
    txt_path.write_text("\n".join(lines), encoding="utf-8")

    return json_path


def import_match(path: str | Path, factory) -> Any:
    """Import a match from a .roco-match.json file.

    factory: SimFactory instance (provides sprite_db + skill_loader)
    """
    from backend.engine.serializer import battle_from_dict

    data = json.loads(Path(path).read_text(encoding="utf-8"))
    battle = battle_from_dict(data, factory.sprite_db, factory._build_skill_list)
    return battle


def export_team(player, name: str, output_dir: str | Path | None = None) -> Path:
    """Export a team to JSON + text files."""
    from backend.engine.serializer import player_to_dict

    out = Path(output_dir) if output_dir else _ensure_dir()
    data = {
        "version": "1.0",
        "type": "team",
        "name": player.name,
    }
    data.update(player_to_dict(player))

    # JSON
    json_path = out / f"{name}.roco-team.json"
    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    # Text
    txt_path = out / f"{name}.roco-team.txt"
    lines = [f"队伍: {player.name} (生命: {player.lives})"]
    for s in player.team:
        skill_names = ", ".join(bs.base.name for bs in (s.skills or []) if bs.base)
        lines.append(f">>>SPRITE:{s.name}:{s.species.number}:{skill_names}")
    txt_path.write_text("\n".join(lines), encoding="utf-8")

    return json_path


def import_team(path: str | Path, factory) -> Any:
    """Import a team from a .roco-team.json file.

    factory: SimFactory instance
    """
    from backend.engine.serializer import player_from_dict

    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return player_from_dict(data, factory.sprite_db.get, factory._build_skill_list)


# ═══════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════

def _main():
    import argparse

    parser = argparse.ArgumentParser(description="导入导出工具")
    sub = parser.add_subparsers(dest="cmd")

    # export
    exp = sub.add_parser("export")
    exp_sub = exp.add_subparsers(dest="type")
    exp_team = exp_sub.add_parser("team")
    exp_team.add_argument("--team", choices=["A", "B"], default="A")
    exp_team.add_argument("-o", "--output", required=True)
    exp_match = exp_sub.add_parser("match")
    exp_match.add_argument("-o", "--output", required=True)

    # import
    imp = sub.add_parser("import")
    imp_sub = imp.add_subparsers(dest="type")
    imp_team = imp_sub.add_parser("team")
    imp_team.add_argument("name")
    imp_match = imp_sub.add_parser("match")
    imp_match.add_argument("name")

    args = parser.parse_args()
    if not args.cmd:
        parser.print_help()
        return

    from backend.sim.factory import SimFactory
    factory = SimFactory()

    if args.cmd == "export":
        if args.type == "team":
            print("导出队伍需要从正在进行的对局中调用 export_team(player, name)。")
            print("请使用 Python API: from roco.serializer import export_team")
        elif args.type == "match":
            print("导出对局需要从正在进行的对局中调用 export_match(battle, name)。")
            print("请使用 Python API: from roco.serializer import export_match")
    elif args.cmd == "import":
        if args.type == "team":
            name = args.name
            path = _EXPORT_DIR / f"{name}.roco-team.json"
            if not path.exists():
                print(f"文件不存在: {path}")
                sys.exit(1)
            player = import_team(path, factory)
            print(f"导入队伍: {player.name} ({len(player.team)} 精灵)")
            for s in player.team:
                print(f"  {s.name} HP={s.current_hp}/{s.max_hp} E={s.energy}")
        elif args.type == "match":
            name = args.name
            path = _EXPORT_DIR / f"{name}.roco-match.json"
            if not path.exists():
                print(f"文件不存在: {path}")
                sys.exit(1)
            battle = import_match(path, factory)
            print(f"导入对局: {battle.player_a.name} vs {battle.player_b.name}")
            print(f"  回合: {battle.turn}")
            print(f"  天气: {battle.globals.weather or '无'}")
            print(f"  日志: {len(battle.log)} 条回合记录")


if __name__ == "__main__":
    _main()
