#!/usr/bin/env python3
"""
scripts/build_skill_effects.py — 从技能 description 提取 buff/debuff 效果并写回 frontmatter

用法：
  python scripts/build_skill_effects.py [--dry-run] [--verbose]

功能：
  1. 扫描 wiki/技能图鉴/**/*.md
  2. 用正则从 description 字段提取效果
  3. 在 frontmatter 中写入/更新 self_buffs / opp_buffs / self_debuffs / opp_debuffs 字段
  4. 输出摘要（处理数、识别数）

效果字段格式（YAML 列表，每项为字符串）：
  self_buffs:   ["物攻+100%", "蓄势印记×3", "能耗-2"]
  opp_debuffs:  ["星陨印记×3", "中毒", "能耗+2"]
  self_debuffs: ["双防-40%"]
  opp_buffs:    []                      # 作用于对手的增益（极少）

未能识别的效果不写入（留空列表），可人工补充。
"""

import re
import sys
from pathlib import Path

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

BASE = Path(__file__).resolve().parent.parent

# ─── 游戏效果类型常量 ────────────────────────────────────────────

STAT_TYPES = ['物攻', '魔攻', '物防', '魔防', '双攻', '双防', '速度', '生命']
STAT_PAT   = '|'.join(STAT_TYPES)

MARK_TYPES = [
    '星陨印记', '光合印记', '降临印记', '润泽印记', '蓄势印记', '蓄电印记',
    '龙式印记', '中毒印记', '减速印记', '棘刺', '迟缓', '风起',
    '攻击印记',
]
MARK_PAT = '|'.join(re.escape(m) for m in sorted(MARK_TYPES, key=len, reverse=True))

ABNORMAL_TYPES = ['萌化', '中毒', '寄生', '冻结', '灼烧', '晕眩', '眩晕']
ABNORMAL_PAT   = '|'.join(re.escape(a) for a in sorted(ABNORMAL_TYPES, key=len, reverse=True))


# ─── 工具函数 ─────────────────────────────────────────────────────

def dedup(lst: list[str]) -> list[str]:
    seen: set[str] = set()
    return [x for x in lst if not (x in seen or seen.add(x))]  # type: ignore[func-returns-value]


def parse_stat_changes(text: str) -> list[str]:
    """
    从文本中提取属性变化，返回 ["物攻+100%", "速度-30", ...] 列表。
    处理以下格式：
      - "物攻+100%"
      - "双攻和双防-100%"  → ["双攻-100%", "双防-100%"]
      - "物攻+100%和速度-30" → ["物攻+100%", "速度-30"]
    """
    results: list[str] = []

    # 格式 A：stat1和stat2 共享一个符号值，如 "双攻和双防-100%"
    for m in re.finditer(
        rf'({STAT_PAT})和({STAT_PAT})([+\-]\d+(?:\.\d+)?%?)', text
    ):
        s1, s2, val = m.group(1), m.group(2), m.group(3)
        results.append(f'{s1}{val}')
        results.append(f'{s2}{val}')

    # 格式 B：单个属性，如 "物攻+100%"
    for m in re.finditer(
        rf'({STAT_PAT})([+\-]\d+(?:\.\d+)?%?)', text
    ):
        stat, val = m.group(1), m.group(2)
        entry = f'{stat}{val}'
        # 避免与格式 A 重复（格式 A 里已经追加了）
        if entry not in results:
            results.append(entry)

    return results


def classify_stat_changes(
    changes: list[str],
    target: str,  # 'self' or 'opp'
    self_buffs: list, opp_debuffs: list,
    self_debuffs: list, opp_buffs: list,
) -> None:
    for c in changes:
        m = re.match(rf'({STAT_PAT})(.+)', c)
        if not m:
            continue
        sign = m.group(2)
        if target == 'self':
            if sign.startswith('+'):
                self_buffs.append(c)
            else:
                self_debuffs.append(c)
        else:  # opp
            if sign.startswith('-'):
                opp_debuffs.append(c)
            else:
                opp_buffs.append(c)


# ─── 效果提取主函数 ───────────────────────────────────────────────

