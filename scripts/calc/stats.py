#!/usr/bin/env python3
"""
scripts/calc_stats.py — 精灵六维属性计算器 CLI

根据精灵种类、性格、个体值、能力修正、特性，输出最终六项属性。

用法（CLI）：
  python scripts/calc_stats.py <精灵名> [--form <形态>] [--nature <性格>]
       [--iv-hp N --iv-atk N --iv-sp-atk N --iv-def N --iv-sp-def N --iv-speed N]
       [--mod "物攻+100%" "速度-30" ...]
       [--ability <特性名>] [--json]

数据来源：
  1. wiki/精灵图鉴/**/*.md 的 frontmatter（hp/atk/sp_atk/def/sp_def/speed）
  2. wiki/meta/sprites.csv（CSV 字段名 spd→speed）

退出码：
  0  正常
  1  精灵未找到 / 性格未识别 / 参数错误
"""

import csv
import json
import re
import sys
from pathlib import Path
from typing import Optional

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from scripts.common import (
    STAT_KEYS, STAT_LABELS, LABEL_TO_KEY,
    get_nature_coeff,
    half_round, apply_mods, StatsCalc,
    SpeciesStats, StatsResult,
)


# ═══════════════════════════════════════════════
# SpriteDB — 精灵种族值数据库
# ═══════════════════════════════════════════════

