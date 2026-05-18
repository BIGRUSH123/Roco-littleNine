"""scripts/build/scrape_wiki_skills.py — 从 wiki 爬取精灵技能数据

对于每个进化链，只爬取一次（链中精灵共享技能）。
例外：圣羽翼王、灵息帕尔需要单独爬取。

用法:
  python scripts/build/scrape_wiki_skills.py            # 全部爬取
  python scripts/build/scrape_wiki_skills.py --dry-run  # 仅分析链，不爬取
  python scripts/build/scrape_wiki_skills.py --limit 5  # 仅爬取前5个（测试用）
  python scripts/build/scrape_wiki_skills.py --retry-failed  # 重试之前失败的
"""

import json
import os
import random
import re
import sys
import time
import urllib.parse
from pathlib import Path
from collections import defaultdict

import requests
from bs4 import BeautifulSoup

PROJECT_ROOT = Path(__file__).parent.parent.parent
SPRITES_DIR = PROJECT_ROOT / "data" / "sprites"
FAILED_LOG = PROJECT_ROOT / "data" / "sprites" / "_scrape_failed.json"
WIKI_BASE = "https://wiki.biligame.com/rocom/"

# 例外精灵：进化链共享之外单独爬取
EXCEPTION_SPRITES = {"圣羽翼王", "灵息帕尔"}

# 元素提取：从 alt="图标 宠物 属性 火.png" 中提取 "火"
ELEMENT_RE = re.compile(r'属性[_\s]*(\S+)\.png')

# 请求间隔（秒），wiki 反爬较严格，加大间隔
REQUEST_DELAY = 5.0
REQUEST_JITTER = 3.0  # 额外随机 ±3 秒，总间隔 5~8s

# 重试配置
MAX_RETRIES = 3
RETRY_BACKOFF = 10  # 重试等待倍数（10s, 20s, 40s）


_SKILL_SUFFIXES = ('_精灵技能', '_血脉技能', '_可学技能石')


def _is_sprite_json(filename: str) -> bool:
    """判断是否为精灵 JSON（排除技能文件和索引文件）。"""
    if filename.startswith('_'):
        return False
    stem = Path(filename).stem
    for suffix in _SKILL_SUFFIXES:
        if stem.endswith(suffix):
            return False
    return True


def load_all_sprites() -> list[dict]:
    """加载所有精灵 JSON（排除技能文件和索引文件）。"""
    sprites = []
    for sf in sorted(SPRITES_DIR.glob("*.json")):
        if not _is_sprite_json(sf.name):
            continue
        try:
            data = json.loads(sf.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        name = data.get('name', '').strip()
        if not name:
            continue
        sprites.append({
            'number': str(data.get('number', '')).strip(),
            'name': name,
            'form': data.get('form', '').strip(),
            'pre_species': str(data.get('pre_species', '')).strip(),
            'filename': sf.name,
        })
    return sprites


def build_evolution_chains(sprites: list[dict]) -> dict[str, list[dict]]:
    """构建进化链。

    返回: {root_number: [sprite_dict, ...]}
    每个链以 root（pre_species 为空）为键，包含该链所有精灵。
    standalone 精灵自成一组。
    """
    by_number: dict[str, list[dict]] = defaultdict(list)
    for s in sprites:
        by_number[s['number']].append(s)

    # 找到每个 number 的链根（向上走到 pre_species 尽头）
    num_to_root: dict[str, str] = {}

    def find_root(num: str, visited: frozenset[str] = frozenset()) -> str:
        if num in num_to_root:
            return num_to_root[num]
        if num in visited:
            return num  # 环路，返回自身
        sprites_at_num = by_number.get(num, [])
        if not sprites_at_num:
            return num
        pre = sprites_at_num[0]['pre_species']
        if not pre or pre not in by_number:
            root = num
        else:
            root = find_root(pre, visited | {num})
        num_to_root[num] = root
        return root

    for s in sprites:
        find_root(s['number'])

    # 按根分组
    chains: dict[str, list[dict]] = defaultdict(list)
    seen = set()
    for s in sprites:
        root = num_to_root.get(s['number'], s['number'])
        chains[root].append(s)

    return dict(chains)


def get_chain_scrape_targets(chains: dict[str, list[dict]]) -> list[dict]:
    """确定需要爬取的精灵列表。每个链爬取一次（用链根名称）。

    返回: [{name, targets: [number, ...]}, ...]
    """
    results = []

    for root_num, members in chains.items():
        # 找链根精灵（pre_species=""）
        root_sprite = None
        for m in members:
            if not m['pre_species']:
                root_sprite = m
                break
        if not root_sprite:
            root_sprite = members[0]

        scrape_name = root_sprite['name']
        target_numbers = list(set(m['number'] for m in members))
        # 链中所有不同名称（用于 404 回退）
        alt_names = list(dict.fromkeys(m['name'] for m in members))

        results.append({
            'scrape_name': scrape_name,
            'targets': target_numbers,
            'alt_names': alt_names,
            'chain_root': root_num,
        })

    # 例外：单独爬取
    all_sprites_by_name = {}
    for members in chains.values():
        for m in members:
            all_sprites_by_name[m['name']] = m

    for exc_name in EXCEPTION_SPRITES:
        found = all_sprites_by_name.get(exc_name)
        if found:
            results.append({
                'scrape_name': exc_name,
                'targets': [found['number']],
                'alt_names': [exc_name],
                'chain_root': f'EXCEPTION:{exc_name}',
            })
            print(f"  例外单独爬取: {exc_name}")
        else:
            print(f"  警告: 例外精灵未找到: {exc_name}")

    return results


_SESSION_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    'Referer': 'https://wiki.biligame.com/',
}


