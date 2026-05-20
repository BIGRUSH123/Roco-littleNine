#!/usr/bin/env python3
"""
Roco Wiki Scraper - 精灵图鉴 & 技能图鉴 爬虫

Scrapes https://wiki.biligame.com/rocom/ to build:
  精灵图鉴/ - one .md per pet matching template.md
  技能图鉴/{element}/ - one .md per skill matching _template.md

Strategy:
  1. Scrape main 精灵图鉴 page to get pet list (names, numbers, elements)
  2. For each pet, scrape its detail page for race stats + skill data
  3. Skills are embedded in pet pages with full data (power, energy, etc.)
  4. No need for separate skill detail page requests

Usage:
  python scraper.py [--skip-pets] [--skip-skills] [--skip-existing] [--max-pets N] [--resume-from N]
"""

import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

WIKI_BASE = "https://wiki.biligame.com/rocom"
API_URL = f"{WIKI_BASE}/api.php"

WIKI_DIR = r"D:\projects\Roco-小八\wiki"
PET_DIR = os.path.join(WIKI_DIR, "精灵图鉴")
SKILL_DIR = os.path.join(WIKI_DIR, "技能图鉴")

DELAY = 1.0  # seconds between API calls
MAX_RETRIES = 5
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'


# ── network helpers ──────────────────────────────────────

def fetch_json(params, retries=MAX_RETRIES):
    """Fetch JSON from MediaWiki API with retry logic."""
    params['format'] = 'json'
    query = urllib.parse.urlencode(params)
    url = f"{API_URL}?{query}"
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={
                'User-Agent': USER_AGENT,
                'Accept': 'application/json',
            })
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode('utf-8'))
        except Exception as e:
            if attempt < retries - 1:
                wait = 3 * (attempt + 1)
                print(f"\n    Retry {attempt+1}/{retries-1} in {wait}s...", end='', flush=True)
                time.sleep(wait)
            else:
                print(f"\n    ERROR (after {retries} retries): {e}")
                return None


def fetch_page_html(title):
    """Fetch parsed HTML of a wiki page."""
    data = fetch_json({'action': 'parse', 'page': title, 'prop': 'text'})
    if data and 'parse' in data and 'text' in data['parse']:
        return data['parse']['text']['*']
    return None


# ── pet extraction from main page ────────────────────────

def extract_pets_from_main():
    """Extract all pets from the main 精灵图鉴 page."""
    print("=" * 60)
    print("Step 1: Fetching 精灵图鉴 main page...")
    print("=" * 60)

    html = fetch_page_html('精灵图鉴')
    if not html:
        print("ERROR: Could not fetch main page!")
        return []

    pets, seen = [], set()

    # Find each divsort pet card - match the full card content
    divsort_pattern = re.compile(
        r'<div class="divsort"[^>]*data-param1="([^"]*)"\s+data-param2="([^"]*)"[^>]*>'
        r'(.*?)</div>\s*</div>\s*</div>',
        re.DOTALL
    )

    for m in divsort_pattern.finditer(html):
        form = m.group(1)
        element = m.group(2)
        content = m.group(3)

        link_m = re.search(r'<a href="(/rocom/[^"]+)" title="([^"]+)">', content)
        if not link_m:
            continue
        url = link_m.group(1)
        page_title = link_m.group(2)

        no_m = re.search(r'NO\.(\d+)', content)
        if not no_m:
            continue
        no = int(no_m.group(1))

        img_m = re.search(r'alt="页面 宠物 立绘 ([^"]+) 1\.png"', content)
        name = img_m.group(1) if img_m else page_title

        key = (no, name)
        if key not in seen:
            seen.add(key)
            pets.append({
                'no': no,
                'name': name,
                'element': element.replace(',', '、'),
                'form': form,
                'url': url,
                'page_title': page_title,
            })

    pets.sort(key=lambda p: p['no'])
    print(f"  Found {len(pets)} unique pets")

    single_elems = {}
    for p in pets:
        for e in re.split(r'[,、\s]+', p['element']):
            e = e.strip()
            if e:
                single_elems[e] = single_elems.get(e, 0) + 1
    print(f"  Elements: {', '.join(f'{e}({c})' for e, c in sorted(single_elems.items(), key=lambda x: -x[1]))}")

    return pets


