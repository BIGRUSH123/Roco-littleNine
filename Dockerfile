# ─────────────────────────────────────────────────────────────
# 格斗小九 AI 训练镜像（国内源稳定版）
# GPU 版：docker build -t roco-train:gpu .
# ─────────────────────────────────────────────────────────────
FROM nvidia/cuda:12.8.0-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive
WORKDIR /app

# 1. 直接覆盖为阿里云 Ubuntu 22.04 源（避免 sed 匹配失败）
RUN echo "deb http://mirrors.aliyun.com/ubuntu/ jammy main restricted universe multiverse\n\
deb http://mirrors.aliyun.com/ubuntu/ jammy-updates main restricted universe multiverse\n\
deb http://mirrors.aliyun.com/ubuntu/ jammy-backports main restricted universe multiverse\n\
deb http://mirrors.aliyun.com/ubuntu/ jammy-security main restricted universe multiverse" > /etc/apt/sources.list

# 2. 安装基础工具（提前装好 gnupg2 和 ca-certificates，避免 PPA 密钥问题）
RUN apt-get update && apt-get install -y --no-install-recommends \
    software-properties-common \
    curl \
    git \
    ca-certificates \
    gnupg2 \
    lsb-release \
    && rm -rf /var/lib/apt/lists/*

# 3. 添加 deadsnakes PPA（提前更新一次，确保 key 能拉下来）
RUN add-apt-repository -y ppa:deadsnakes/ppa && apt-get update

# 4. 安装 Python 3.12 及虚拟环境
RUN apt-get install -y \
    python3.12 \
    python3.12-venv \
    && rm -rf /var/lib/apt/lists/*

# 5. 使用 ensurepip 安装 pip（无需网络）
RUN python3.12 -m ensurepip --upgrade

# 6. 配置 pip 国内源
RUN python3.12 -m pip config set global.index-url https://mirrors.aliyun.com/pypi/simple/

# 7. 安装本地 torch 轮子（需保证 wheels/ 目录下有文件）

COPY wheels/*.whl /tmp/wheels/


# 假设你的 wheels 目录下只有一个 torch-*.whl 文件
RUN python3.12 -m pip install \
    --find-links /tmp/wheels \
    --index-url https://mirrors.aliyun.com/pypi/simple/ \
    --extra-index-url https://download.pytorch.org/whl/cu128 \
    /tmp/wheels/torch-*.whl

# 8. 安装其他核心依赖
RUN python3.12 -m pip install numpy

# 9. 拷贝项目代码
COPY backend/ ./backend/
COPY data/ ./data/
COPY pyproject.toml ./

# 10. 清理临时文件
RUN rm -rf /tmp/torch-*.whl

# 11. 设置默认 Python 软链
RUN ln -sf /usr/bin/python3.12 /usr/bin/python3

ENV PYTHONUNBUFFERED=1
VOLUME ["/app/checkpoints"]

ENTRYPOINT ["python3.12", "-m", "backend.engine.ai.train"]
CMD ["--iterations", "5", "--battles", "200", "--sims", "200"]