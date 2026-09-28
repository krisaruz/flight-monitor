# Gatefare 部署到阿里云 ECS（博客公开检索）

适用配置：**2 核 / 2G 内存 / 40G 盘**（活动机）。公开模式免登录，供 krisnote.online 访客使用。

## 0. 你需要准备

| 项 | 说明 |
|----|------|
| 服务器公网 IP | 阿里云 ECS 控制台可见 |
| SSH 登录 | 购买时设置的密码或密钥 |
| `TRAVELPAYOUTS_TOKEN` | 本地 `E:\new_project\flight-monitor\.env` 里已有，**不要提交到 Git** |
| 安全组 | 放行 **TCP 8000**（或 80/443 若你前面加了 Nginx） |

## 1. 登录服务器并装 Docker

```bash
ssh root@你的公网IP
```

Alibaba Cloud Linux / CentOS 示例：

```bash
yum install -y yum-utils
yum-config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
yum install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
systemctl enable --now docker
```

Ubuntu 可用官方文档：https://docs.docker.com/engine/install/ubuntu/

加一点 swap（2G 内存强烈建议）：

```bash
fallocate -l 2G /swapfile
chmod 600 /swapfile
mkswap /swapfile
swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab
```

## 2. 拉取代码

```bash
cd /opt
git clone https://github.com/krisaruz/flight-monitor.git
cd flight-monitor
```

若仓库尚未推送公开模式改动，可先在本机推送后再 `git pull`，或用 `scp` 把整个目录拷上去。

## 3. 配置环境变量

```bash
cp .env.example .env
nano .env   # 或 vi
```

最少改这些：

```env
JWT_SECRET=换成一长串随机字符
TRAVELPAYOUTS_TOKEN=你的真实 token
USE_DEMO=false
PUBLIC_MODE=true
MAX_CONCURRENT_SCANS=1
PUBLIC_SCAN_LIMIT_PER_HOUR=5
# 站长本机公网 IP（逗号分隔）；白名单不受每小时次数限制
# PUBLIC_SCAN_IP_WHITELIST=你的家宽公网IP
CORS_ORIGINS=https://www.krisnote.online,https://krisnote.online,http://你的公网IP:8000
```

## 4. 构建并启动

```bash
docker compose up -d --build
```

查看日志：

```bash
docker compose logs -f --tail=100
```

健康检查：

```bash
curl -s http://127.0.0.1:8000/api/health
```

期望 `"ok": true`，且 `"public_mode": true`。

浏览器打开：`http://你的公网IP:8000/`  
应直接进入任务板，**无登录页**。

## 5. 阿里云安全组

ECS → 安全组 → 入方向添加：

- 协议：TCP
- 端口：8000
- 源：0.0.0.0/0（或先仅自己 IP 测试）

## 6. 接到博客

在博客 Vercel 环境变量（或本地 `.env.local`）设置：

```env
NEXT_PUBLIC_FLIGHTS_URL=http://你的公网IP:8000
```

博客已提供：

- 首页 Hero「机票查询」→ `/flights`
- `/flights` → 302 跳转到上述地址
- 项目卡 Flight Monitor 同源

改完后重新部署博客（push 或 Vercel Redeploy）。

## 7. （可选）域名 + HTTPS

有域名时，把 `flights.krisnote.online` A 记录指到 ECS IP，用 Nginx/Caddy 反代到 `127.0.0.1:8000` 并开 HTTPS。  
然后把 `NEXT_PUBLIC_FLIGHTS_URL` 改成 `https://flights.krisnote.online`。

## 运维备忘

```bash
# 更新代码后重建
cd /opt/flight-monitor && git pull && docker compose up -d --build

# 清理无用镜像（盘只有 40G）
docker system prune -af

# 重启
docker compose restart
```

## 资源注意（2G 机）

- 务必 `MAX_CONCURRENT_SCANS=1`
- 务必开 swap
- 扫价时内存会冲高，属正常；若 OOM，再降 `VERIFY_TOP_K=10`