# ── pet detail extraction ────────────────────────────────

def extract_race_stats(html):
    """Extract race stats (种族值) from pet detail page."""
    stats = {}

    for li_match in re.finditer(
        r'<li>\s*<div[^>]*>.*?'
        r'<p class="rocom_sprite_info_qualification_name">(\w+)</p>'
        r'.*?<p class="rocom_sprite_info_qualification_value">(\d+)</p>',
        html, re.DOTALL
    ):
        name = li_match.group(1)
        value = int(li_match.group(2))
        stats[name] = value

    total = 0
    total_m = re.search(
        r'alt="[^"]*种族[^"]*\.png"[^>]*/>\s*种族值\s*</p>\s*<p>(\d+)</p>',
        html
    )
    if total_m:
        total = int(total_m.group(1))
    elif stats:
        total = sum(stats.values())

    return stats, total


def extract_skills_from_pet(html):
    """Extract skill list from pet detail page."""
    skills = []
    seen_names = set()

    skill_boxes = re.finditer(
        r'<div class="rocom_sprite_skill_box">(.*?)(?=<div class="rocom_sprite_skill_box">|$)',
        html, re.DOTALL
    )

    for sb in skill_boxes:
        box = sb.group(1)

        level_m = re.search(r'rocom_sprite_skill_level[^>]*>\s*(.*?)\s*</div>', box)
        level = re.sub(r'&#160;|&nbsp;|\s', '', level_m.group(1)) if level_m else ''

        elem_m = re.search(r'alt="图标 宠物 属性 (\S+)\.png"', box)
        element = elem_m.group(1) if elem_m else ''

        name_m = re.search(r'rocom_sprite_skillName[^>]*>\s*(\S[^<]*)', box)
        name = name_m.group(1).strip() if name_m else ''
        if not name or name in seen_names:
            continue
        seen_names.add(name)

        type_m = re.search(r'rocom_sprite_skillType[^>]*>(.*?)</div>', box, re.DOTALL)
        skill_type = ''
        if type_m:
            # Strip HTML tags and whitespace
            raw = re.sub(r'<[^>]+>', '', type_m.group(1))
            skill_type = raw.strip()

        power_m = re.search(r'rocom_sprite_skill_power[^>]*>\s*([^<\s]*)', box)
        power = power_m.group(1).strip() if power_m else ''
        if not power:
            power = '-'

        energy_m = re.search(r'rocom_sprite_skillDamage[^>]*>(?:.*?<[^>]*>)*?(\d+)', box)
        energy = energy_m.group(1).strip() if energy_m else '-'

        desc_m = re.search(r'rocom_sprite_skillContent[^>]*>\s*([^<]*)', box)
        description = desc_m.group(1).replace('✦', '').strip() if desc_m else ''

        skills.append({
            'name': name,
            'level': level,
            'element': element,
            'type': skill_type,
            'power': power,
            'energy': energy,
            'description': description,
        })

    return skills


def extract_abilities(html):
    """Extract abilities (特性) from pet detail page.

    Each ability lives inside a rocom_sprite_info_characteristic_content div
    with a characteristic_title <p> and a rocom_sprite_info_characteristic_text <p>.
    We match the triple as one regex so we don't depend on counting </div> tags,
    which varies between pages (temp vs main section, attribute boxes, etc.).
    """
    abilities = []
    seen = set()

    for m in re.finditer(
        r'rocom_sprite_info_characteristic_content[^>]*>.*?'
        r'characteristic_title[^>]*>\s*(.*?)\s*</p>'
        r'.*?'
        r'rocom_sprite_info_characteristic_text[^>]*>\s*(.*?)\s*</p>',
        html, re.DOTALL
    ):
        name = m.group(1).strip()
        desc = m.group(2).strip()
        if name and name not in seen:
            seen.add(name)
            abilities.append({'name': name, 'description': desc})

    return abilities


# ── save functions ───────────────────────────────────────

def safe_filename(name):
    """Make a safe filename from a name."""
    unsafe = r'[<>:"/\\|?*]'
    return re.sub(unsafe, '_', name)


def pet_primary_element(pet):
    """Get the primary (first) element from a pet's element string."""
    elements = [e.strip() for e in re.split(r'[、,]+', pet['element']) if e.strip()]
    return elements[0] if elements else '未知'


