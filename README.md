# Gatefare · Flight Monitor

在选定的**出发日期窗**内，按停留 **N–M 天**筛出往返最低价（国内 + 中国出境），支持一次性扫价与定时盯价。

产品名：**Gatefare**。技术栈：FastAPI + SQLite + React，查价管线全程免费（无付费航班 API）。

## 查价管线

```
日期窗任务
  → Travelpayouts 免费缓存价（发现候选）
  → Playwright OTA 核验（携程 → 去哪儿 → 飞猪 → Google Flights）
  → 按核验价排序 Top-10+
  → 可选飞书 Webhook 告警
```

| 情况 | 结果 |
|------|------|
| 无 `TRAVELPAYOUTS_TOKEN` / `USE_DEMO=true` | 拒绝扫价 |
| Travelpayouts 请求失败 | `failed` |
| OTA 核验全部失败 | `failed`（不用未核验缓存价冒充最优） |
| 部分核验成功 | `partial`，只展示成功项 |

**数据保真：**

- **缓存价**：仅用于初筛，不作为推荐排序依据  
- **核验价**：扫描时从 OTA 页面抓取，用于排序与告警  
- **携程 / 去哪儿 / Google 链接**：真实搜索页（日期与航线一致），下单前请再确认当前售价  

## 目录结构

```
backend/          # FastAPI 应用、扫价/核验服务、定时任务
frontend/         # React 管理界面（Vite）
tests/            # pytest
data/             # 本地 SQLite（运行时生成，不入库）
Dockerfile        # 前端构建 + 后端 + Playwright Chromium
docker-compose.yml
start_web.bat     # Windows 一键启动
.env.example      # 环境变量模板
```

## 配置

```bash
copy .env.example .env
```

必填 / 建议修改：

```env
TRAVELPAYOUTS_TOKEN=你的免费token
JWT_SECRET=请改成随机长串
ADMIN_USERNAME=admin
ADMIN_PASSWORD=请改密码
USE_DEMO=false
```

Token 获取：[Travelpayouts](https://www.travelpayouts.com/) → Programs → Aviasales → API。

## 本地启动

### 1. 后端依赖

```bash
pip install -r requirements.txt
playwright install chromium
```

### 2. 启动（二选一）

**Windows：** 双击或运行 `start_web.bat`

**命令行：**

```bash
set PYTHONPATH=backend
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

打开 http://127.0.0.1:8000 ，默认账号见 `.env`。

> 若页面是旧版 UI：先构建前端并拷贝到 `backend/static`（见下节），或用前端开发服务器。

### 3. 前端开发 / 发布静态资源

开发（热更新，代理到后端）：

```bash
cd frontend
npm install
npm run dev
```

访问 http://127.0.0.1:5173 。

发布到后端静态目录：

```bash
cd frontend
npm run build
# Windows PowerShell
Copy-Item -Recurse -Force dist\* ..\backend\static\
```

## Docker

```bash
docker compose up --build
```

镜像内会安装 Playwright Chromium；需在 `.env` 中配置 `TRAVELPAYOUTS_TOKEN`。

### 公开模式（博客访客免登录）

在 `.env` 设置：

```env
PUBLIC_MODE=true
MAX_CONCURRENT_SCANS=1
```

效果：无登录墙、无 admin/账户页；用 guest Cookie 隔离访客任务；禁止定时盯价；按 IP 限流扫价。

阿里云 ECS 部署步骤见 [DEPLOY_ALIYUN.md](./DEPLOY_ALIYUN.md)。

## 使用流程

1. 确认 `/api/health` 为就绪（公开模式顶栏显示「公开检索」）  
2. **任务板**新建航线并扫描  
3. **详情页**看实时进度与核验价榜；点击携程 / 去哪儿 / Google 跳转核对  
4. 非公开模式才可开启定时盯价与飞书 Webhook  

扫价较慢（缓存扫窗 + 多组 OTA 核验属正常）；界面会持续输出阶段与已核验结果。

## 测试

```bash
set PYTHONPATH=backend
python -m pytest tests/ -q
```

## 文档

| 文档 | 用途 |
|------|------|
| [PRD.md](./PRD.md) | 产品需求与验收标准 |
| [CHANGELOG.md](./CHANGELOG.md) | 更新日志（功能改动必记） |
| [AGENTS.md](./AGENTS.md) | 代码代理开发 / 验收规范 |

## 说明

- 携程对自动化浏览器常返回 WhaleGuard；**真实用户 Chrome 打开深链通常可用**，核验阶段会自动回退 Google Flights。  
- 生产环境请务必修改 `JWT_SECRET` 与管理员密码，且勿将 `.env` 提交到 Git。  
- 协作规范：先读 `AGENTS.md`（含完整开发方法论）。凡有意义改动，必须先同步 `CHANGELOG.md` 再回复用户（未写不算完成）。
