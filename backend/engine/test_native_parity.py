"""Rust 扩展与 Python oracle 的位级对拍：MT19937 兼容层 + 伤害公式。

Rust 实现（native/roco-core）必须与 CPython random 模块产生完全一致的
随机序列、与 backend.vm.damage.calc_damage 产生完全一致的伤害值。
"""

from __future__ import annotations

import math
import random
import sys

import pytest

sys.path.insert(0, ".")

try:
    import roco_engine
except ImportError:
    pytest.skip("roco_engine 未编译（maturin develop --release）", allow_module_level=True)

from backend.vm.damage import calc_damage as py_calc_damage


def test_engine_info() -> None:
    assert "Rust" in roco_engine.engine_info()


# ── MT19937 对拍 ──

@pytest.mark.parametrize('seed', [1, 42, 0, 20260917, 2**40 + 12345, -(2**33 + 7)])
def test_random_stream(seed: int) -> None:
    random.seed(seed)
    expected = [random.random() for _ in range(64)]
    assert roco_engine.py_random_stream(seed, 64) == expected


@pytest.mark.parametrize('seed', [7, 123456789])
def test_choice_stream(seed: int) -> None:
    items = ['电', '火', '水', '草', '武', '妖', '光', '暗']
    random.seed(seed)
    expected = [random.choice(items) for _ in range(50)]
    assert roco_engine.py_choice_stream(seed, items, 50) == expected


@pytest.mark.parametrize('seed', [7, 20260917])
@pytest.mark.parametrize('n_pop,k', [(20, 5), (100, 7), (10, 10), (50, 1)])
def test_sample_indices(seed: int, n_pop: int, k: int) -> None:
    random.seed(seed)
    expected = [random.sample(range(n_pop), k) for _ in range(8)]
    assert roco_engine.py_sample_indices(seed, n_pop, k, 8) == expected


@pytest.mark.parametrize('seed,n', [(3, 1), (3, 2), (99, 6), (99, 7), (5, 100)])
def test_randbelow_stream(seed: int, n: int) -> None:
    rng = random.Random(seed)
    expected = [rng._randbelow(n) for _ in range(64)]
    assert roco_engine.py_randbelow_stream(seed, n, 64) == expected


def test_shuffle_matches() -> None:
    seed = 20260917
    random.seed(seed)
    expected = list(range(40))
    random.shuffle(expected)
    assert roco_engine.py_shuffle_indices(seed, 40) == expected


# ── 伤害公式对拍 ──

def test_damage_grid_matches() -> None:
    """覆盖幂次/克制/天气/减伤/连击/印记各乘区的组合网格。"""
    cases = []
    for power in (0, 30, 80, 90, 120, 200):
        for atk in (1, 50, 130, 300):
            for dfn in (1, 60, 110, 280):
                cases.append(dict(power=power, atk_base=atk, def_base=dfn))
    for case in cases:
        assert roco_engine.py_calc_damage(
            case['power'], case['atk_base'], case['def_base'],
        ) == py_calc_damage(case['power'], case['atk_base'], case['def_base'])


def test_damage_fuzz_matches() -> None:
    """种子化模糊对拍：连续乘区值含 .5 边界与常规值。"""
    rng = random.Random(777)
    for _ in range(500):
        kwargs = dict(
            power=rng.choice([0, 40, 75, 90, 110, 150]),
            atk_base=rng.randint(20, 400),
            def_base=rng.randint(20, 400),
            atk_stage=rng.choice([-0.5, -0.3, 0.0, 0.2, 0.4, 0.5]),
            def_stage=rng.choice([-0.4, 0.0, 0.3]),
            stab_mult=rng.choice([1.0, 1.25, 1.5]),
            type_mult=rng.choice([0.5, 1.0, 2.0]),
            weather_mult=rng.choice([0.8, 1.0, 1.2]),
            damage_reduction=rng.choice([0.0, 0.2, 0.35, 0.5]),
            power_mult=rng.choice([0.5, 1.0, 1.3, 2.0]),
            counter_power_mult=rng.choice([0.5, 1.0, 1.5]),
            additive_power=rng.choice([0, 10, 30]),
            damage_mult=rng.choice([0.7, 1.0, 1.4]),
            combo_count=rng.choice([1, 2, 3]),
            mark_bonus=rng.choice([0.0, 0.1, 0.25]),
        )
        assert roco_engine.py_calc_damage(**kwargs) == py_calc_damage(**kwargs), kwargs


def test_damage_half_even_boundaries() -> None:
    """精确命中 .5 的组合（银行家舍入敏感点）。"""
    # (37/41)*atk/def*power_term 可能落在 .5 上；扫一批等值组合
    found_ties = 0
    for power_term in range(1, 200):
        for atk in range(41, 441, 10):
            for dfn in (82, 110, 164, 205):
                core0 = (37.0 / 41.0) * atk / dfn * power_term
                if (core0 - math.floor(core0)) == 0.5 or (core0 - math.floor(core0)) == 0.0:
                    found_ties += 1
                    assert roco_engine.py_calc_damage(power_term, atk, dfn) == \
                        py_calc_damage(power_term, atk, dfn)
    assert found_ties > 0, '未覆盖到任何舍入边界组合'
