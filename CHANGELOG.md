# Changelog

本文件记录 Gatefare / Flight Monitor 的**用户可感知功能**与**接口/数据行为**变更。  
格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，按时间**倒序**。

代理（Cursor / Claude / Codex 等）在完成功能改动后**必须**先读系统日期，再把条目追加到本文件顶部对应日期下；禁止只改代码不写日志。

变更类型标签：`Added` / `Changed` / `Fixed` / `Removed` / `Security`

---

## 2026-07-24

### Added

- **地点自动完成**：新建任务时出发地/目的地支持搜索城市、机场三字码或国家；可选「国家 · 不限机场」，将该国主要机场一并纳入扫价（如柬埔寨 → PNH/REP/KOS）。
- API `GET /api/places/suggest`：地点建议列表。
- 任务字段 `origin_codes` / `dest_codes`：支持多机场逗号分隔；扫价按 OD 组合发现并合并核验结果。
- 扫价实时进度：`progress_message`、核验中途写入结果、详情页 Live 日志与票价卡片流式展示。
- 登录页 **DepartureBoard / SplitFlap** 航班信息牌动效与分栏布局；任务板加载骨架等界面组件。
- 项目文档：`CHANGELOG.md`、`PRD.md`、`AGENTS.md`（Gatefare 代理规范）。

### Changed

- 深链生成：携程使用城市码 + `retdate`；去哪儿改官网带参首页（中文城市名）；结果页强调数据保真说明。
- Travelpayouts HTTP：**默认直连**，避免 Windows 残留本地代理（如 `127.0.0.1:7897`）未启动时出现 WinError 10061；需要代理时显式设置 `HTTPS_PROXY` / `TRAVELPAYOUTS_PROXY`。
- 前端视觉：Gatefare 深色航班信息风格（顶栏、卡片光效、健康状态文案等）。
- 仓库清理：移除临时候选探测脚本、过时 CLI 与调试产物；README 按混合管线重写。
- `AGENTS.md`：Gatefare 产品约束 + 完整代理开发方法论（需求/PRD/变更/复用/UI/IME/测试/安全/交付等）；**Changelog 同步**列为交付硬门槛与固定工作流卡点（未写不得声称完成）。
- Cursor 规则 `changelog-and-docs.mdc`：强化「先写 Changelog 再回复用户」。

### Fixed

- 出发地/目的地搜索缺大量国内城市（如珠海）：改为内置 Travelpayouts **中国大陆全量可飞城市**目录（220+），支持中文名与英文别名；「中国大陆（不限机场）」仍只扩主要枢纽，避免扫价组合爆炸。可用 `scripts/refresh_cn_cities.py` 刷新目录。
- 去哪儿旧 `round_list.htm` 跳转 404，导致核对链接不可用。
- 携程深链误用 `arrdate` / 机场码（如 KIX）导致列表页异常的问题（改为城市码 + `retdate`）。

---

## 2026-07-23

### Added

- **混合查价管线**：Travelpayouts 免费缓存发现 → Playwright OTA 核验（携程优先，WhaleGuard 则 Google Flights）→ 按核验价排序 Top-10+。
- Fail-closed：无 token / `USE_DEMO` / TP 失败 / 核验全失败时不得静默降级为深链板或演示价。
- FastAPI + React（Gatefare）Web：任务、详情、账户飞书 Webhook、健康检查、定时盯价。
- Docker 镜像内安装 Playwright Chromium。

### Removed

- 生产路径上「仅深链即成功」「demo 冒充扫价」等降级逻辑。