def fetch_page(sprite_name: str, session: requests.Session | None = None) -> str | None:
    """获取精灵的 wiki 页面 HTML。每次调用创建新 session 以避免反爬累积。"""
    # 每次请求新建 session（共用 session 会累积反爬标记）
    s = requests.Session()
    s.headers.update(_SESSION_HEADERS)

    encoded = urllib.parse.quote(sprite_name)
    url = WIKI_BASE + encoded

    for attempt in range(MAX_RETRIES):
        try:
            resp = s.get(url, timeout=30)
            if resp.status_code == 200 and len(resp.text) > 5000:
                return resp.text
            if resp.status_code == 567:
                if attempt < MAX_RETRIES - 1:
                    wait = RETRY_BACKOFF * (2 ** attempt)
                    print(f"    反爬限制(567)，等待 {wait}s 后重试...")
                    time.sleep(wait)
                    s = requests.Session()
                    s.headers.update(_SESSION_HEADERS)
                    continue
                else:
                    print(f"    反爬限制(567)，已重试 {MAX_RETRIES} 次，跳过")
                    return None
            if resp.status_code == 404:
                print(f"    404: wiki 页面不存在")
                return None
            print(f"    HTTP {resp.status_code}, body={len(resp.text)} bytes")
            return None
        except requests.RequestException as e:
            if attempt < MAX_RETRIES - 1:
                wait = RETRY_BACKOFF * (2 ** attempt)
                print(f"    请求异常: {e}，等待 {wait}s 后重试...")
                time.sleep(wait)
                s = requests.Session()
                s.headers.update(_SESSION_HEADERS)
                continue
            else:
                print(f"    请求失败，已重试 {MAX_RETRIES} 次: {e}")
                return None

    return None


def parse_skills(html: str, section_title: str) -> list[dict]:
    """从 HTML 中提取指定 tab 的技能列表。使用第一个匹配的 tab。"""
    soup = BeautifulSoup(html, 'html.parser')

    tab = soup.find('div', class_='tabbertab', title=section_title)
    if not tab:
        return []

    skills = []
    boxes = tab.find_all('div', class_='rocom_sprite_skill_box')

    for box in boxes:
        skill = {}

        # Level
        level_el = box.find('div', class_='rocom_sprite_skill_level')
        skill['level'] = level_el.get_text(strip=True) if level_el else ''

        # Element from img alt
        attr_img = box.find('img', class_='rocom_sprite_skill_attr')
        if attr_img:
            alt = attr_img.get('alt', '')
            m = ELEMENT_RE.search(alt)
            skill['element'] = m.group(1) if m else ''
        else:
            skill['element'] = ''

        # Skill name
        name_el = box.find('div', class_='rocom_sprite_skillName')
        skill['name'] = name_el.get_text(strip=True) if name_el else ''

        # Energy cost
        damage_el = box.find('div', class_='rocom_sprite_skillDamage')
        skill['energy'] = damage_el.get_text(strip=True) if damage_el else ''

        # Type (物攻/魔攻/状态)
        type_el = box.find('div', class_='rocom_sprite_skillType')
        skill['type'] = type_el.get_text(strip=True) if type_el else ''

        # Power
        power_el = box.find('div', class_='rocom_sprite_skill_power')
        skill['power'] = power_el.get_text(strip=True) if power_el else ''

        # Description
        desc_el = box.find('div', class_='rocom_sprite_skillContent')
        skill['description'] = desc_el.get_text(strip=True) if desc_el else ''

        skills.append(skill)

    return skills


def fetch_with_fallback(name: str, chain_members: list[str]) -> tuple[str | None, str]:
    """尝试获取 wiki 页面，如果根名称失败则尝试链中其他精灵名。
    返回 (html, used_name)。
    """
    html = fetch_page(name)
    if html:
        return html, name

    for alt_name in chain_members:
        if alt_name == name:
            continue
        print(f"    尝试替代名称: {alt_name}")
        html = fetch_page(alt_name)
        if html:
            return html, alt_name

    return None, name


