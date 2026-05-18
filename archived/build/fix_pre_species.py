"""修复 pre_species 字段 — 限制链长≤3，字符重叠为主，手动覆盖特例"""
import json, os
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SPRITES_DIR = os.path.join(ROOT, 'data', 'sprites')

# 加载
sprites = []
for f in sorted(os.listdir(SPRITES_DIR)):
    if f.endswith('.json'):
        path = os.path.join(SPRITES_DIR, f)
        with open(path, encoding='utf-8') as fp:
            sprites.append(json.load(fp))

by_number = defaultdict(list)
for s in sprites:
    by_number[s['number']].append(s)
sorted_nums = sorted(by_number.keys(), key=int)

def all_chars(num):
    chars = set()
    for s in by_number[num]:
        chars.update(s['name'])
    return chars

def name_overlap(a, b):
    return len(all_chars(a) & all_chars(b))

# ═══ 手动覆盖表：强制指定哪些编号必须断开（新链起点）═══
# 这些是算法无法正确判断的边界
FORCE_BREAK: set[str] = {
    # 石肤蜥链(24-26) 与 布是石链(27-29) 是不同链
    '27',
    # 小怂猫链(163-164) 与 小狮鹫链(165-167) 是不同链
    '165',
    # 电动长颈鹿链(226-228) 与 缇塔链(229-230) 是不同链
    '229',
    # 厉毒小萝链(291-292) 与 小帕尔链(293-295) 是不同链
    '293',
    # 豆丁鱼链(239-241) 与 胆小鳗鱼链(242-243) 是不同链
    '242',
    # 机械方方链(263-265) 与 可立鸡链(266-269)
    '266',
}

# 手动强制合并：编号必须属于前一链（算法断开但实际应连）
FORCE_MERGE: dict[str, str] = {
    # 喵喵链: 002→003→004
    '4': '3',
    # 水蓝蓝链: 008→009→010
    '9': '8',
    '10': '9',
    # 雪绒鸟链: 018→019→020
    '19': '18',
    '20': '19',
    # 毛毛链: 032→033→034
    '33': '32',
    '34': '33',
    # 丢丢链: 044→045→046
    '45': '44',
    '46': '45',
    # 小灵面链: 054→055→056
    '55': '54',
    '56': '55',
    # 白发懒人链: 073→074→075
    '74': '73',
    '75': '74',
    # 乖乖鹄(88)→蓝珠天鹅(89), 翠顶夫人(90)→黑羽夫人(91) share 夫人, 两段分开
    '89': '88',
    # 阿米亚特(105)→阿米樱(106)→罗隐(107)
    '106': '105',
    '107': '106',
}

# ═══ 算法部分 ═══
# Step 1: 字符重叠检测链接
links = {}  # num -> True if linked to num-1
for i in range(1, len(sorted_nums)):
    curr = sorted_nums[i]
    prev = sorted_nums[i-1]

    # 手动覆盖优先
    if curr in FORCE_BREAK:
        links[curr] = False
        continue
    if curr in FORCE_MERGE:
        links[curr] = True
        continue

    # 字符重叠检测
    if name_overlap(curr, prev) > 0:
        links[curr] = True
        continue

    # 间接桥接：curr 与 prev-1 重叠，且 prev 与 prev-1 重叠
    if i > 1:
        prev2 = sorted_nums[i-2]
        if name_overlap(prev, prev2) > 0 and name_overlap(curr, prev2) > 0:
            links[curr] = True
            continue

    links[curr] = False

# Step 2: 推导链（强制限制长度 ≤ 3，除非手动允许）
chains = []
current_chain = [sorted_nums[0]]
for i in range(1, len(sorted_nums)):
    num = sorted_nums[i]
    linked = links.get(num, False)

    if linked and len(current_chain) < 3:
        current_chain.append(num)
    else:
        chains.append(current_chain)
        current_chain = [num]
chains.append(current_chain)

# Step 3: 统计
print('=== 进化链检测结果 ===')
issues = []
for chain in chains:
    names_list = []
    for num in chain:
        entries = by_number[num]
        name_set = set()
        for s in entries:
            n = s['name']
            if s['form']:
                n += f'({s["form"]})'
            name_set.add(n)
        names_list.append('|'.join(sorted(name_set)[:3]))

    chain_str = ' → '.join(names_list)
    gaps = []
    for j in range(1, len(chain)):
        if name_overlap(chain[j], chain[j-1]) == 0:
            gaps.append(f'{chain[j-1]}→{chain[j]}')
    gap_str = f' [断连: {", ".join(gaps)}]' if gaps else ''
    label = f'[{chain[0]}-{chain[-1]}]' if len(chain) > 1 else f'[{chain[0]}]'
    print(f'  {label} L={len(chain)} {chain_str}{gap_str}')

print(f'\n共 {len(chains)} 条进化链')

# Step 4: 写入 pre_species
fixed = 0
for chain in chains:
    for j in range(len(chain)):
        num = chain[j]
        pre = chain[j-1] if j > 0 else ''
        for s in by_number[num]:
            if s['pre_species'] != pre:
                s['pre_species'] = pre
                fixed += 1

for s in sprites:
    filename = f'{s["number"]}_{s["name"]}'
    if s['form']:
        filename += f'（{s["form"]}）'
    filename += '.json'
    path = os.path.join(SPRITES_DIR, filename)
    with open(path, 'w', encoding='utf-8') as fp:
        json.dump(s, fp, ensure_ascii=False, indent=2)

print(f'修复了 {fixed} 个 pre_species 字段')

# Step 5: 验证
print('\n=== 验证: 链概况 ===')
for chain in chains:
    names = []
    for num in chain:
        first = by_number[num][0]
        n = first['name']
        names.append(f'#{num} {n}')
    print(f'  {" → ".join(names)}')
