# -*- coding: utf-8 -*-
"""汇总逐阵容 A/B 的分片结果（读 JSON，不做对局）：
- 总体胜率（决出局加权）+ 95% CI
- 逐阵容胜率表（按胜率排序）
- 平局合计
- 若给了 --base-dir，则与另一批同名分片逐阵容对比（Δ 胜率、Δ 平局）
"""
import argparse
import glob
import json
import math
from pathlib import Path


def load(pattern: str) -> dict:
    rows = {}
    for p in sorted(glob.glob(pattern)):
        for team, v in json.loads(Path(p).read_text(encoding='utf-8')).items():
            if 'error' in v:
                continue
            rows[team] = v
    return rows


def ci(w: int, l: int) -> tuple[float, float, float]:
    dec = w + l
    if not dec:
        return float('nan'), float('nan'), 0.0
    wr = w / dec
    half = 1.96 * math.sqrt(max(1e-9, wr * (1 - wr)) / dec)
    return wr, half, dec


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--dir', default='.zcode/tmp/dsw/ab2')
    ap.add_argument('--mode', default='antistall')
    ap.add_argument('--base-dir', default='', help='对比批（同模式名）')
    ap.add_argument('--min-dec', type=int, default=20)
    a = ap.parse_args()

    rows = load(f'{a.dir}/{a.mode}_s*.json')
    tot_w = sum(v['new_wins'] for v in rows.values())
    tot_l = sum(v['new_losses'] for v in rows.values())
    tot_d = sum(v['new_draws'] for v in rows.values())
    wr, half, dec = ci(tot_w, tot_l)
    print(f'=== {a.mode}：{len(rows)} 支队伍，决出 {dec} 局（平 {tot_d}） ===')
    print(f'  总体新口径胜率 {wr:.3f} [{wr - half:.3f},{wr + half:.3f}]  '
          f'（{tot_w} 胜 / {tot_l} 负）  平局占比 {tot_d / max(1, dec + tot_d):.3f}')

    base = load(f'{a.base_dir}/{a.mode}_s*.json') if a.base_dir else {}
    print(f'\n{"队伍":18s} {"新口径":>6s} {"平":>4s} {"旧口径":>6s} {"Δ":>7s}')
    table = []
    for team, v in rows.items():
        w, l, d = v['new_wins'], v['new_losses'], v['new_draws']
        if w + l < a.min_dec:
            table.append((None, team, v, base.get(team)))
            continue
        t_wr = w / (w + l)
        table.append((t_wr, team, v, base.get(team)))
    for wr_t, team, v, b in sorted(table, key=lambda x: (x[0] is None, x[0] or 0)):
        w, l, d = v['new_wins'], v['new_losses'], v['new_draws']
        line = f'{team[:16]:18s} {w / max(1, w + l):6.3f} {d:4d}'
        if b:
            bw, bl, bd = b['new_wins'], b['new_losses'], b['new_draws']
            b_wr = bw / max(1, bw + bl)
            line += f' {b_wr:6.3f} {w / max(1, w + l) - b_wr:+7.3f}'
        print(line)


if __name__ == '__main__':
    main()