def save_skills_for_sprite(number: str, skills: list[dict], section_title: str):
    """为所有匹配 number 的精灵文件保存技能数据。"""
    for sf in sorted(SPRITES_DIR.glob(f"{number}_*.json")):
        if not _is_sprite_json(sf.name):
            continue
        base = sf.stem
        out_path = SPRITES_DIR / f"{base}_{section_title}.json"
        out_path.write_text(
            json.dumps(skills, ensure_ascii=False, indent=2),
            encoding='utf-8'
        )
        print(f"    保存: {out_path.name}")


def save_failed(failed: list[dict]) -> None:
    """将失败的爬取任务保存到日志文件，供后续重试。"""
    FAILED_LOG.write_text(
        json.dumps(failed, ensure_ascii=False, indent=2),
        encoding='utf-8'
    )


def load_failed() -> list[dict] | None:
    """加载之前失败的爬取任务。"""
    if not FAILED_LOG.exists():
        return None
    try:
        return json.loads(FAILED_LOG.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return None


def main():
    dry_run = '--dry-run' in sys.argv
    limit = None
    retry_failed = '--retry-failed' in sys.argv

    for i, arg in enumerate(sys.argv):
        if arg == '--limit' and i + 1 < len(sys.argv):
            limit = int(sys.argv[i + 1])
            break

    print("=== 加载精灵数据 ===")
    sprites = load_all_sprites()
    print(f"共 {len(sprites)} 个精灵 JSON")

    print("\n=== 构建进化链 ===")
    chains = build_evolution_chains(sprites)
    print(f"共 {len(chains)} 个链")

    # 显示链信息
    multi_chains = 0
    for root, members in sorted(chains.items()):
        names = [m['name'] for m in members]
        if len(names) > 1:
            multi_chains += 1
    print(f"其中 {multi_chains} 个链包含多个精灵")

    print("\n=== 确定爬取目标 ===")

    if retry_failed:
        failed_data = load_failed()
        if failed_data:
            targets = failed_data
            print(f"从失败日志加载 {len(targets)} 个重试任务")
        else:
            print("没有失败日志，将正常分析")
            targets = get_chain_scrape_targets(chains)
    else:
        targets = get_chain_scrape_targets(chains)

    targets.sort(key=lambda t: t['scrape_name'])
    print(f"需爬取 {len(targets)} 个 wiki 页面")

    if limit:
        targets = targets[:limit]
        print(f"  (限制模式: 仅前 {limit} 个)")

    if dry_run:
        print("\n=== 预览爬取目标 ===")
        for t in targets:
            print(f"  {t['scrape_name']} → 编号 {t['targets']}")
        print(f"\n总计 {len(targets)} 个页面，跳过实际爬取。")
        return

    # 统计
    success_count = 0
    fail_count = 0
    total_skills = 0
    failed_tasks: list[dict] = []

    print(f"\n=== 开始爬取 (间隔 {REQUEST_DELAY}s ± {REQUEST_JITTER}s) ===")

    for i, t in enumerate(targets):
        name = t['scrape_name']
        target_nums = t['targets']
        print(f"\n[{i+1}/{len(targets)}] {name} → 目标编号: {target_nums}")

        html, used_name = fetch_with_fallback(name, t.get('alt_names', [name]))
        if html and used_name != name:
            print(f"    使用 {used_name} 的 wiki 页面")
        if not html:
            fail_count += 1
            failed_tasks.append(t)
            continue

        sections = [
            ('精灵技能', '精灵技能'),
            ('血脉技能', '血脉技能'),
            ('可学技能石', '可学技能石'),
        ]

        any_found = False
        for section_title, file_label in sections:
            skills = parse_skills(html, section_title)
            if not skills:
                print(f"  {section_title}: 未找到")
                continue

            print(f"  {section_title}: {len(skills)} 个技能")
            any_found = True
            total_skills += len(skills)

            # 保存到该链所有精灵
            for num in target_nums:
                save_skills_for_sprite(num, skills, section_title)

        if any_found:
            success_count += 1
        else:
            fail_count += 1
            failed_tasks.append(t)
            print(f"  警告: 未找到任何技能数据")

        # 保存失败日志（增量）
        if failed_tasks:
            save_failed(failed_tasks)

        # 随机延迟
        delay = REQUEST_DELAY + random.uniform(0, REQUEST_JITTER)
        time.sleep(delay)

    print(f"\n=== 完成 ===")
    print(f"成功: {success_count}/{len(targets)}, 失败: {fail_count}")
    print(f"总技能条目: {total_skills}")
    if failed_tasks:
        print(f"失败任务已保存到: {FAILED_LOG}")
        print(f"使用 --retry-failed 重试失败任务")


if __name__ == '__main__':
    main()
