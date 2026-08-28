# QLU 抢课服务器（多用户网页版）

把「教务抢课脚本.py」的发包逻辑做成**常驻 Web 服务**：注册账号 → 网页里填自己的
JSESSIONID 和课程参数 → 一键抢课，实时看日志。多用户隔离，适合宿舍/小圈子共建一台服务器。

```
浏览器(控制台) ──HTTP──▶ FastAPI 服务(Linux 服务器)
                            ├─ 多用户注册/登录（口令哈希存储）
                            ├─ 每人独立：配置/任务/日志（互不可见）
                            ├─ 抢课引擎（并发 + 间隔抖动 + 成功即停 + 定时启动）
                            └─▶ 教务服务器 jw.qlu.edu.cn（发包）
```

## 目录结构

```
web_server/
├── app.py              # 服务主程序（FastAPI 单文件，含引擎与多用户认证）
├── static/index.html   # 网页控制台（登录/注册 + 抢课面板）
├── requirements.txt
├── Dockerfile
├── .gitignore          # data/ 含用户账号与 cookie，切勿提交
└── tests/              # 本地冒烟测试（打 mock，不打真实教务）
```

## 快速开始（本地）

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt      # Windows: .venv\Scripts\pip
.venv/bin/python app.py --host 0.0.0.0 --port 8000
```

打开 `http://服务器IP:8000` → 注册第一个账号即可用。

## Linux 服务器部署

### 方式一：Docker（推荐）

```bash
cd web_server
docker build -t qlu-server .
docker run -d --name qlu-server --restart unless-stopped \
  -p 8000:8000 -v $(pwd)/data:/data qlu-server
```

或 docker-compose：

```yaml
services:
  qlu-server:
    build: .
    container_name: qlu-server
    restart: unless-stopped
    ports:
      - "8000:8000"
    environment:
      - QLU_DATA=/data
    volumes:
      - ./data:/data   # 用户与配置持久化
```

### 方式二：systemd（裸机/虚拟机）

```bash
# 把本目录放到 /opt/qlu-server，创建 venv 并安装依赖后：
sudo tee /etc/systemd/system/qlu-server.service > /dev/null <<'EOF'
[Unit]
Description=QLU Course Grab Server
After=network.target

[Service]
WorkingDirectory=/opt/qlu-server
ExecStart=/opt/qlu-server/.venv/bin/python app.py --host 127.0.0.1 --port 8000
Restart=always
RestartSec=3
Environment=QLU_DATA=/opt/qlu-server/data

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now qlu-server
journalctl -u qlu-server -f   # 看日志
```

### 方式三：校园网限制下的部署（frp 内网穿透）

> **适用场景**：教务系统仅校园网可访问（例如齐鲁工大 `jw.qlu.edu.cn` 现在只在内网
> 可达，校外需通过学校 VPN——深信服 aTrust——才能访问）。此时云服务器无法直连教务，
> 需要把「抢课请求的出口」放到一台能访问教务的机器上，再用 frp 把服务穿回公网。

```
浏览器 ──▶ https://域名 → 公网服务器 nginx → 127.0.0.1:8010
                                  └─ frps(7000) ←frp隧道← 校园机 frpc
                                                         → 校园机 8010(本服务)
                                                         → 校园网/学校VPN → jw.qlu.edu.cn ✅
```

#### 前置条件

- 一台能访问教务的**常开电脑**：在校园网内，或装有学校 VPN 客户端（如 aTrust）并已登录
- 一台有公网 IP 的服务器（跑 frps + nginx；域名可选，IP:8010 也能用）

#### ① 公网服务器：跑 frps

```bash
cd /opt
wget https://github.com/fatedier/frp/releases/download/v0.61.1/frp_0.61.1_linux_amd64.tar.gz
tar xzf frp_0.61.1_linux_amd64.tar.gz && cd frp_0.61.1_linux_amd64

cat > frps.toml <<'EOF'
bindPort = 7000
auth.method = "token"
auth.token = "换成你自己的强密码"
EOF

nohup ./frps -c frps.toml > frps.log 2>&1 &
```

防火墙/安全组放行 **7000** 端口（仅 frp 认证用，别对公网开放其他多余端口）。

#### ② 校园机：跑本服务 + frpc

先确认这台电脑的浏览器能打开教务（校园网直连或已登录学校 VPN）。

```powershell
# 启动本服务
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\python app.py --host 127.0.0.1 --port 8010
```

下载 frpc（**版本必须与服务器端一致**）：
`https://github.com/fatedier/frp/releases/download/v0.61.1/frp_0.61.1_windows_amd64.zip`

`frpc.toml`：

```toml
serverAddr = "公网服务器IP"
serverPort = 7000
auth.token = "和 frps.toml 相同的强密码"

[[proxies]]
name = "qlu-web"
type = "tcp"
localIP = "127.0.0.1"
localPort = 8010
remotePort = 8010
```

