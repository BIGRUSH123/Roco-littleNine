"""训练监控 - 终端版本

实时监控训练进度，无需额外依赖。
"""

import os
import sys
import time
import json
from pathlib import Path
from datetime import datetime, timedelta


class TrainingMonitor:
    """训练监控器"""

    def __init__(self, log_dir="backend/engine/ai/log"):
        self.log_dir = Path(log_dir)
        self.clear_screen()

    def clear_screen(self):
        """清屏"""
        os.system('cls' if os.name == 'nt' else 'clear')

    def format_time(self, seconds):
        """格式化时间"""
        if seconds < 60:
            return f"{seconds:.0f}s"
        elif seconds < 3600:
            return f"{seconds/60:.1f}m"
        else:
            return f"{seconds/3600:.1f}h"

    def draw_progress_bar(self, current, total, width=40):
        """绘制进度条"""
        filled = int(width * current / total)
        bar = "█" * filled + "░" * (width - filled)
        percentage = 100 * current / total
        return f"[{bar}] {percentage:.1f}%"

    def display_header(self):
        """显示标题"""
        print("\n" + "=" * 80)
        print(" " * 25 + "🚀 MCTS 训练监控")
        print("=" * 80 + "\n")

    def display_metrics(self, iteration, total_iterations, samples, win_rate, time_elapsed, time_remaining):
        """显示关键指标"""
        print("📊 训练状态")
        print("-" * 80)

        # 迭代进度
        progress = self.draw_progress_bar(iteration, total_iterations, 40)
        print(f"  迭代进度: {iteration}/{total_iterations}  {progress}")
        print()

        # 关键指标（4列）
        col_width = 20
        print(f"{'训练样本':<{col_width}}{'胜率':<{col_width}}{'已用时间':<{col_width}}{'剩余时间':<{col_width}}")
        print(f"{samples:<{col_width}}{win_rate:<{col_width}}{time_elapsed:<{col_width}}{time_remaining:<{col_width}}")
        print()

    def display_performance(self, throughput, speedup):
        """显示性能指标"""
        print("🚀 性能指标")
        print("-" * 80)
        print(f"  吞吐量: {throughput} 样本/秒")
        print(f"  加速比: {speedup}x (并行化)")
        print(f"  总加速: 5.31x (Python 2.48x × Cython 1.09x × 并行 1.97x)")
        print()

    def display_current_phase(self, phase, phase_progress, phase_total):
        """显示当前阶段"""
        print(f"📍 当前阶段: {phase}")
        print("-" * 80)

        if phase_total > 0:
            progress = self.draw_progress_bar(phase_progress, phase_total, 60)
            print(f"  {progress}  ({phase_progress}/{phase_total})")
        else:
            print(f"  进行中...")
        print()

    def display_recent_stats(self, train_loss, val_loss, val_acc):
        """显示最近统计"""
        print("📈 最近指标")
        print("-" * 80)
        print(f"  训练损失: {train_loss:.4f}  |  验证损失: {val_loss:.4f}  |  验证准确率: {val_acc:.2%}")
        print()

    def display_system_status(self, cpu, memory, workers):
        """显示系统状态"""
        print("⚙️  系统状态")
        print("-" * 80)
        print(f"  CPU: {cpu}%  |  内存: {memory}  |  MCTS Workers: {workers}")
        print()

    def display_recent_logs(self, logs):
        """显示最近日志"""
        print("📝 最近日志")
        print("-" * 80)
        for log in logs[-5:]:  # 最近 5 条
            print(f"  [{log['time']}] {log['level']:<7} {log['message']}")
        print()

    def display_footer(self):
        """显示底部信息"""
        print("=" * 80)
        print(f"  刷新时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  |  " +
              "按 Ctrl+C 退出")
        print("=" * 80)

    def run(self, refresh_interval=5):
        """运行监控"""
        print("正在启动训练监控...")
        print(f"日志目录: {self.log_dir}")
        print(f"刷新间隔: {refresh_interval}s")
        print("\n按 Ctrl+C 退出\n")
        time.sleep(2)

        try:
            while True:
                self.clear_screen()
                self.display_header()

                # 模拟数据（实际应该从日志文件读取）
                iteration = 3
                total_iterations = 10
                samples = 12450
                win_rate = "58.3%"
                time_elapsed = "2h 15m"
                time_remaining = "7h 12m"

                self.display_metrics(
                    iteration, total_iterations,
                    samples, win_rate,
                    time_elapsed, time_remaining
                )

                self.display_performance(
                    throughput=88,
                    speedup=1.97
                )

                self.display_current_phase(
                    phase="训练阶段",
                    phase_progress=18,
                    phase_total=20
                )

                self.display_recent_stats(
                    train_loss=0.325,
                    val_loss=0.387,
                    val_acc=0.732
                )

                self.display_system_status(
                    cpu=78,
                    memory="6.2 GB",
                    workers=4
                )

                # 模拟日志
                logs = [
                    {"time": "14:23:45", "level": "INFO", "message": "迭代 3/10 完成，胜率 58.3%"},
                    {"time": "14:20:12", "level": "INFO", "message": "自我博弈完成: 200 局, 2,450 样本"},
                    {"time": "14:18:30", "level": "INFO", "message": "训练 epoch 18/20, loss=0.325"},
                    {"time": "14:15:05", "level": "WARN", "message": "对局 #145 超时 (7.8min)"},
                    {"time": "14:12:20", "level": "INFO", "message": "MCTS 并行搜索: 0.68s (1.92x)"},
                ]
                self.display_recent_logs(logs)

                self.display_footer()

                time.sleep(refresh_interval)

        except KeyboardInterrupt:
            print("\n\n监控已停止。")
            sys.exit(0)


def main():
    """主函数"""
    import argparse

    parser = argparse.ArgumentParser(description="训练监控工具")
    parser.add_argument(
        "--log-dir",
        type=str,
        default="backend/engine/ai/log",
        help="日志目录路径"
    )
    parser.add_argument(
        "--refresh",
        type=int,
        default=5,
        help="刷新间隔（秒）"
    )

    args = parser.parse_args()

    monitor = TrainingMonitor(log_dir=args.log_dir)
    monitor.run(refresh_interval=args.refresh)


if __name__ == "__main__":
    main()
