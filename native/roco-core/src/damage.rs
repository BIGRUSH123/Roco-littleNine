//! damage — 伤害公式（移植自 backend/vm/damage.py，纯函数）。
//!
//! 与 Python 的位级一致由两点保证：
//! 1. 相同运算顺序（IEEE f64 除法/乘法均为精确定义，不做重结合）；
//! 2. Python round() 是银行家舍入（round-half-to-even），必须用 py_round
//!    而不是 Rust 的 f64::round（half away from zero）。

/// Python `round(x)`（单参数，返回 int）：round-half-to-even。
pub fn py_round(x: f64) -> i64 {
    let r = x.round(); // half away from zero
    let frac = (x - x.trunc()).abs();
    if frac == 0.5 {
        // 恰好 .5：向偶数舍入
        if r % 2.0 != 0.0 {
            let even = r - x.signum();
            return even as i64;
        }
    }
    r as i64
}

/// 等价 backend.vm.damage.calc_damage。参数与公式顺序逐行对齐原实现。
#[allow(clippy::too_many_arguments)]
pub fn calc_damage(
    power: i64,
    atk_base: i64,
    def_base: i64,
    atk_stage: f64,
    def_stage: f64,
    stab_mult: f64,
    type_mult: f64,
    weather_mult: f64,
    damage_reduction: f64,
    power_mult: f64,
    counter_power_mult: f64,
    additive_power: i64,
    damage_mult: f64,
    combo_count: i64,
    mark_bonus: f64,
) -> i64 {
    if atk_base <= 0 || def_base <= 0 {
        return 0;
    }
    let combo_count = if combo_count < 1 { 1 } else { combo_count };

    let power_term = py_round(
        (power as f64 * counter_power_mult + additive_power as f64) * power_mult,
    );
    if power_term <= 0 {
        return 0;
    }

    let mut core = (37.0f64 / 41.0) * atk_base as f64 / def_base as f64 * power_term as f64;
    core *= stab_mult * type_mult * weather_mult * (1.0 - damage_reduction);
    core *= 1.0 + atk_stage - def_stage + mark_bonus;
    core *= combo_count as f64 * damage_mult;

    if damage_reduction >= 1.0 {
        return 0;
    }
    let result = core.round_even_max1();
    if std::env::var("ROCO_DEBUG_DMG").is_ok() {
        eprintln!(
            "[rust calc] power={power} atk={atk_base} def={def_base} a_st={atk_stage} d_st={def_stage} dr={damage_reduction} stab={stab_mult} type={type_mult} weather={weather_mult} pm={power_mult} cpm={counter_power_mult} add={additive_power} dm={damage_mult} combo={combo_count} mark={mark_bonus} -> {result}"
        );
    }
    result
}

trait RoundEven {
    fn round_even_max1(self) -> i64;
}

impl RoundEven for f64 {
    #[inline]
    fn round_even_max1(self) -> i64 {
        let v = py_round(self);
        if v < 1 { 1 } else { v }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn banker_rounding() {
        assert_eq!(py_round(2.5), 2);
        assert_eq!(py_round(3.5), 4);
        assert_eq!(py_round(1.5), 2);
        assert_eq!(py_round(0.5), 0);
        assert_eq!(py_round(-2.5), -2);
        assert_eq!(py_round(-1.5), -2);
        assert_eq!(py_round(2.4), 2);
        assert_eq!(py_round(2.6), 3);
    }

    #[test]
    fn doc_example_matches() {
        // (37/41)*130/110*90 = 95.9867... -> round -> 96
        let d = calc_damage(90, 130, 110, 0.0, 0.0, 1.0, 1.0, 1.0, 0.0, 1.0, 1.0, 0, 1.0, 1, 0.0);
        assert_eq!(d, 96);
    }
}
