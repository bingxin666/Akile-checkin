**Akile.io 自动签到脚本**

基于 Selenium 实现的自动签到工具

## ✨ 特性

- 🤖 **全自动签到** - 自动登录并完成每日签到任务
- 🔐 **TOTP 自动填入** - 支持二次验证自动填写
- 🍪 **Session 持久化** - 复用本地 Chrome session，避免每次重复登录
- 🐳 **Docker 支持** - 开箱即用的容器化部署，默认每天 10:00 自动签到
- ⏰ **随机时间差** - 默认 ±5 分钟随机延迟，降低被检测风险

## 📦 快速开始

### Docker 部署（推荐）

镜像已自动构建并推送至 GitHub Container Registry (GHCR)，可直接拉取运行：

```bash
# 1. 克隆项目
git clone https://github.com/nianzhibai/Akile-checkin.git
cd Akile-checkin

# 2. 配置文件
cp config.ini.example config.ini
# 编辑 config.ini 填入你的账号信息

# 3. 运行容器（默认每天 10:00 左右签到，±5 分钟随机延迟）
docker run -d --name akile-checkin \
  -v $(pwd)/config.ini:/app/config.ini \
  -v $(pwd)/chrome_session:/app/chrome_session \
  ghcr.io/bingxin666/akile-checkin:latest
```

容器启动后会进入定时模式，每日在设定时间前后随机延迟执行一次签到。签到完成后容器会继续运行，等待下一次签到时间。

如需手动运行一次或覆盖默认签到时间，可设置环境变量：

```bash
docker run --rm \
  -e RUN_SCHEDULED=false \
  -e AKILE_CHECKIN_TIME=09:00 \
  -e AKILE_RANDOM_DELAY_MINUTES=10 \
  -v $(pwd)/config.ini:/app/config.ini \
  -v $(pwd)/chrome_session:/app/chrome_session \
  ghcr.io/bingxin666/akile-checkin:latest
```

### 本地构建 Docker 镜像

```bash
docker build -t akile-checkin .
docker run -d --name akile-checkin \
  -v $(pwd)/config.ini:/app/config.ini \
  -v $(pwd)/chrome_session:/app/chrome_session \
  akile-checkin
```

### 直接 Python 运行

```bash
# 1. 克隆项目
git clone https://github.com/nianzhibai/Akile-checkin.git
cd Akile-checkin

# 2. 安装依赖
pip install -r requirements.txt

# 3. 配置文件
cp config.ini.example config.ini
# 编辑 config.ini 填入你的账号信息

# 4. 运行脚本（立即执行一次）
python Akile-Checkin.py
```

如需启用本地定时模式，可设置环境变量：

```bash
export RUN_SCHEDULED=true
export AKILE_CHECKIN_TIME=10:00
export AKILE_RANDOM_DELAY_MINUTES=5
python Akile-Checkin.py
```

### GitHub Actions

- **定时签到工作流**：已移除定时触发器，保留 `workflow_dispatch` 手动触发。如需手动在 GitHub Actions 中运行，可在 Actions 页面手动触发 `Akile Daily Check-in`。
- **镜像构建工作流**：每次推送至 `main` 分支或创建 `v*` 标签时，会自动构建并推送 Docker 镜像到 GHCR。也可在 Actions 页面手动触发 `Build and Push Docker Image to GHCR`。

## ⚙️ 配置说明

编辑 `config.ini` 文件：

```ini
[akile]
email = your_email@example.com     # Akile 账号邮箱
password = your_password            # Akile 账号密码
totp =                              # TOTP 密钥（没有可不填）
session_dir = chrome_session        # 本地 Chrome session 保存目录
push_key =                          # Server 酱 push_key（可选）
```

脚本同时支持环境变量配置，便于 Docker 和 CI 场景使用：

| 环境变量 | 说明 | 默认值 |
|---|---|---|
| `AKILE_EMAIL` | Akile 登录邮箱 | 无 |
| `AKILE_PASSWORD` | Akile 登录密码 | 无 |
| `AKILE_TOTP` | TOTP 二次验证密钥 | 无 |
| `AKILE_PUSH_KEY` | Server 酱推送 key | 无 |
| `AKILE_SESSION_DIR` | Chrome session 保存目录 | `chrome_session` |
| `RUN_SCHEDULED` | 是否启用定时模式 | `false`（Docker 镜像中为 `true`） |
| `AKILE_CHECKIN_TIME` | 每日签到时间（HH:MM） | `10:00` |
| `AKILE_RANDOM_DELAY_MINUTES` | 签到随机延迟范围（±分钟） | `5` |

## 🕐 定时任务

### Docker 默认调度

Docker 镜像默认启用定时模式，每天 `10:00` 前后在 `±5` 分钟范围内随机选择时间点尝试签到。可通过环境变量调整：

```bash
docker run -d --name akile-checkin \
  -e AKILE_CHECKIN_TIME=09:00 \
  -e AKILE_RANDOM_DELAY_MINUTES=3 \
  -v $(pwd)/config.ini:/app/config.ini \
  -v $(pwd)/chrome_session:/app/chrome_session \
  ghcr.io/bingxin666/akile-checkin:latest
```

### Linux Crontab（旧方式）

如果你仍希望通过宿主机 cron 调度，可参考：

```bash
# 每天上午 10:00 执行一次
0 10 * * * docker run --rm -v /root/Akile-checkin/config.ini:/app/config.ini -v /root/Akile-checkin/chrome_session:/app/chrome_session ghcr.io/bingxin666/akile-checkin:latest > /root/Akile-checkin/checkin.log 2>&1
```

**⚠️ 注意**：将 `/root/Akile-checkin` 替换为你的实际项目路径。

## 📝 运行日志

成功签到：
```
签到成功, 获得10个AK币, 当前有100个AK币
```

重复签到：
```
今日已签到, 现在有100AK币
```

## 📂 项目结构

```
Akile-checkin/
├── Akile-Checkin.py      # 主程序
├── notice.py             # 消息推送模块
├── config.ini.example    # 配置文件示例
├── requirements.txt      # Python 依赖
├── Dockerfile            # Docker 镜像
├── .dockerignore         # Docker 构建忽略文件
├── .gitignore            # Git 忽略文件
└── README.md             # 项目说明
```

## 📄 依赖项

```
selenium
undetected-chromedriver
requests
pyotp
```

## ⚠️ 免责声明

- 本项目仅供学习交流使用
- 请勿将本项目用于商业用途
- 使用本项目所产生的一切后果由使用者自行承担
- 请遵守 Akile.io 的用户协议和使用条款

## 📜 开源协议

本项目基于 [MIT](LICENSE) 协议开源

## 🤝 贡献

欢迎提交 Issue 和 Pull Request 来帮助改进项目！

---

<div align="center">

**如果觉得这个项目对你有帮助，欢迎 Star ⭐**

</div>
