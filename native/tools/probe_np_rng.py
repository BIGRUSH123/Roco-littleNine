"""probe_np_rng.py — 采集 numpy legacy RandomState 基准序列，供 Rust 镜像校准。

Rust 侧 native/roco-core/src/np_random.rs 必须逐位复现这些值，
MCTS 的 Dirichlet 根噪声与策略采样才能与 Python 完全一致。
"""

import numpy as np
import random

print("numpy", np.__version__)

NP_SEED = 12345

np.random.seed(NP_SEED)
print("random5", [repr(float(x)) for x in np.random.random(5)])

np.random.seed(NP_SEED)
print("raw32", np.random.randint(0, 2**32, size=6, dtype=np.uint32).tolist())

np.random.seed(NP_SEED)
print("std_exp5", [repr(float(x)) for x in np.random.standard_exponential(5)])

np.random.seed(NP_SEED)
print("std_gamma03_5", [repr(float(x)) for x in np.random.standard_gamma(0.3, 5)])

np.random.seed(NP_SEED)
print("std_gamma15_5", [repr(float(x)) for x in np.random.standard_gamma(1.5, 5)])

np.random.seed(NP_SEED)
print("dirichlet4", [repr(float(x)) for x in np.random.dirichlet([0.3] * 4)])

np.random.seed(NP_SEED)
print("dirichlet17", [repr(float(x)) for x in np.random.dirichlet([0.3] * 17)])

np.random.seed(NP_SEED)
print("randint10", np.random.randint(0, 10, size=5).tolist())

np.random.seed(NP_SEED)
st = np.random.get_state()
print("state_head", st[1][:8].tolist(), "pos", st[2])

random.seed(NP_SEED)
print("pyrandom3", [repr(random.random()) for _ in range(3)])
