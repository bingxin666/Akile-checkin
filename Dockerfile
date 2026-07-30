FROM python:3.11-slim

# 安装 Chromium 和依赖
RUN apt-get update && apt-get install -y \
    chromium \
    chromium-driver \
    && rm -rf /var/lib/apt/lists/*

# 设置工作目录
WORKDIR /app

# 复制依赖文件
COPY requirements.txt .

# 安装 Python 依赖
RUN pip install --no-cache-dir -r requirements.txt

# 复制项目文件
COPY . .

# 默认启用定时签到模式：每天 10:00 左右（±5 分钟）尝试签到
ENV RUN_SCHEDULED=1
ENV AKILE_CHECKIN_TIME=10:00
ENV AKILE_RANDOM_DELAY_MINUTES=5

# 运行脚本
CMD ["python", "Akile-Checkin.py"]
