//! np_random — numpy legacy `RandomState` 位级镜像（mtrand：MT19937 + rk_double 系）。
//!
//! MCTS 的根节点 Dirichlet 噪声（`np.random.dirichlet`）与对手动作采样
//! （`np.random.random`）在 Python oracle 中走 numpy 全局 legacy RandomState，
//! Rust 侧必须产生完全一致的随机序列，阶段4「同种子结果一致」门才能逐位成立。
//!
//! 与 CPython `random.Random` 的关键差异（已实测 numpy 2.4.6 校准）：
//! - 种子：`np.random.seed(int)` → `mt19937_seed`（即 init_genrand），
//!   不是 CPython 的 init_by_array；state[0] == seed，pos == 624。
//! - 取值：`rk_double` = (a*2^26 + b)/2^53，a = u1>>5，b = u2>>6
//!   （CPython genrand_res53 同式，但种子与后续分布算法不同）。
//! - `standard_exponential` = -log(1-u)；`standard_gamma` 为 Marsaglia-Tsang
//!   的 legacy 变体；`dirichlet` = 各分量标准 gamma 归一化。
//! 校准基准见 native/tools/probe_np_rng.py 与文件末尾单测。

const N: usize = 624;
const M: usize = 397;
const MATRIX_A: u32 = 0x9908_b0df;
const UPPER_MASK: u32 = 0x8000_0000;
const LOWER_MASK: u32 = 0x7fff_ffff;

pub struct NpRandom {
    key: [u32; N],
    pos: usize,
    has_gauss: bool,
    gauss: f64,
}

impl NpRandom {
    /// 等价 `np.random.seed(seed)`（seed ∈ [0, 2^32-1]）。
    pub fn seed_u32(seed: u32) -> Self {
        let mut r = NpRandom { key: [0; N], pos: N, has_gauss: false, gauss: 0.0 };
        r.key[0] = seed;
        for i in 1..N {
            r.key[i] = 1812433253u32
                .wrapping_mul(r.key[i - 1] ^ (r.key[i - 1] >> 30))
                .wrapping_add(i as u32);
        }
        r
    }

    /// 等价 `np.random.seed(seed % (2**32 - 1))`（训练侧种子协议）。
    pub fn seed_i64(seed: i64) -> Self {
        Self::seed_u32((seed % (2i64.pow(32) - 1)) as u32)
    }

    /// 状态前 n 个字（对拍用，py 侧对应 np.random.get_state()[1][:n]）。
    pub fn state_head(&self, n: usize) -> Vec<u32> {
        self.key[..n.min(N)].to_vec()
    }

    /// 当前抽取位置（py np.random.get_state()[2]）。
    pub fn pos(&self) -> usize {
        self.pos
    }

    fn twist(&mut self) {
        for i in 0..(N - M) {
            let y = (self.key[i] & UPPER_MASK) | (self.key[i + 1] & LOWER_MASK);
            self.key[i] = self.key[i + M] ^ (y >> 1) ^ if y & 1 != 0 { MATRIX_A } else { 0 };
        }
        for i in (N - M)..(N - 1) {
            let y = (self.key[i] & UPPER_MASK) | (self.key[i + 1] & LOWER_MASK);
            self.key[i] = self.key[i + M - N] ^ (y >> 1) ^ if y & 1 != 0 { MATRIX_A } else { 0 };
        }
        let y = (self.key[N - 1] & UPPER_MASK) | (self.key[0] & LOWER_MASK);
        self.key[N - 1] = self.key[M - 1] ^ (y >> 1) ^ if y & 1 != 0 { MATRIX_A } else { 0 };
    }

    /// numpy mt19937_next32（含 tempering）。
    pub fn next_u32(&mut self) -> u32 {
        if self.pos >= N {
            self.twist();
            self.pos = 0;
        }
        let mut y = self.key[self.pos];
        self.pos += 1;
        y ^= y >> 11;
        y ^= (y << 7) & 0x9d2c_5680;
        y ^= (y << 15) & 0xefc6_0000;
        y ^= y >> 18;
        y
    }

    /// 等价 numpy `random_sample()`（rk_double）。
    pub fn random(&mut self) -> f64 {
        let a = (self.next_u32() >> 5) as f64;
        let b = (self.next_u32() >> 6) as f64;
        (a * 67108864.0 + b) / 9007199254740992.0
    }

    /// 等价 numpy `standard_exponential()`（legacy：-log(1-u)）。
    pub fn standard_exponential(&mut self) -> f64 {
        -(1.0 - self.random()).ln()
    }

    /// 等价 numpy legacy `gauss()`（极坐标法，含第二值缓存）。
    pub fn gauss(&mut self) -> f64 {
        if self.has_gauss {
            self.has_gauss = false;
            return self.gauss;
        }
        loop {
            let x1 = 2.0 * self.random() - 1.0;
            let x2 = 2.0 * self.random() - 1.0;
            let r2 = x1 * x1 + x2 * x2;
            if r2 >= 1.0 || r2 == 0.0 {
                continue;
            }
            let f = (-2.0 * r2.ln() / r2).sqrt();
            self.gauss = f * x1;
            self.has_gauss = true;
            return f * x2;
        }
    }

