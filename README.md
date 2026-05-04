# ✈️ Flight Monitor — 机票价格监控 + 飞书机器人

监控指定航线在日期范围内的往返机票价格，按价格从低到高排序。支持 **命令行查价** 和 **飞书机器人** 两种使用方式。

## 功能特性

- **日期范围扫描** — 自动枚举日期范围内所有出发日，搜索往返组合
- **价格排序** — 按总价从低到高排列，一眼找到最便宜的机票
- **飞书机器人** — 在群聊/私聊中 @机器人 发送自然语言指令即可查价
- **交互式卡片** — 搜索结果以飞书消息卡片形式展示
- **群 Webhook 推送** — 支持定时推送机票信息到飞书群
- **演示模式** — 无需 API 密钥，使用模拟数据体验全部功能

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

运行单元测试（可选）：

```bash
pip install pytest
python -m pytest tests/ -q
```

将 `.env.example` 复制为 `.env` 并填写飞书与（可选）Amadeus 凭证。程序启动时会通过 `python-dotenv` 自动加载项目目录下的 `.env`。

**运维相关环境变量**：`AMADEUS_REQUEST_DELAY`（请求间隔，默认 0.15）、`BOT_MAX_CONCURRENT_SEARCHES`（机器人并发搜索数，默认 2）。

### 2. 命令行使用

```bash
# 演示模式（无需 API 密钥）
python flight_monitor.py --demo

# 指定航线和日期
python flight_monitor.py --origin 香港 --dest 大阪 --start 2026-05-01 --end 2026-06-30 --days 5

# 保存结果
python flight_monitor.py --demo --save results.json
```

### 3. 飞书机器人

```bash
# 凭证放在 .env，或直接 export / set
set FEISHU_APP_ID=cli_xxxx
set FEISHU_APP_SECRET=your_secret

# 启动机器人：若 .env 中未配置 Amadeus，会自动使用演示数据；配置了 Amadeus 则查真实票价
python feishu_flight_bot.py

# 显式仅用演示数据（即使配置了 Amadeus）
python feishu_flight_bot.py --demo

# Amadeus 生产环境 + 每日最多 5 条报价
python feishu_flight_bot.py --production --max-per-date 5
```

在飞书中 @机器人 发送：
- `搜机票 香港到大阪 5月 5天`
- `查航班 深圳到东京 4月20到6月30 7天`
- `搜 香港到曼谷 五一 4天`
- `帮助`

## 配置说明

复制 `.env.example` 为 `.env` 并填入你的凭证：

```bash
copy .env.example .env
```

| 变量 | 必填 | 说明 |
|------|------|------|
| `FEISHU_APP_ID` | 飞书机器人必填 | 飞书开放平台 App ID |
| `FEISHU_APP_SECRET` | 飞书机器人必填 | 飞书开放平台 App Secret |
| `FEISHU_WEBHOOK` | Webhook 推送时需要 | 飞书群机器人 Webhook 地址 |
| `AMADEUS_CLIENT_ID` | 真实数据时需要 | [Amadeus API](https://developers.amadeus.com/register) Client ID |
| `AMADEUS_CLIENT_SECRET` | 真实数据时需要 | Amadeus API Client Secret |

### 飞书应用配置

1. 前往 [飞书开放平台](https://open.feishu.cn/app) 创建企业自建应用
2. 添加「机器人」能力
3. 权限管理 → 添加：`im:message`、`im:message:send_as_bot`
4. 事件与回调 → 订阅方式选「**使用长连接接收事件**」→ 添加 `im.message.receive_v1`
5. 发布版本并审批

### Amadeus API 注册

免费注册：https://developers.amadeus.com/register

默认使用测试环境，如需生产数据加 `--production` 参数。

## 文件说明

| 文件 | 说明 |
|------|------|
| `flight_monitor.py` | 核心模块：数据模型 + Amadeus API + CLI |
| `feishu_flight_bot.py` | 飞书机器人：WebSocket 长连接 + 自然语言解析 + 卡片消息 |
| `requirements.txt` | Python 依赖 |
| `start_bot.bat` | Windows 守护启动脚本（崩溃自动重启） |
| `push_to_group.py` / `push_to_group.bat` | 一次性搜索并通过 Webhook 推送到飞书群 |
| `install_autostart.bat` | 设置 Windows 开机自启 |
| `.env.example` | 环境变量配置模板 |

## 支持的城市

| 城市 | IATA | 城市 | IATA | 城市 | IATA |
|------|------|------|------|------|------|
| 香港 | HKG | 大阪 | KIX | 东京 | NRT |
| 首尔 | ICN | 曼谷 | BKK | 新加坡 | SIN |
| 台北 | TPE | 上海 | PVG | 北京 | PEK |
| 广州 | CAN | 深圳 | SZX | 名古屋 | NGO |
| 福冈 | FUK | 札幌 | CTS | 冲绳 | OKA |
| 成都 | CTU | 杭州 | HGH | 武汉 | WUH |

也可以直接使用任意 IATA 三字码。

## 命令行参数

```
python flight_monitor.py --help

  --origin, -o    出发城市/机场代码 (默认: HKG)
  --dest, -d      目的城市/机场代码 (默认: KIX)
  --start, -s     搜索起始日期 YYYY-MM-DD (默认: 明天)
  --end, -e       搜索结束日期 YYYY-MM-DD (默认: 起始+60天)
  --days, -n      往返天数 (默认: 5)
  --adults        成人数量 (默认: 1)
  --currency      货币代码 (默认: CNY)
  --cabin         舱位: ECONOMY / PREMIUM_ECONOMY / BUSINESS / FIRST
  --top           显示前N个最便宜结果 (默认: 30)
  --save          保存结果到 JSON 文件
  --production    使用 Amadeus 生产环境
  --demo          演示模式，使用模拟数据
```

## License

MIT
