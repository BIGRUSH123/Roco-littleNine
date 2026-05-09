"""scripts/common/sprite_db.py — 精灵种族值数据库

两个数据来源（优先级由高到低）：
  1. wiki/精灵图鉴/**/*.md frontmatter（含 form 拆分）
  2. wiki/meta/sprites.csv（spd → speed 字段映射）

sim 和 calc 共用此模块。
"""

import csv
import re
import sys
from pathlib import Path
from typing import Optional

from .models import SpeciesStats


def _err(msg: str) -> None:
    print(msg, file=sys.stderr)


class SpriteDB:
    """精灵种族值数据库。从 wiki 文件加载，提供按名称/形态查询。"""

    def __init__(self, wiki_root: Path):
        self._db: dict[str, SpeciesStats] = {}
        self._index: dict[str, list[str]] = {}
        self._load_wiki(wiki_root / "精灵图鉴")
        self._load_csv(wiki_root / "meta" / "sprites.csv")

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
                continue
            self._db[display] = stats
            self._index.setdefault(base_name, []).append(display)
            cnt += 1
        _err(f"[SpriteDB] wiki 加载 {cnt} 个精灵")

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

    def get(self, name: str, form: str = '') -> Optional[SpeciesStats]:
        """精确查询：按 (name, form) 找到唯一形态。"""
        m = self._RE_FORM_SUFFIX.search(name)
        if m and not form:
            form = m.group(1)
            name = name[:m.start()].strip()

        key = SpeciesStats(name=name, form=form).display_name()
        if key in self._db:
            return self._db[key]
        candidates = self._index.get(name, [])
        if len(candidates) == 1:
            return self._db[candidates[0]]
        if candidates and not form:
            for d in candidates:
                if self._db[d].form == '':
                    return self._db[d]
        return None

    def list_forms(self, name: str) -> list[str]:
        """返回某个 base name 下的所有形态名。"""
        return [self._db[d].form for d in self._index.get(name, [])]
