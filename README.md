# VIX Strategy Docker App

一个可直接跑在 Docker 中的 Python 策略服务，支持：
- **原版策略**
- **高频版策略**
- **每天收盘后跑一次**（默认 daily 模式）
- **买入 / 卖出信号邮件通知**
- 本地 `state.json` 去重，避免重复发同一信号

## 策略逻辑

### 原版策略
**买入条件**
- VIX 单日涨幅 > 9%，或
- VIX > 10 日均线的 110%，或
- VIX 突破 20 日布林上轨
- 且 S&P 500 RSI(14) < 35
- 且 VIX > 25

**卖出条件**
- VIX <= 20 日均线，或
- VIX 单日跌幅 <= -9%，或
- VIX 跌破 20 日布林下轨，或
- VIX < 20

### 高频版策略
**买入条件**
- VIX 单日涨幅 > 6%，或
- VIX 突破 20 日布林上轨
- 且 S&P 500 RSI(14) < 45
- 且 VIX > 20

**卖出条件**
- VIX <= 10 日均线，或
- VIX 单日跌幅 <= -6%，或
- VIX 跌破 20 日布林下轨，或
- VIX < 20

---

## 数据源
当前默认数据源是 **yfinance**：
- `^VIX`：VIX 指数
- `^GSPC`：标普 500 指数
- `SPY` / `QQQ`：交易标的

### 这意味着什么？
- 我已经把**数据接口接好了**，程序现在开箱可跑
- 但它不是付费实时行情终端，而是 **Yahoo Finance 数据源**
- 更准确地说：它适合 **日线收盘后策略**，不适合毫秒级/盘口级实盘

所以对于你这个策略，**每天收盘后运行一次是合适的**。

---

## 文件
- `app.py`：主程序
- `requirements.txt`：依赖
- `Dockerfile`：Docker 镜像构建
- `docker-compose.yml`：运行配置
- `.env.example`：SMTP 和策略参数示例

## 快速开始

### 1. 复制环境变量
```bash
cp .env.example .env
```

### 2. 填写邮件参数
至少要填：
- `SMTP_HOST`
- `SMTP_PORT`
- `SMTP_USERNAME`
- `SMTP_PASSWORD`
- `EMAIL_FROM`
- `EMAIL_TO`

### 3. 设置运行时间
默认：
```env
RUN_MODE=daily
RUN_TIME_UTC=22:30
```

这表示容器每天在 **22:30 UTC** 跑一次。

### 4. 构建并运行
```bash
docker compose up --build -d
```

---

## 关键环境变量
- `STRATEGY_MODE=original` 或 `highfreq`
- `TRADE_SYMBOL=SPY` 或 `QQQ`
- `RUN_MODE=daily` 或 `once`
- `RUN_TIME_UTC=22:30`
- `EMAIL_TO=your@email.com`

---

## 运行模式
### daily
常驻容器，每天到指定 UTC 时间执行一次。

### once
容器启动后只运行一次，然后退出。
适合配合宿主机 cron / Kubernetes CronJob。

---

## 说明
- 邮件只在**新 BUY / SELL 信号**出现时发送
- `HOLD` 不发邮件
- 默认用 `state.json` 去重
- 如需同时监控多组（例如 `SPY-original`、`QQQ-highfreq`），建议起多个容器实例