def save_pet_page(pet, stats, total, abilities, skills):
    """Save pet info matching template.md format."""
    primary_elem = safe_filename(pet_primary_element(pet))
    elem_dir = os.path.join(PET_DIR, primary_elem)
    os.makedirs(elem_dir, exist_ok=True)

    lines = [
        f"# {pet['name']}",
        "",
        f"- **编号**：`{pet['no']:03d}`",
        f"- **属性**：`{pet['element']}`",
        "",
    ]

    # Race stats
    if stats and total > 0:
        stat_names = ['生命', '物攻', '魔攻', '物防', '魔防', '速度']
        lines.append(f"## 种族值：{total}")
        lines.append("| **属性** | **数值** |")
        lines.append("| -------- | -------- |")
        for sn in stat_names:
            val = stats.get(sn, '-')
            lines.append(f"| **{sn}** | **{val}**  |")
        lines.append("")

    # Abilities
    lines.append("## 特性")
    lines.append("")
    if abilities:
        for a in abilities:
            lines.append(f"**{a['name']}**：{a['description']}")
    else:
        lines.append("无")
    lines.append("")

    # Skills with wikilinks (go up 2 levels from element subfolder)
    lines.append("## 技能")
    lines.append("")
    if skills:
        for s in skills:
            elem = s['element'] if s['element'] else '未知'
            link = f"../../技能图鉴/{safe_filename(elem)}/{safe_filename(s['name'])}"
            lines.append(f"- [**{s['name']}**]({link})")
    else:
        lines.append("无")
    lines.append("")

    filepath = os.path.join(elem_dir, f"{pet['no']:03d}_{safe_filename(pet['name'])}.md")
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    return filepath


def save_skill_page(skill_name, skill_data):
    """Save skill matching _template.md format."""
    element = skill_data.get('element', '未知')
    elem_dir = os.path.join(SKILL_DIR, safe_filename(element))
    os.makedirs(elem_dir, exist_ok=True)

    # Learners with proper relative links (3 levels up from skill/{elem}/)
    unique_pets = sorted(set(skill_data.get('source_pets', [])))
    learner_links = []
    for no, name, primary_elem in unique_pets:
        link = f"../../../精灵图鉴/{safe_filename(primary_elem)}/{no:03d}_{safe_filename(name)}"
        learner_links.append(f"[**{name}**]({link})")

    # Detect 应对 from description (应对攻击/应对防御/应对状态)
    counter = '无'
    desc = skill_data.get('description', '')
    counter_match = re.search(r'应对(攻击|防御|状态)', desc)
    if counter_match:
        counter = counter_match.group(1)

    lines = [
        f"# **{skill_name}**",
        "",
        f"**属性：{element}**",
        "",
        f"**类型：{skill_data.get('type', '未知')}**",
        "",
        f"**威力：{skill_data.get('power', '-')}**",
        "",
        f"**耗能：{skill_data.get('energy', '-')}**",
        "",
        f"**应对：{counter}**",
        "",
        f"**描述：**`{desc}`",
        "",
    ]

    if learner_links:
        lines.append("## 可学习精灵")
        lines.append("")
        lines.append('、'.join(learner_links))
        lines.append("")

    filepath = os.path.join(elem_dir, f"{safe_filename(skill_name)}.md")
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    return filepath


# ── main pipeline ────────────────────────────────────────

def pet_file_exists(pet):
    """Check if the .md file for a pet already exists."""
    primary_elem = safe_filename(pet_primary_element(pet))
    filepath = os.path.join(PET_DIR, primary_elem,
                            f"{pet['no']:03d}_{safe_filename(pet['name'])}.md")
    return os.path.isfile(filepath)


