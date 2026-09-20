"""dbg_choice_sem — 用 RandomState 固定种子确定 np.random.choice 的精确公式。

对随机 P 向量批量测试候选实现，找出与 np.random.choice 逐例一致的公式。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
_logf = open(ROOT / "native" / "tools" / "_dbg_choice_sem_log.txt", "w", encoding="utf-8")


class _Tee:
    def __init__(self, *streams):
        self._s = streams

    def write(self, s):
        for x in self._s:
            x.write(s)

    def flush(self):
        for x in self._s:
            x.flush()


sys.stdout = _Tee(sys.stdout, _logf)

import numpy as np  # noqa: E402

N = 17


def pairwise_sum_f32(a):
    """numpy .sum() 的成对求和镜像（f32）。
    numpy 对 reduce 使用 8 路部分和（n>=8 时），这里镜像其结构。"""
    n = len(a)
    if n < 8:
        acc = np.float32(0.0)
        for x in a:
            acc = np.float32(acc + x)
        return float(acc)
    r = [np.float32(0.0)] * 8
    for i in range(0, n - (n % 8), 8):
        for j in range(8):
            r[j] = np.float32(r[j] + a[i + j])
    for i in range(n - (n % 8), n):
        r[0] = np.float32(r[0] + a[i])
    res = np.float32(0.0)
    for x in r:
        res = np.float32(res + x)
    return float(res)


def f_old_rust(p, u):
    """旧 rust：cutoff = u * np_sum_f32；f64 顺序累加；cutoff < cumulative；跳过 w<=0。"""
    total = pairwise_sum_f32(p)
    if total <= 0:
        return -1
    cutoff = u * total
    cumulative = 0.0
    last_valid = -1
    for i, x in enumerate(p):
        w = float(x)
        if w <= 0.0:
            continue
        last_valid = i
        cumulative += w
        if cutoff < cumulative:
            return i
    return last_valid


def f_new_rust(p, u):
    """新 rust：s=成对f32 → p/=s(f32) → f64 顺序 cumsum → cdf[i]/total <= u 计数。"""
    s = pairwise_sum_f32(p)
    if s <= 0:
        return -1
    q = [np.float32(x / np.float32(s)) for x in p]
    cdf = []
    acc = 0.0
    for x in q:
        acc += float(x)
        cdf.append(acc)
    total = cdf[-1]
    idx = 0
    for i in range(N):
        if cdf[i] / total <= u:
            idx = i + 1
        else:
            break
    return min(idx, N - 1)


def f_prediv_free(p, u):
    """变体：不预除，f64 widen 后顺序 cumsum → /cdf[-1] → searchsorted right。"""
    cdf = []
    acc = 0.0
    for x in p:
        acc += float(x)
        cdf.append(acc)
    total = cdf[-1]
    idx = 0
    for i in range(N):
        if cdf[i] / total <= u:
            idx = i + 1
        else:
            break
    return min(idx, N - 1)


def main() -> None:
    rng = np.random.RandomState(20260918)
    trials = 20000
    mism = {"old": 0, "new": 0, "prediv": 0}
    examples = {}
    for t in range(trials):
        # 构造与真实场景相似的 P：少数支撑点、f32
        k = rng.randint(3, 10)
        support = rng.choice(N, size=k, replace=False)
        p = np.zeros(N, dtype=np.float32)
        raw = rng.random_sample(k).astype(np.float32)
        raw = raw / raw.sum().astype(np.float32)
        for j, s in enumerate(support):
            p[s] = raw[j]
        p = (p / p.sum().astype(np.float32)).astype(np.float32)

        rs1 = np.random.RandomState(t)
        u = float(rs1.random_sample())  # choice 将消耗的第一个 double
        rs2 = np.random.RandomState(t)
        truth = int(rs2.choice(N, p=p))

        for name, fn in (("old", f_old_rust), ("new", f_new_rust), ("prediv", f_prediv_free)):
            got = fn(p, u)
            if got != truth:
                mism[name] += 1
                if name not in examples:
                    examples[name] = (t, truth, got, u, p.copy())

    print(f"trials={trials}  mismatch: old={mism['old']} new={mism['new']} "
          f"prediv={mism['prediv']}", flush=True)
    for name, (t, truth, got, u, p) in examples.items():
        print(f"[{name}] 首个不一致 trial={t} truth={truth} got={got} u={u!r}", flush=True)
        nz = np.nonzero(p)[0]
        print(f"    支撑 {[(int(i), float(p[i])) for i in nz]}", flush=True)
        # 打印三种 cumsum 的边界值
        cdf_new = []
        acc = 0.0
        s = pairwise_sum_f32(p)
        q = [np.float32(x / np.float32(s)) for x in p]
        for x in q:
            acc += float(x)
            cdf_new.append(acc)
        tot = cdf_new[-1]
        near = [(i, cdf_new[i] / tot, float(np.float32(cdf_new[i] / np.float32(tot))))
                for i in range(max(0, min(truth, got) - 2), min(N, max(truth, got) + 3))]
        for i, v64, _v in near:
            print(f"    cdf[{i}]/tot = {v64!r}", flush=True)


if __name__ == "__main__":
    main()