```powershell
.\frpc.exe -c frpc.toml
```

#### ③ 数据迁移

数据跟着服务走：把旧服务器 `data/` 目录（`users.json` + `configs/`）复制到校园机的
`web_server/data/`，老账号无需重新注册。

#### ④ 验证

```bash
# 公网服务器上执行，200 说明隧道通了、页面实际从校园机发出
curl -I http://127.0.0.1:8010/
```

浏览器打开 `https://你的域名`（nginx 反代 `http://127.0.0.1:8010`），老账号能登录即成功。

#### 注意事项

1. 校园机**禁止休眠**（电源设置 → 睡眠改为"从不"）；用任务计划程序设置开机自启
   `frpc` 与本服务，aTrust 类 VPN 客户端开启自动登录/自动重连。
2. aTrust 只有 Windows/macOS/移动端/统信·麒麟版客户端，**没有通用 Linux 版**，
   云服务器无头安装不可行——这正是本方案存在的原因。
3. 学校 VPN 是资源级放行：确认 `jw.qlu.edu.cn` 在资源列表里（平时能在校外打开
   教务页面即说明已放行）。

## 安全建议（多人共用服务器必看）

1. **公网/跨网段必须套 HTTPS**，否则 cookie 会在传输中被抓。推荐 Caddy 一行搞定：
   `caddy reverse-proxy --from 抢课.example.com --to 127.0.0.1:8000`
   （或 Nginx + certbot）。只给自己人用的话，也可以直接防火墙白名单 IP。
2. **密码**：服务端存的是 PBKDF2 加盐哈希，不是明文；但你填进去的 **JSESSIONID 以明文存在服务器的 `data/configs/<用户名>.json` 里**——确保服务器只有你们可信的人能登录。
3. **速率**：Web 面板默认间隔 ≥30ms、并发上限 8，但仍建议间隔 ≥100ms、并发 1-3，
   避免把自己的账号或学校系统搞崩（旧脚本"页面崩溃"很大程度就是刷出来的）。
4. **cookie 会过期**：教务会话通常几小时到一天，过期后重新抓 JSESSIONID 填进去即可。

## 使用流程

1. 浏览器登录教务选课页 → F12 → Network → 点选课请求，复制：
   - 请求头 `Cookie` 里的 `JSESSIONID`
   - 表单里的 `jxb_ids / kch_id / xkkz_id / njdm_id / zyh_id / kklxdm`（选课与查询用**同一个**教学班）
2. 网页里填进去 → 点「测试连接」验证 cookie 是否有效 → 保存。
3. 设置策略（间隔、并发、成功即停、定时启动）→ 「开始抢课」。
4. 日志实时滚动，抢到后自动停止（可在网页关掉"成功即停"继续捡漏）。

## 参数说明

| 参数 | 含义 | 来源 |
|---|---|---|
| cookie | JSESSIONID | 抓包请求头 |
| xkxnm / xkxqm | 学年 / 学期 | 同一请求表单 |
| xkkz_id | 选课批次控制号 | 同一请求表单 |
| njdm_id / zyh_id | 年级 / 专业号 | 同一请求表单 |
| kklxdm | 课程类别（10=通识等） | 同一请求表单 |
| kch_id | 课程号 | 同一请求表单 |
| jxb_ids | 教学班加密ID（128位长串） | 同一请求表单 |
| bj | 班级（状态查询用） | 查询请求表单 |

## 打包成 Windows EXE（可选形态）

```bash
pip install pyinstaller
pyinstaller -F -n QLU抢课服务 --add-data "static;static" app.py
```

EXE 启动会自动打开浏览器到本机服务，数据存同目录 `data/`（单机自用形态）。

## 测试

```bash
.venv/bin/python tests/mock_jw.py          # 终端1：本地模拟教务(8124)
.venv/bin/python app.py --host 127.0.0.1 --port 8123   # 终端2：服务
.venv/bin/python tests/mock_success.py    # 终端3：模拟"返回成功"(8125)
.venv/bin/python tests/smoke_test.py      # 终端4：多用户/启停/隔离 25 项用例
.venv/bin/python tests/success_stop_test.py  # 成功即停用例
```

所有测试只打本地 mock，**不会**向真实教务发送任何请求。

## 已知边界

- 油猴脚本（`qlufz.js`）仍需在浏览器里用，它和本服务是互补的两种抢法；服务器版本质
  是原 Python 发包脚本的常驻化，前端改版/封接口时两者可能同时失效。
- 服务端没有验证码/滑块处理能力；若教务上滑块，此方案失效，请回到浏览器。
- 仅供本人账号合规使用，请勿超高频打本校服务器。