class SpriteDB:
    """两个数据来源（优先级由高到低）：
      1. wiki/精灵图鉴/**/*.md frontmatter（含 form 拆分）
      2. wiki/meta/sprites.csv（spd → speed 字段映射）
    """

    def __init__(self, wiki_root: Path):
        self._db: dict[str, SpeciesStats] = {}    # 主键：display_name
        self._index: dict[str, list[str]] = {}    # 名称 → display_name 列表
        self._load_wiki(wiki_root / "精灵图鉴")
        self._load_csv(wiki_root / "meta" / "sprites.csv")

    # ── wiki 加载 ───────────────────────────────────

    _RE_FORM_SUFFIX = re.compile(r'（([^）]+)）$')

    def _load_wiki(self, sprite_dir: Path) -> None:
        if not sprite_dir.is_dir():
            return
        cnt = 0
        for md in sprite_dir.rglob("*.md"):
            if md.name.startswith('_') or md.stem == 'index':
                continue
            try:
                text = md.read_text(encoding='utf-8', errors='ignore')
            except OSError:
                continue
            if not text.startswith('---'):
                continue
            end = text.find('\n---', 3)
            if end == -1:
                continue
            data = self._parse_fm(text[4:end])
            name_full = data.get('name', '').strip().strip('"').strip("'")
            if not name_full:
                continue
            # 名称中分离 base + form
            m = self._RE_FORM_SUFFIX.search(name_full)
            if m:
                base_name, form = name_full[:m.start()].strip(), m.group(1).strip()
            else:
                base_name, form = name_full, ''
            try:
                stats = SpeciesStats(
                    name=base_name, form=form,
                    hp=int(data.get('hp', '0')),
                    atk=int(data.get('atk', '0')),
                    sp_atk=int(data.get('sp_atk', '0')),
                    def_=int(data.get('def', '0')),
                    sp_def=int(data.get('sp_def', '0')),
                    speed=int(data.get('speed', '0')),
                    attributes=data.get('attributes', ''),
                )
            except (ValueError, TypeError):
                continue
            display = stats.display_name()
            if display in self._db:
                continue   # 已存在
            self._db[display] = stats
            self._index.setdefault(base_name, []).append(display)
            cnt += 1
        _err(f"[SpriteDB] wiki 加载 {cnt} 个精灵")

    # ── CSV 回退 ────────────────────────────────────

    def _load_csv(self, csv_path: Path) -> None:
        if not csv_path.is_file():
            _err(f"[SpriteDB] 未找到 CSV: {csv_path}")
            return
        cnt = 0
        try:
            with open(csv_path, encoding='utf-8-sig', newline='') as f:
                for row in csv.DictReader(f):
                    name = row.get('name', '').strip()
                    form = row.get('form', '').strip()
                    if not name:
                        continue
                    try:
                        stats = SpeciesStats(
                            name=name, form=form,
                            hp=int(row.get('hp', '0') or '0'),
                            atk=int(row.get('atk', '0') or '0'),
                            sp_atk=int(row.get('sp_atk', '0') or '0'),
                            def_=int(row.get('def', '0') or '0'),
                            sp_def=int(row.get('sp_def', '0') or '0'),
                            speed=int(row.get('spd', '0') or '0'),
                            attributes=row.get('attributes', ''),
                            ability=row.get('ability_name', ''),
                        )
                    except (ValueError, TypeError):
                        continue
                    display = stats.display_name()
                    if display in self._db:
                        # 仅补充缺失的 ability 字段
                        if not self._db[display].ability and stats.ability:
                            self._db[display].ability = stats.ability
                        continue
                    self._db[display] = stats
                    self._index.setdefault(name, []).append(display)
                    cnt += 1
        except OSError as e:
            _err(f"[SpriteDB] 读取 CSV 失败: {e}")
            return
        _err(f"[SpriteDB] CSV 补充 {cnt} 个精灵")

    @staticmethod
    def _parse_fm(raw: str) -> dict[str, str]:
        d: dict[str, str] = {}
        for line in raw.splitlines():
            if ':' not in line:
                continue
            k, _, v = line.partition(':')
            d[k.strip()] = v.strip().strip('"').strip("'")
        return d

    # ── 查询 ────────────────────────────────────────

    def get(self, name: str, form: str = '') -> Optional[SpeciesStats]:
        """精确查询：按 (name, form) 找到唯一形态。"""
        m = self._RE_FORM_SUFFIX.search(name)
        if m and not form:
            form = m.group(1)
            name = name[:m.start()].strip()

        key = SpeciesStats(name=name, form=form).display_name()
        if key in self._db:
            return self._db[key]
        # 模糊：匹配 base name 唯一形态
        candidates = self._index.get(name, [])
        if len(candidates) == 1:
            return self._db[candidates[0]]
        if candidates and not form:
            # 多个形态时，优先空 form
            for d in candidates:
                if self._db[d].form == '':
                    return self._db[d]
        return None

    def list_forms(self, name: str) -> list[str]:
        """返回某个 base name 下的所有形态名。"""
        return [self._db[d].form for d in self._index.get(name, [])]


# ═══════════════════════════════════════════════
# 4. 输出格式
# ═══════════════════════════════════════════════

def format_table(r: StatsResult) -> str:
    show_mods = bool(r.mods)
    cols = ['属性', '种族', '基础(IV后)', '性格后']
    if show_mods:
        cols.append('能力修正后')

    lines: list[str] = []
    name = r.species.display_name()
    lines.append(f"# {name}")
    if r.species.attributes:
        lines.append(f"- 属性：{r.species.attributes}")
    if r.species.ability:
        lines.append(f"- 特性：{r.species.ability}")
    if r.ability and r.ability != r.species.ability:
        lines.append(f"- 输入特性：{r.ability}")
    lines.append(f"- 性格：{r.nature or '（无）'}")
    iv_str = '/'.join(f"{STAT_LABELS[k]}{r.iv[k]}" for k in STAT_KEYS)
    lines.append(f"- 个体值：{iv_str}")
    if r.mods:
        lines.append(f"- 能力修正：{'、'.join(r.mods)}")
    lines.append('')

    lines.append('| ' + ' | '.join(cols) + ' |')
    lines.append('|' + '|'.join(['------'] * len(cols)) + '|')
    nat_coeff = get_nature_coeff(r.nature)
    for k in STAT_KEYS:
        marker = ''
        if nat_coeff[k] > 1.0:
            marker = '↑'
        elif nat_coeff[k] < 1.0:
            marker = '↓'
        row = [
            f"{STAT_LABELS[k]}{marker}",
            str(r.base_stats[k]),
            str(r.raw_stats[k]),
            str(r.nature_stats[k]),
        ]
        if show_mods:
            row.append(str(r.final_stats[k]))
        lines.append('| ' + ' | '.join(row) + ' |')
    return '\n'.join(lines)


