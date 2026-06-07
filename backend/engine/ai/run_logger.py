"""backend/engine/ai/run_logger.py — 训练运行日志记录器。

每次训练自动：
  1. 使用 logging 模块写全量日志 `run_<ts>.log`（控制台由 train.py 的 _log() 统一接管）；
  2. 抽取每轮关键指标写结构化 `run_<ts>.jsonl`（一行一轮，缓冲区落盘）；
  3. 训练结束时生成易读汇总表 `run_<ts>_summary.txt`；
  4. 可选 TensorBoard 集成 — 启动时自动创建 tensorboard/ 子目录。

使用方式：
  logger = RunLogger(log_dir, params=params, enable_tensorboard=True)
  logger.install()       # 替换全局 logger，此后 logger.info(...) 自动双写
  logger.info("开始训练")

  每轮结束：
    logger.record_iteration({...}, step=iteration)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any


# ═══════════════════════════════════════════════════════════════════
# 内部 Logger
# ═══════════════════════════════════════════════════════════════════

_LOGGER_NAME = "RL_Engine"


def _build_logger(log_path: Path) -> logging.Logger:
    """创建文件日志器（控制台输出由 train.py 的 _log() 统一管理）。"""
    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(logging.INFO)
    if logger.handlers:
        return logger

    # 文件：带时间戳的全量输出
    file_handler = logging.FileHandler(str(log_path), encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("[%(asctime)s] %(message)s"))
    logger.addHandler(file_handler)

    return logger


# ═══════════════════════════════════════════════════════════════════
# RunLogger
# ═══════════════════════════════════════════════════════════════════

class RunLogger:
    """单次训练运行的日志器。"""

    def __init__(
        self,
        log_dir: str | Path,
        params: dict[str, Any] | None = None,
        run_name: str | None = None,
        *,
        enable_tensorboard: bool = False,
        metrics_buffer_size: int = 10,
    ) -> None:
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.run_id = run_name or f"run_{ts}"
        self.full_log_path = self.log_dir / f"{self.run_id}.log"
        self.metrics_path = self.log_dir / f"{self.run_id}.jsonl"
        self.summary_path = self.log_dir / f"{self.run_id}_summary.txt"

        self._params = dict(params or {})
        self._records: list[dict[str, Any]] = []
        self._start = datetime.now()

        # ── logging 模块双写 ──
        self._logger = _build_logger(self.full_log_path)

        # ── metrics JSONL（缓冲写入） ──
        self._metrics_fp = open(self.metrics_path, "w", encoding="utf-8")
        self._metrics_buffer_size = metrics_buffer_size
        self._metrics_write_count = 0

        header = {
            "type": "run_start",
            "run_id": self.run_id,
            "time": self._start.isoformat(timespec="seconds"),
            "params": self._params,
        }
        self._metrics_fp.write(json.dumps(header, ensure_ascii=False) + "\n")
        self._metrics_fp.flush()

        # ── TensorBoard ──
        self._tb_writer = None
        if enable_tensorboard:
            try:
                from torch.utils.tensorboard import SummaryWriter
                tb_dir = self.log_dir / "tensorboard"
                tb_dir.mkdir(parents=True, exist_ok=True)
                self._tb_writer = SummaryWriter(log_dir=str(tb_dir))
                self.info(f"TensorBoard 日志目录: {tb_dir}")
            except ImportError:
                self.info("TensorBoard 未安装 (pip install tensorboard)，跳过")

    # ── 安装全局 logger ──

    def install(self) -> None:
        """替换全局 RL_Engine logger，此后可直接使用：
            import logging
            logging.getLogger("RL_Engine").info(...)
        或简化为 self.info(...)。
        """
        # logger 已在 __init__ 中构建，此处仅确保后续 getLogger 拿到同一实例
        pass

    def info(self, msg: str) -> None:
        """向控制台 + 全量日志写入一行。"""
        self._logger.info(msg)

    # ── 指标上报 ──

    def record_iteration(
        self,
        metrics: dict[str, Any],
        *,
        step: int | None = None,
    ) -> None:
        """记录一轮迭代的关键指标。

        metrics 中的字段会被写为 JSONL 行；若启用 TensorBoard，
        标准字段（train_v_loss, train_p_loss, val_acc, win_rate 等）
        会被自动提取并写入 SummaryWriter。
        """
        rec = {"type": "iteration", **metrics}
        self._records.append(rec)

        # ── JSONL 缓冲写入 ──
        self._metrics_fp.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self._metrics_write_count += 1
        if self._metrics_write_count >= self._metrics_buffer_size:
            self._metrics_fp.flush()
            self._metrics_write_count = 0

        # ── TensorBoard ──
        if self._tb_writer is not None:
            s = step if step is not None else len(self._records)
            for key, tag in _TB_MAPPING:
                val = metrics.get(key)
                if val is not None:
                    self._tb_writer.add_scalar(tag, float(val), s)

    # ── 收尾汇总 ──

    def finalize(self, best_iteration: int | None = None) -> None:
        elapsed = (datetime.now() - self._start).total_seconds()
        footer = {
            "type": "run_end",
            "elapsed_sec": round(elapsed, 1),
            "iterations": len(self._records),
            "promotions": sum(1 for r in self._records if r.get("promoted")),
            "best_iteration": best_iteration,
        }
        self._metrics_fp.write(json.dumps(footer, ensure_ascii=False) + "\n")
        self._metrics_fp.flush()

        lines = self._build_summary(footer)
        with open(self.summary_path, "w", encoding="utf-8") as fp:
            fp.write("\n".join(lines) + "\n")

        # 打印汇总到控制台
        print("\n" + "\n".join(lines))
        print(
            f"\n日志已写入:\n  全量: {self.full_log_path}\n"
            f"  指标: {self.metrics_path}\n  汇总: {self.summary_path}"
        )

        self._metrics_fp.close()

        if self._tb_writer is not None:
            self._tb_writer.close()

    # ── 内部 ──

    def _build_summary(self, footer: dict[str, Any]) -> list[str]:
        lines: list[str] = []
        lines.append("=" * 78)
        lines.append(f"训练汇总  {self.run_id}")
        lines.append("=" * 78)
        if self._params:
            kv = "  ".join(f"{k}={v}" for k, v in self._params.items())
            lines.append(f"参数: {kv}")
        lines.append(
            f"总耗时 {footer['elapsed_sec']:.0f}s  "
            f"迭代 {footer['iterations']}  晋升 {footer['promotions']}  "
            f"最优轮 {footer['best_iteration']}"
        )
        lines.append("-" * 78)
        header = (
            f"{'轮':>3} {'样本':>6} {'平%':>5} {'胜负局':>7} "
            f"{'val_acc':>8} {'v(tr/val)':>13} {'p(tr/val)':>13} "
            f"{'门控%':>6} {'晋升':>4}"
        )
        lines.append(header)
        lines.append("-" * 78)
        for r in self._records:
            promoted = "✓" if r.get("promoted") else ("-" if r.get("win_rate") is not None else " ")
            win = r.get("win_rate")
            win_s = f"{win * 100:5.1f}" if win is not None else "  -  "
            lines.append(
                f"{r.get('iteration', 0):>3} "
                f"{r.get('samples', 0):>6} "
                f"{r.get('draw_ratio', 0.0) * 100:>5.1f} "
                f"{r.get('decisive_games', 0):>3}/{r.get('total_games', 0):<3} "
                f"{r.get('final_val_acc', 0.0):>8.3f} "
                f"{r.get('final_train_v_loss', 0.0):>6.4f}/{r.get('final_val_v_loss', 0.0):<6.4f} "
                f"{r.get('final_train_p_loss', 0.0):>6.4f}/{r.get('final_val_p_loss', 0.0):<6.4f} "
                f"{win_s:>6} {promoted:>4}"
            )
        lines.append("-" * 78)
        lines.append("用时占比（用于定位优化方向）")
        lines.append("-" * 78)
        timing_header = (
            f"{'轮':>3} {'总耗时s':>8} {'自博%':>7} {'训练%':>7} "
            f"{'评估%':>7} {'保存%':>7} {'其它%':>7} {'样本/s':>8}"
        )
        lines.append(timing_header)
        lines.append("-" * 78)
        for r in self._records:
            pct = r.get("phase_percent") or {}
            lines.append(
                f"{r.get('iteration', 0):>3} "
                f"{r.get('iteration_sec', 0.0):>8.1f} "
                f"{pct.get('selfplay', 0.0):>7.1f} "
                f"{pct.get('train', 0.0):>7.1f} "
                f"{pct.get('eval', 0.0):>7.1f} "
                f"{pct.get('checkpoint', 0.0):>7.1f} "
                f"{pct.get('other', 0.0):>7.1f} "
                f"{r.get('samples_per_sec', 0.0):>8.1f}"
            )
        lines.append("=" * 78)
        return lines


# ═══════════════════════════════════════════════════════════════════
# TensorBoard 字段映射
# ═══════════════════════════════════════════════════════════════════

_TB_MAPPING: list[tuple[str, str]] = [
    # metrics key → TensorBoard tag
    ("final_train_v_loss", "Loss/Train_Value"),
    ("final_val_v_loss",   "Loss/Val_Value"),
    ("final_train_p_loss", "Loss/Train_Policy"),
    ("final_val_p_loss",   "Loss/Val_Policy"),
    ("final_val_acc",      "Eval/Val_Acc"),
    ("win_rate",           "Eval/Win_Rate"),
    ("samples_per_sec",    "Sys/Samples_Per_Sec"),
]
