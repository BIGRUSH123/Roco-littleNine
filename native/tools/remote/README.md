# native/tools/remote — 远端（阿里云 DSW / PAI 实例）实验通道

本目录是"在远端实例上跑对局实验"的一整套工具：**登录一次，之后全程本地驱动**。
远端实例本身（23 vCPU / 200 GB / ROCm GPU）只当算力用，代码用本地工作树同步过去。

## 通道长什么样

```
本地 Chrome（--remote-debugging-port=9222，独立 profile）
   └─ CDP ──> Jupyter 网关页面（带阿里云登录 cookie）
                 └─ HTTP REST /api/contents  ← 上传代码、读日志、取产物
                 └─ WSS /api/kernels/<id>/channels ← 真正执行 shell/python
```

- 登录态在**真实 Chrome 的独立 profile**（`%TEMP%\dsw-cdp-profile`）里，不在 gstack 自动化浏览器里
  —— 后者的可见窗口在本机会崩（`browse handoff`/`--headed` 实测两次都崩），别再走那条路。
- `dsw.py` 用 CDP 导出的 cookie 直接和网关说话：**没有浏览器也能跑**，只有 cookie 过期时才需要重新登录。

## 使用顺序

```powershell
# 1) 起 Chrome + 调试端口，人工扫码登录（profile 持久化，通常只需一次）
pwsh -File native/tools/remote/login.ps1          # 见下（首次手工建）

# 2) 导出 cookie（CDP → cookies.json，与 dsw.py 同目录）
node native/tools/remote/cdp.mjs cookies native/tools/remote/cookies.json aliyun.com

# 3) 远端能不能说话
env\python.exe -X utf8 native/tools/remote/dsw.py ping        # 内核版本 + 主机名
env\python.exe -X utf8 native/tools/remote/dsw.py ls          # 列远端工作区

# 4) 同步代码（本地打包含当前工作树 → 上传 → 远端解包）
env\python.exe -X utf8 native/tools/remote/mkbundle.py
env\python.exe -X utf8 native/tools/remote/dsw.py put .zcode/tmp/dsw/roco_patch.zip roco_remote/roco_patch.zip
env\python.exe -X utf8 native/tools/remote/dsw.py sh "cd /mnt/workspace/roco_remote && python -m zipfile -e roco_patch.zip ."

# 5) 跑实验（长任务一律 nohup 后台 + 日志落盘，再用 cat/--tail 轮询）
env\python.exe -X utf8 native/tools/remote/dsw.py sh "cd /mnt/workspace/roco_remote && bash run_ab.sh shipped 120 60 8"
```

## 每个脚本干什么

| 文件 | 作用 |
|---|---|
| `cdp.mjs` | CDP 客户端：`targets` / `goto <url>` / `eval <js\|@file>` / `cookies <out.json> [hostFilter]` |
| `dsw.py` | 远端 shell：`ls/cat/put/get/rm/kernels/py/sh/bg/ping`（REST 文件 + kernel websocket 执行） |
| `mkbundle.py` | 把本地工作树打成交付 zip（排除 `__pycache__`、`native/target`、`ai/log`、`ai/data`、检查点等） |
| `ab_per_team.py` | 逐阵容 A/B 分片驱动：`--ab <mode> --games N --shards M --shard i`，产物 `ab/<mode>_s<i>.json` |
| `run_ab.sh` | 起 M 个分片进程并行跑 `ab_per_team.py`（远端 23 核，8~16 片合适） |
| `stall_stats.py` | 统计随机对局里的"空过动作率 / 打满回合率 / 平局率"（训练数据质量指标） |
| `trace_mirror.py` | 单局镜像逐回合追踪：动作、事件、血线、能量，以及"为什么选聚能"的规划层打分 |

## 注意

- **凭据不入库**：`cookies.json` / `kernel.json` 已 gitignore。cookie 里有 `login_aliyunid_ticket` 之类的账号凭据，
  不要贴进对话、issue 或提交。
- 远端工作区 `/mnt/workspace/roco_remote` 是持久化的（关机会保留），里面有 30K 局 BC 数据集（10.6 GB）与历史权重。
- 远端跑任何 python 都要 `PYTHONHASHSEED=0 PYTHONUTF8=1 PYTHONIOENCODING=utf-8`（与本地度量口径一致）。
- 远端没有 `maturin`，也没编 Rust 扩展 → 走纯 Python 实现（慢但口径一致）。
