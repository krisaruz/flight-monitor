# Changelog

本文件记录 Gatefare / Flight Monitor 的**用户可感知功能**与**接口/数据行为**变更。  
格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，按时间**倒序**。

代理（Cursor / Claude / Codex 等）在完成功能改动后**必须**先读系统日期，再把条目追加到本文件顶部对应日期下；禁止只改代码不写日志。

变更类型标签：`Added` / `Changed` / `Fixed` / `Removed` / `Security`

---

## 2026-09-28

### Added

- **公开模式代码入库（迁移固化）**：线上 ECS 实际运行的公开模式（public_mode）全量代码此前仅存于本机工作区（热补丁机制导致仓库与线上脱节），本次整体提交入库——`public_mode`/日历匹配混合管线（`calendar_match.py`）、公开扫价限流（`rate_limit.py`，每 IP 每小时上限 + 白名单）、`Dockerfile.public`（Chrome+xvfb 公开云镜像）、`docker-compose.public.yml`（WARP+autoheal 生产栈）、`DEPLOY_ALIYUN.md` 部署文档、前端字体栈（`fonts.css`）、相关测试 4 个（calendar_match / google_airport / rate_limit / scanner_calendar_fallback）。行为与线上现状一致，无新变更。

---

## 2026-09-02

### Added

- **WARP 自愈（autoheal）**：生产栈新增 `willfarrell/autoheal` 容器，`warp-proxy` 打 `autoheal=true` 标签并补 healthcheck（SOCKS5h 走 `www.google.com`，60s/5 次）。WARP 隧道断连但进程存活时（Docker restart 策略失效的死局），healthcheck 转 unhealthy 后 autoheal 自动重启容器恢复隧道。已在 ECS 实战验证一轮自动恢复。

### Fixed

- **OTA 核验连续失败（ERR_SOCKS_CONNECTION_FAILED）**：根因是 8-27 起 WARP 隧道断连且无人自愈，静默故障 5.6 天；手动重启 warp 恢复，并以上述 autoheal 方案防复发。任务 #22 复扫通过（HKG→NGO 最低核验价 CNY 1,987）。

---

## 2026-07-30

### Added

- **公开扫价 IP 白名单**：配置 `PUBLIC_SCAN_IP_WHITELIST`（逗号分隔）；命中后不受「每 IP 每小时 N 次」限制。本机 `127.0.0.1` / `::1` 始终放行。

### Fixed

- **国内稀航线发现阶段硬失败**：TP 日历无缓存且 Google 单程补洞凑不出「去+回都有价」组合时（如 ZUH→DAT），国内航线改为均匀日期骨架直进 OTA 核验，不再在发现阶段直接报「没有日历价组合」；进度提示「缓存/补洞无双边组合 · 国内均匀日期直核验」。国际航线仍不默认开骨架。
- **空候选错误文案**：仍失败时附带「去程有价 a/b、回程有价 c/d、匹配 n」，便于判断是数据空洞还是匹配失败。

### Changed

- **PRD v1.9**：核验池组装明确日历匹配优先、国内空池骨架回落、国际不默认骨架。
- **健康检查 hint**：管线说明改为「双边有价匹配（国内空池均匀骨架直核验）」。

---

## 2026-07-28

### Changed

- **代理部署约定**：默认线上改动先热补丁（`patch/` bind mount + recreate）立刻验收；每次改完须询问是否重建 `gatefare:public` 镜像以免 patch 堆积。见 `AGENTS.md` §16、`.cursor/rules/aliyun-patch-deploy.mdc`、`DEPLOY_ALIYUN.md`。
- **缓存发现再提速**：默认 `TRAVELPAYOUTS_CONCURRENCY=6`、`TRAVELPAYOUTS_REQUEST_DELAY=0.2s`（相对此前 4×0.35；仍远低于官方 prices_for_dates≈600/min；限流提示与退避不变）。
- **Google 核验加速**：打开后等价格符号即可继续（约 8s 超时），不再空等 `networkidle`；缩短价格/回程轮询与点选等待；候选间隔默认 `CTRIP_VERIFY_DELAY_SEC=0.6`（仍串行单浏览器，适配 2G 机）。
- **缓存发现加速**（同日早先）：同一 OD 内日期组合有限并行；HTTP 429 写入可见进度「缓存限流」。

### Added

- 配置项 `TRAVELPAYOUTS_CONCURRENCY`（1–16）；`.env.example` 补充发现阶段并发与间隔、核验间隔说明。

---

## 2026-07-27

### Removed

- 本地过程产物：Story Nav 迭代截图（`backups/`、`frontend/backups/`）、仓库内 agent skill 缓存（`.agents/`、`skills-lock.json`）；并写入 `.gitignore` 防止再入库。

### Changed

- **Story Nav 入场动画升级**：六幕统一为「航站调度接力」体验；新增随滚动点亮的航线、`GF-01` 至 `GF-06` 当前幕读数、分层景深入场与一次性舞台扫描，强化场景衔接且不增加持续干扰。
- **Story Nav 响应式与无障碍动效**：窄屏隐藏装饰航线并保留幕编号；减少动态效果偏好下关闭航线描绘、扫描和景深位移，正文、交互与 CTA 保持完整可用。
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

### Fixed

- **Story Nav 可访问入口**：将最新前端构建同步到 `backend/static`，修复后端 `8000` 仍提供旧静态包、访问 `/story` 无法看到动画的问题；本地正式入口为 `http://127.0.0.1:8000/story`，`5173/story` 仅在 Vite 开发服务运行时可用。

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
