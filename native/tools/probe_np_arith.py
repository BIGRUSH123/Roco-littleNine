"""probe_np_arith.py — 确认 numpy 2.x (NEP 50) 下 MCTS 关键表达式的 dtype/舍入语义。

PUCT 打分、根噪声混合、float32 求和三条路径在 numpy 2 里的标量提升规则
（Python float 是弱标量）决定了 Rust 侧要用 f32 还是 f64 运算才能逐位一致。
"""

import math

import numpy as np

print("numpy", np.__version__)

prior = np.array([0.1, 0.25, 0.3, 0.35], dtype=np.float32)
c_puct = 2.0
sqrt_n = math.sqrt(6)

u = c_puct * prior[1] * sqrt_n / (1 + 3)
print("u_type", type(u).__name__, "u", repr(float(u)))

u_f64 = c_puct * float(prior[1]) * sqrt_n / (1 + 3)
print("u_f64", repr(u_f64))

u_f32 = np.float32(np.float32(np.float32(c_puct) * prior[1]) * np.float32(sqrt_n)) / np.float32(4)
print("u_f32", repr(float(u_f32)))

q = 0.12345678901234567
s = q + u
print("q_plus_u_type", type(s).__name__, "s", repr(float(s)))
print("q_plus_u_f64", repr(q + float(u)))
print("q_plus_u_f32", repr(float(np.float32(q) + u_f32)))

best = -1e9
print("compare_branch", (s > best))

noise = np.random.dirichlet([0.3] * 4)
p = prior.copy()
p[1] = (1 - 0.25) * p[1] + 0.25 * noise[1]
print("noise_mix_stored", repr(float(p[1])))

inner32 = np.float32(0.75) * prior[1] + np.float32(0.25 * noise[1])
print("noise_mix_f32", repr(float(inner32)))

inner64 = (1 - 0.25) * float(prior[1]) + 0.25 * float(noise[1])
print("noise_mix_f64", repr(inner64))

probs = np.array([0.01 * i for i in range(17)], dtype=np.float32)
print("sum32", repr(float(probs.sum())))
acc32 = np.float32(0.0)
for v in probs:
    acc32 = np.float32(acc32 + v)
print("sum_seq32", repr(float(acc32)))
acc64 = 0.0
for v in probs:
    acc64 += float(v)
print("sum_seq64", repr(acc64))

# argmax 语义（policy_select_idx greedy 路径）
pp = np.array([0.0, 0.5, 0.5, 0.1], dtype=np.float32)
print("argmax_tie", int(np.argmax(pp)), "value", repr(float(pp[int(np.argmax(pp))])))