def extract_effects(description: str) -> dict[str, list[str]]:
    """
    从 description 文本中提取效果，返回四个列表字典。
    """
    d = description
    self_buffs:   list[str] = []
    opp_debuffs:  list[str] = []
    self_debuffs: list[str] = []
    opp_buffs:    list[str] = []

    # ── 1. 属性增益/减益提取 ─────────────────────────────────────

    # 1a. 自己/自身获得… (含条件前缀 "若…，自己获得")
    for m in re.finditer(r'(?:自己|自身)(?:和队友)?获得(.{1,60}?)(?:[。；，]|$)', d):
        chunk = m.group(1)
        changes = parse_stat_changes(chunk)
        classify_stat_changes(changes, 'self', self_buffs, opp_debuffs, self_debuffs, opp_buffs)

    # 1b. 自己和敌方获得… → 双方都受效果影响
    for m in re.finditer(r'自己和敌方获得(.{1,60}?)(?:[。；，：]|$)', d):
        chunk = m.group(1)
        changes = parse_stat_changes(chunk)
        classify_stat_changes(changes, 'self', self_buffs, opp_debuffs, self_debuffs, opp_buffs)
        classify_stat_changes(changes, 'opp', self_buffs, opp_debuffs, self_debuffs, opp_buffs)

    # 1c. 敌方获得… (直接匹配，避免误捕 "若击败敌方，自己获得…")
    #     只在 "敌方获得" 连写时匹配，不用模糊中间字符
    for m in re.finditer(r'(?:使|令)?敌方(?:精灵)?获得(.{1,60}?)(?:[。；，]|$)', d):
        chunk = m.group(1)
        changes = parse_stat_changes(chunk)
        classify_stat_changes(changes, 'opp', self_buffs, opp_debuffs, self_debuffs, opp_buffs)

    # 1d. 全体获得（通常作用于己方队伍）
    for m in re.finditer(r'全体(?:获得|提升)(.{1,30}?)(?:[。；，]|$)', d):
        chunk = m.group(1)
        changes = parse_stat_changes(chunk)
        classify_stat_changes(changes, 'self', self_buffs, opp_debuffs, self_debuffs, opp_buffs)

    # ── 2. 能耗效果提取 ───────────────────────────────────────────
    # 格式：敌方获得全技能能耗+N  /  自己获得全技能能耗-N
    # 注意：区分"全技能"（all skills）还是"本技能"（this skill, 非状态效果）

    # 敌方能耗增加 → opp_debuffs: "能耗+N"
    for m in re.finditer(r'敌方获得(?:全技能?|攻击技能?)能耗([+\-]\d+)', d):
        opp_debuffs.append(f'能耗{m.group(1)}')

    # 应对后敌方能耗增加
    for m in re.finditer(r'应对.*?(?:敌方获得|使敌方)(?:全技能?)?能耗([+\-]\d+)', d):
        opp_debuffs.append(f'能耗{m.group(1)}')

    # 自己获得能耗减少 → self_buffs: "能耗-N"
    for m in re.finditer(r'(?:自己|自身)获得(?:全技能?)?能耗([+\-]\d+)', d):
        val = m.group(1)
        if val.startswith('-'):
            self_buffs.append(f'能耗{val}')
        else:
            self_debuffs.append(f'能耗{val}')

    # 注意：不设通用 fallback，"获得能耗" 的主语不明确时不提取（避免误判）

    # ── 3. 印记提取 ───────────────────────────────────────────────

    # 3a. 含层数：自己/敌方获得 N 层 X印记
    for m in re.finditer(
        rf'(自己|自身|我方|敌方|对手)(?:精灵)?(?:.*?)?获得(\d+)层({MARK_PAT})', d
    ):
        target_word = m.group(1)
        stacks = m.group(2)
        mark   = m.group(3)
        entry  = f'{mark}×{stacks}'
        if target_word in ('自己', '自身', '我方'):
            self_buffs.append(entry)
        else:
            opp_debuffs.append(entry)

    # 3b. 无层数：自己获得光合印记
    for m in re.finditer(
        rf'(自己|自身|我方)获得({MARK_PAT})(?![×\d])', d
    ):
        mark = m.group(2)
        entry = mark
        if entry not in self_buffs:
            self_buffs.append(entry)

    # 3c. 无层数：敌方获得减速印记
    for m in re.finditer(
        rf'(?:使|令)?(?:敌方|对手)获得({MARK_PAT})(?![×\d])', d
    ):
        mark = m.group(1)
        entry = mark
        if entry not in opp_debuffs:
            opp_debuffs.append(entry)

    # ── 4. 异常状态提取 ───────────────────────────────────────────

    # 4a. 含层数：敌方获得 N 层 异常
    for m in re.finditer(
        rf'(?:使|令)?(?:敌方|对手).*?(\d+)层({ABNORMAL_PAT})', d
    ):
        stacks, ab = m.group(1), m.group(2)
        entry = f'{ab}×{stacks}'
        if entry not in opp_debuffs:
            opp_debuffs.append(entry)

    # 4b. 无层数：使敌方萌化 / 敌方冻结 / 令敌方中毒
    for m in re.finditer(
        rf'(?:使|令)?(?:敌方|对手)(?:精灵)?(?:获得|进入|陷入)?({ABNORMAL_PAT})', d
    ):
        ab = m.group(1)
        if ab not in opp_debuffs and not any(ab in e for e in opp_debuffs):
            opp_debuffs.append(ab)

    # 4c. "敌方萌化" 简写直接包含
    for ab in ABNORMAL_TYPES:
        if f'敌方{ab}' in d or f'使敌方{ab}' in d or f'令敌方{ab}' in d:
            if ab not in opp_debuffs and not any(ab in e for e in opp_debuffs):
                opp_debuffs.append(ab)

    # 4d. 自己/自身 进入异常状态（萌化等）
    for m in re.finditer(
        rf'(?:使|令)?(?:自己|自身)(?:获得|进入|陷入)?({ABNORMAL_PAT})', d
    ):
        ab = m.group(1)
        if ab not in self_debuffs:
            self_debuffs.append(ab)

    # 4e. 自己和敌方 获得异常状态 → 双方都记录
    for m in re.finditer(
        rf'自己和敌方(?:获得|进入|陷入)?({ABNORMAL_PAT})', d
    ):
        ab = m.group(1)
        if ab not in self_debuffs:
            self_debuffs.append(ab)
        if ab not in opp_debuffs and not any(ab in e for e in opp_debuffs):
            opp_debuffs.append(ab)

    # ── 5. 去重 ───────────────────────────────────────────────────
    return {
        'self_buffs':   dedup(self_buffs),
        'opp_debuffs':  dedup(opp_debuffs),
        'self_debuffs': dedup(self_debuffs),
        'opp_buffs':    dedup(opp_buffs),
    }