def main():
    skip_pets = '--skip-pets' in sys.argv
    skip_skills = '--skip-skills' in sys.argv
    skip_existing = '--skip-existing' in sys.argv
    max_pets = None
    resume_from = 0

    for a in sys.argv:
        if a.startswith('--max-pets='):
            max_pets = int(a.split('=')[1])
        if a.startswith('--resume-from='):
            resume_from = int(a.split('=')[1])

    if skip_pets and skip_skills:
        print("Nothing to do")
        return

    # Step 1: Get pet list
    pets = []
    if not skip_pets:
        pets = extract_pets_from_main()
        if not pets:
            print("ERROR: No pets found!")
            return

    if max_pets:
        pets = pets[:max_pets]
        print(f"  (limited to {max_pets} pets)")

    if resume_from > 0:
        pets = [p for p in pets if p['no'] >= resume_from]
        print(f"  (resuming from NO.{resume_from:03d}, {len(pets)} pets remaining)")

    if skip_existing:
        existing_count = sum(1 for p in pets if pet_file_exists(p))
        pets = [p for p in pets if not pet_file_exists(p)]
        print(f"  (skipping {existing_count} existing pets, {len(pets)} new to scrape)")

    # Step 2: Scrape each pet detail page
    all_skills = {}  # {skill_name: {data, source_pets: []}}
    total = len(pets)
    pet_count = 0
    failed_pets = []

    if pets:
        print("\n" + "=" * 60)
        print("Step 2: Scraping pet detail pages...")
        print("=" * 60)

        start_time = time.time()

        for i, pet in enumerate(pets):
            elapsed = time.time() - start_time
            eta = (elapsed / (i + 1)) * (total - i - 1) if i > 0 else 0
            eta_str = f" | ETA: {eta/60:.0f}m{eta%60:.0f}s" if i > 0 else ""

            print(f"  [{i+1}/{total}] NO.{pet['no']:03d} {pet['name']}...",
                  end='', flush=True)

            html = fetch_page_html(pet['page_title'])
            if not html:
                print(f" SKIP (fetch failed){eta_str}")
                failed_pets.append(pet['name'])
                continue

            stats, total_stat = extract_race_stats(html)
            abilities = extract_abilities(html)
            skills = extract_skills_from_pet(html)

            save_pet_page(pet, stats, total_stat, abilities, skills)
            pet_count += 1

            # Merge into global skill collection
            for s in skills:
                name = s['name']
                if name not in all_skills:
                    all_skills[name] = {
                        'element': s['element'],
                        'type': s['type'],
                        'power': s['power'],
                        'energy': s['energy'],
                        'description': s['description'],
                        'source_pets': [],
                    }
                # Fill in missing data with non-empty values
                existing = all_skills[name]
                for field in ['element', 'type', 'power', 'energy', 'description']:
                    if (not existing[field] or existing[field] == '-') and s[field] and s[field] != '-':
                        existing[field] = s[field]
                if not any(p[1] == pet['name'] for p in existing['source_pets']):
                    existing['source_pets'].append(
                        (pet['no'], pet['name'], pet_primary_element(pet)))

            print(f" OK ({len(skills)} skills, total: {total_stat}){eta_str}")

            if i < total - 1:
                time.sleep(DELAY)

        if failed_pets:
            print(f"\n  Failed: {len(failed_pets)} pets: {', '.join(failed_pets[:10])}{'...' if len(failed_pets) > 10 else ''}")

        print(f"\n  Pet pages saved: {pet_count}")
        print(f"  Unique skills collected: {len(all_skills)}")

    # Step 3: Save skill pages
    if not skip_skills and all_skills:
        print("\n" + "=" * 60)
        print("Step 3: Saving skills to 技能图鉴/{element}/...")
        print("=" * 60)

        by_element = {}
        for sname, sdata in all_skills.items():
            elem = sdata.get('element', '未知')
            by_element.setdefault(elem, []).append((sname, sdata))

        for element, skill_list in sorted(by_element.items()):
            print(f"  [{element}] {len(skill_list)} skills")
            for sname, sdata in skill_list:
                save_skill_page(sname, sdata)

        print(f"\n  Total: {len(all_skills)} skills across {len(by_element)} elements")

    # Done
    print("\n" + "=" * 60)
    print("DONE!")
    print(f"  Pets saved: {pet_count}")
    if failed_pets:
        print(f"  Failed pets: {len(failed_pets)}")
    print(f"  Skills saved: {len(all_skills)}")
    print("=" * 60)

    if failed_pets:
        print("\nTo retry failed pets, run:")
        failed_nums = sorted([p['no'] for p in pets if p['name'] in failed_pets])
        print(f"  python scraper.py --skip-skills --resume-from={failed_nums[0]}" if failed_nums else "")


if __name__ == '__main__':
    main()