    /// 等价 numpy legacy `standard_gamma(shape)`（Marsaglia-Tsang 变体）。
    pub fn standard_gamma(&mut self, shape: f64) -> f64 {
        if shape == 1.0 {
            return self.standard_exponential();
        }
        if shape == 0.0 {
            return 0.0;
        }
        if shape < 1.0 {
            loop {
                let u = self.random();
                let v = self.standard_exponential();
                if u <= 1.0 - shape {
                    let x = u.powf(1.0 / shape);
                    if x <= v {
                        return x;
                    }
                } else {
                    let y = -((1.0 - u) / shape).ln();
                    let x = (1.0 - shape + shape * y).powf(1.0 / shape);
                    if x <= v + y {
                        return x;
                    }
                }
            }
        } else {
            let b = shape - 1.0 / 3.0;
            let c = 1.0 / (9.0 * b).sqrt();
            loop {
                let x = loop {
                    let x = self.gauss();
                    if 1.0 + c * x > 0.0 {
                        break x;
                    }
                };
                let vv = 1.0 + c * x;
                let v = vv * vv * vv;
                let u = self.random();
                // 注意：按 C 从左到右结合（0.0331*X*X*X*X），与 (x*x)*(x*x) 差 1 ULP
                if u < 1.0 - 0.0331 * x * x * x * x {
                    return b * v;
                }
                if u.ln() < 0.5 * x * x + b * (1.0 - v + v.ln()) {
                    return b * v;
                }
            }
        }
    }

    /// 等价 numpy `dirichlet(alpha)`：各分量标准 gamma 后按总和归一化。
    pub fn dirichlet(&mut self, alpha: &[f64]) -> Vec<f64> {
        let mut x: Vec<f64> = alpha.iter().map(|&a| self.standard_gamma(a)).collect();
        let acc: f64 = x.iter().sum();
        let inv = 1.0 / acc;
        for v in x.iter_mut() {
            *v *= inv;
        }
        x
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // 基准值由 native/tools/probe_np_rng.py 采集（numpy 2.4.6，seed=12345）
    const SEED: u32 = 12345;

    #[test]
    fn state_head_matches_numpy() {
        let r = NpRandom::seed_u32(SEED);
        assert_eq!(&r.key[..8], &[12345, 2003863422, 2690739229, 1915043646, 1178839775, 1127561115, 2079742408, 4273798484]);
        assert_eq!(r.pos, N);
    }

    #[test]
    fn raw_u32_matches_numpy_randint() {
        // np.random.seed(12345); np.random.randint(0, 2**32, size=6, dtype=np.uint32)
        let mut r = NpRandom::seed_u32(SEED);
        let got: Vec<u32> = (0..6).map(|_| r.next_u32()).collect();
        assert_eq!(got, vec![3992670690, 3823185381, 1358822685, 561383553, 789925284, 170765737]);
    }

    #[test]
    fn random5_matches_numpy() {
        let mut r = NpRandom::seed_u32(SEED);
        let got: Vec<f64> = (0..5).map(|_| r.random()).collect();
        assert_eq!(got, vec![
            0.9296160928171479,
            0.3163755545817859,
            0.18391881167709445,
            0.2045602785530397,
            0.5677250290816866,
        ]);
    }

    #[test]
    fn standard_exponential_matches_numpy() {
        let mut r = NpRandom::seed_u32(SEED);
        let got: Vec<f64> = (0..5).map(|_| r.standard_exponential()).collect();
        assert_eq!(got, vec![
            2.6537906331017487,
            0.380346568552371,
            0.20324143347362159,
            0.22886020849774785,
            0.8386933864671793,
        ]);
    }

    #[test]
    fn standard_gamma_small_shape_matches_numpy() {
        let mut r = NpRandom::seed_u32(SEED);
        let got: Vec<f64> = (0..5).map(|_| r.standard_gamma(0.3)).collect();
        assert_eq!(got, vec![
            1.5249297284595404,
            0.003537967513519072,
            0.1515165724830579,
            2.6552914918937334,
            0.389099765709897,
        ]);
    }

    #[test]
    fn standard_gamma_large_shape_matches_numpy() {
        let mut r = NpRandom::seed_u32(SEED);
        let got: Vec<f64> = (0..5).map(|_| r.standard_gamma(1.5)).collect();
        assert_eq!(got, vec![
            0.959231384462677,
            1.764214007833853,
            3.4116777239020886,
            2.2100326887475297,
            3.0972370900728157,
        ]);
    }

    #[test]
    fn dirichlet_matches_numpy() {
        let mut r = NpRandom::seed_u32(SEED);
        let got = r.dirichlet(&[0.3; 4]);
        assert_eq!(got, vec![
            0.35174918800009175,
            0.0008160882280839184,
            0.03494969659573183,
            0.6124850271760924,
        ]);

        // MCTS 实际用到的维度：len(valid_actions) 最多 17
        let mut r = NpRandom::seed_u32(SEED);
        let got = r.dirichlet(&[0.3; 17]);
        assert_eq!(got, vec![
            0.1459938014413331,
            0.00033871811732359095,
            0.014505901475544489,
            0.25421243458083725,
            0.037251653552130955,
            0.037018027432138244,
            1.148146778166927e-08,
            0.0017056372021679094,
            0.05284922908604554,
            0.25492571313823625,
            0.021908169970182637,
            0.0075973684230244524,
            0.006186197049189326,
            0.04707365484623151,
            5.548724629289171e-07,
            0.1094471030757067,
            0.00898582425597739,
        ]);
    }
}
