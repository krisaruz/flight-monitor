# Changelog

本文件记录 Gatefare / Flight Monitor 的**用户可感知功能**与**接口/数据行为**变更。  
格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，按时间**倒序**。

代理（Cursor / Claude / Codex 等）在完成功能改动后**必须**先读系统日期，再把条目追加到本文件顶部对应日期下；禁止只改代码不写日志。

变更类型标签：`Added` / `Changed` / `Fixed` / `Removed` / `Security`

---

## 2026-07-27

### Removed

- 本地过程产物：Story Nav 迭代截图（`backups/`、`frontend/backups/`）、仓库内 agent skill 缓存（`.agents/`、`skills-lock.json`）；并写入 `.gitignore` 防止再入库。

### Changed

- **Story Nav 文案定稿（软安心）**：采用用户选定组合——「行程还在酝酿，完全正常」「大概那两周就可以开工」「最合适的机场也不用你先猜」「找到了再确认是真价」「设好区间先去做别的事」「一点点意向就够出发」；CTA「帮我盯着」。卖点：模糊区间 + 出行想法 → 盯最便宜那一程。
- **Story Nav 文案对齐卖点 / 口语重写**：叙事主轴为模糊区间与出行意向（承接上条定稿）。
- **Story Nav 全文中文**：分镜标题、副文、按钮、核验状态、通知、下滑提示与进度点文案改为中文（品牌/OTA 专名保留）。
- **Story Nav 分镜差异化**：每幕独立主题色与舞台质感（日期暖绿 / 日期窗青绿图表 / 机场琥珀卡 / 核验终端绿 / 通知冷蓝 / 终幕高对比），并随幕切换背景氛围，避免六幕同一模板感。
- **Story Nav 统一栅格重排**：参考 Linear/Raycast 产品段，全部分镜共用居中 max-width 外框与 gutter；`rail | stage` 顶对齐；舞台保留最小高度（修复机场等矮组件悬浮错位）；Hero 为 stage|rail 翻转；终幕同外框。
- **Story Nav 编辑式排版**：编号 eyebrow + Playfair 斜体强调 + mono 注解；通知手机框；slate 底 + 环境光。
- **Story Nav 视觉加厚**：机场幕「国家卡片 → 机场网格」；终幕能力复述与次级 Sign in。
- **Story Nav Premium 电影感**：轻滚动视差、景深入场、stagger、玻璃通知层；PRD 允许克制视差/景深；`prefers-reduced-motion` 关闭循环与景深。
- **Story Nav 动效质量**：分意图缓动；Hero 加权比较；拖拽零延迟；核验参差时序；下滑提示有限次后静止；分镜 `content-visibility` / IO `once`。

### Added

- **电影式公开导航页（Story Nav）**：未登录访问 `/` 进入滚动分镜交互演示（日历选日、拖日期窗、点国家展开机场、OTA 核验仪表、触价通知、最终 CTA），用动画解释「自动找到最便宜出行日期」；已登录 `/` 仍为任务板。CTA / Sign in → `/login`。演示数据仅为教学示意。
- **Story Nav 动效与引导**：价格翻牌、日历扫描/定格、分镜入场与侧边进度点；首屏底部下滑提示（滚动后淡出）。
- **Story Nav Hero 修复**：主标题（h1）立即可见；比较→定格与洞察同帧；约 2.4s 内出单一主 CTA；Lowest 文字标签 + aria-live；Skip animation；示意价声明；下滑提示在主 CTA 出现前引导、出现后收敛。
- **OTA 核验多源回落**：Playwright 顺序改为 **携程 → 去哪儿 → 飞猪 → Google Flights**；任一家解析到真价即写入 `verified_price`（`source` 分别为 `CtripVerified` / `QunarVerified` / `FliggyVerified` / `GoogleFlightsVerified`）。
- 飞猪往返深链生成：`fliggy_round_trip_url`（结果页/核验共用城市码）。
- 配置项 `OTA_VERIFY_HEADLESS`（默认 `true`）：改为 `false` 时用有头 Chrome/Chromium，部分环境可降低反爬命中（会弹窗）。

### Changed

- **携程核验抗干扰**：去掉 `--enable-automation`、加强 stealth 脚本、`Asia/Shanghai` 时区、先访问航班首页暖场再进列表、轻量鼠标滚动；硬拦截时本轮跳过该源。
- OTA 价解析：下限 ¥450，优先接口 JSON 价，拒绝营销条噪声；单源导航异常不再中断整条回落链。
- **PRD v1.4**：同步多平台核验顺序与反爬预期（不承诺单源零拦截）。

---

## 2026-07-26

### Changed

- **PRD v1.3**：补全权威产品链路说明——Travelpayouts 缓存发现与 Playwright 核验分阶段职责；明确携程与 Google Flights **均由 Playwright 打开页面**（无独立 Google API）；核验池、双价语义（缓存仅对照、排序用核验价）、WhaleGuard 后 Google 回落为正式路径、时序图、API/数据摘要与验收细则。便于对照实现与排障，无运行时行为变更。

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