# ─── frontmatter 工具 ─────────────────────────────────────────────

EFFECT_KEYS = ['self_buffs', 'opp_buffs', 'self_debuffs', 'opp_debuffs']


def build_frontmatter(fm_raw: str, effects: dict[str, list[str]]) -> str:
    """
    在现有 frontmatter 字符串末尾添加/替换效果字段。
    去掉旧的 effect 字段（如有），追加新值。
    """
    lines = fm_raw.splitlines()
    clean_lines = [l for l in lines if not any(l.startswith(k + ':') for k in EFFECT_KEYS)]

    for key in EFFECT_KEYS:
        vals = effects.get(key, [])
        if vals:
            items = ', '.join(f'"{v}"' for v in vals)
            clean_lines.append(f'{key}: [{items}]')
        else:
            clean_lines.append(f'{key}: []')

    return '\n'.join(clean_lines)


def write_effects_to_file(path: Path, effects: dict[str, list[str]], dry_run: bool) -> bool:
    """将效果写入技能文件 frontmatter。返回是否有实际变化。"""
    text = path.read_text(encoding='utf-8', errors='ignore')
    if not text.startswith('---'):
        return False

    end = text.find('\n---', 3)
    if end == -1:
        return False

    fm_raw    = text[4:end]
    body_part = text[end:]  # 包含 \n---\n 以后的内容

    new_fm = build_frontmatter(fm_raw, effects)

    old_effect_lines = [l for l in fm_raw.splitlines()  if any(l.startswith(k + ':') for k in EFFECT_KEYS)]
    new_effect_lines = [l for l in new_fm.splitlines()  if any(l.startswith(k + ':') for k in EFFECT_KEYS)]
    if old_effect_lines == new_effect_lines:
        return False

    new_text = f'---\n{new_fm}{body_part}'
    if not dry_run:
        path.write_text(new_text, encoding='utf-8')
    return True


# ─── 主流程 ───────────────────────────────────────────────────────

def main() -> None:
    args = sys.argv[1:]
    dry_run = '--dry-run' in args
    verbose = '--verbose' in args

    skill_dir = BASE / 'wiki' / '技能图鉴'
    if not skill_dir.is_dir():
        print(f'[错误] 技能图鉴目录不存在: {skill_dir}')
        sys.exit(1)

    total = updated = has_any_effect = 0
    no_desc = 0

    for md in sorted(skill_dir.rglob('*.md')):
        if md.name.startswith('_'):
            continue
        text = md.read_text(encoding='utf-8', errors='ignore')
        if not text.startswith('---'):
            continue

        end = text.find('\n---', 3)
        if end == -1:
            continue
        fm_raw = text[4:end]
        fm_data: dict = {}
        for line in fm_raw.splitlines():
            if ':' in line:
                k, _, v = line.partition(':')
                fm_data[k.strip()] = v.strip().strip('"').strip("'")

        desc = fm_data.get('description', '').strip()
        if not desc:
            no_desc += 1
            continue

        total += 1
        effects = extract_effects(desc)

        any_effect = any(effects[k] for k in EFFECT_KEYS)
        if any_effect:
            has_any_effect += 1

        changed = write_effects_to_file(md, effects, dry_run)
        if changed:
            updated += 1
            if verbose and any_effect:
                name = fm_data.get('name', md.stem)
                print(f'  [{name}] {effects}')

    mode = '[dry-run] ' if dry_run else ''
    print(f'{mode}已处理 {total} 个技能')
    print(f'{mode}  -> 识别到效果: {has_any_effect} 个  ({has_any_effect/total*100:.1f}%)')
    print(f'{mode}  -> 写入/更新:  {updated} 个')
    print(f'{mode}  -> 无描述跳过: {no_desc} 个')
    if dry_run:
        print('(dry-run 模式：未实际写入文件)')


if __name__ == '__main__':
    main()