def to_json(r: StatsResult) -> str:
    return json.dumps({
        'species': {
            'name': r.species.name, 'form': r.species.form,
            'attributes': r.species.attributes, 'ability': r.species.ability,
        },
        'nature': r.nature,
        'iv': r.iv,
        'mods': r.mods,
        'base_stats':    r.base_stats,
        'raw_stats':     r.raw_stats,
        'nature_stats':  r.nature_stats,
        'final_stats':   r.final_stats,
    }, ensure_ascii=False, indent=2)


# ═══════════════════════════════════════════════
# 5. CLI
# ═══════════════════════════════════════════════

def _err(msg: str) -> None:
    print(msg, file=sys.stderr)


def parse_args(argv: list[str]) -> dict:
    if not argv or argv[0] in ('-h', '--help'):
        print(__doc__)
        sys.exit(0)

    name = argv[0]
    args = argv[1:]
    out: dict = {
        'name': name, 'form': '',
        'nature': None,
        'iv': {k: 0 for k in STAT_KEYS},
        'mods': [],
        'ability': '',
        'json': False,
    }
    i = 0
    iv_alias = {
        '--iv-hp': 'hp', '--iv-atk': 'atk', '--iv-sp-atk': 'sp_atk',
        '--iv-def': 'def', '--iv-sp-def': 'sp_def', '--iv-speed': 'speed',
    }
    while i < len(args):
        a = args[i]
        if a == '--form' and i + 1 < len(args):
            out['form'] = args[i + 1]; i += 2
        elif a == '--nature' and i + 1 < len(args):
            out['nature'] = args[i + 1]; i += 2
        elif a == '--ability' and i + 1 < len(args):
            out['ability'] = args[i + 1]; i += 2
        elif a in iv_alias and i + 1 < len(args):
            try:
                out['iv'][iv_alias[a]] = int(args[i + 1])
            except ValueError:
                _err(f"[错误] {a} 需要整数")
                sys.exit(1)
            i += 2
        elif a == '--iv-all' and i + 1 < len(args):
            try:
                v = int(args[i + 1])
                for k in STAT_KEYS:
                    out['iv'][k] = v
            except ValueError:
                _err("[错误] --iv-all 需要整数")
                sys.exit(1)
            i += 2
        elif a == '--mod' and i + 1 < len(args):
            out['mods'].append(args[i + 1]); i += 2
        elif a == '--json':
            out['json'] = True; i += 1
        else:
            _err(f"[警告] 未识别参数：{a}")
            i += 1
    return out


def main() -> None:
    opts = parse_args(sys.argv[1:])
    db = SpriteDB(BASE / "wiki")
    species = db.get(opts['name'], opts['form'])
    if not species:
        forms = db.list_forms(opts['name'])
        if forms:
            _err(f"[错误] 精灵 {opts['name']!r} 有多种形态，请用 --form 指定其一：{forms}")
        else:
            _err(f"[错误] 找不到精灵：{opts['name']!r}")
        sys.exit(1)

    calc = StatsCalc()
    try:
        result = calc.compute(
            species=species,
            nature=opts['nature'],
            iv=opts['iv'],
            mods=opts['mods'],
            ability=opts['ability'],
        )
    except ValueError as e:
        _err(f"[错误] {e}")
        sys.exit(1)

    print(to_json(result) if opts['json'] else format_table(result))


if __name__ == '__main__':
    main()
