# -*- coding: utf-8 -*-
"""多用户版冒烟测试：注册/登录/启动/停止/日志/用户隔离（对本地 mock 教务验证）。"""
import sys
import time

import requests

B = "http://127.0.0.1:8123"


def post(path, body=None, token=None):
    h = {}
    if token:
        h["Authorization"] = "Bearer " + token
    r = requests.post(B + path, json=body or {}, headers=h, timeout=10)
    return r


def get(path, token=None):
    h = {}
    if token:
        h["Authorization"] = "Bearer " + token
    return requests.get(B + path, headers=h, timeout=10)


def check(name, cond, extra=""):
    global FAILED
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  {extra}" if extra else ""))
    if not cond:
        FAILED = True


FAILED = False
cfg = {
    "base_url": "http://127.0.0.1:8124",   # 指向本地 mock，不打真实教务
    "cookie": "TESTCOOKIE",
    "jxb_ids": "mock_jxb_abc",
    "kch_id": "K001",
    "kcmc": "(测试)自动化课程 - 1.0 学分",
    "xkkz_id": "X1", "njdm_id": "2022", "zyh_id": "Z1", "kklxdm": "10",
    "xkxnm": "2025", "xkxqm": "3", "bj": "7",
    "interval_ms": 60, "concurrency": 1, "timeout": 5,
    "stop_on_success": False, "query_each_round": True, "start_at": "",
}

# 1. 首页
r = get("/")
check("首页可访问(含控制台)", r.status_code == 200 and "抢课" in r.text)

# 2. 注册 / 登录
r = post("/api/register", {"username": "alice", "password": "123456"})
check("注册 alice", r.status_code == 200, r.text[:120])
r = post("/api/register", {"username": "bob", "password": "123456"})
check("注册 bob", r.status_code == 200)
r = post("/api/register", {"username": "alice", "password": "654321"})
check("重复注册被拒", r.status_code == 409)
r = post("/api/register", {"username": "a", "password": "123456"})
check("非法用户名被拒", r.status_code == 400)
r = post("/api/login", {"username": "alice", "password": "wrong"})
check("错误密码被拒", r.status_code == 401)
r = post("/api/login", {"username": "alice", "password": "123456"})
check("alice 登录", r.status_code == 200)
ta = r.json()["token"]
r = post("/api/login", {"username": "bob", "password": "123456"})
tb = r.json()["token"]
check("bob 登录", r.status_code == 200)

# 3. 匿名访问被拒
r = get("/api/status")
check("匿名被拒(401)", r.status_code == 401)
r = get("/api/status", token="deadbeef")
check("伪令牌被拒(401)", r.status_code == 401)

# 4. alice 启动任务（打向 mock）
r = post("/api/start", cfg, ta)
check("alice 启动任务", r.status_code == 200, r.text[:200])
missing = dict(cfg)
missing["cookie"] = ""
r = post("/api/start", missing, ta)
check("缺 cookie 被拒(400)", r.status_code == 400)

time.sleep(2.5)
st = get("/api/status", token=ta).json()
check("alice 运行中", st["running"] is True)
check("alice 已有请求次数", st["stats"]["attempts"] >= 1, str(st["stats"]))
logr = get("/api/logs", token=ta).json()["logs"]
check("alice 有日志", len(logr) >= 1)
check("alice 日志不含 cookie", all("TESTCOOKIE" not in l["msg"] for l in logr))

# 5. 用户隔离
stb = get("/api/status", token=tb).json()
check("bob 未运行", stb["running"] is False)
check("bob 无日志", len(get("/api/logs", token=tb).json()["logs"]) == 0)

# 6. 配置保存/回读
r = post("/api/config", cfg, ta)
check("alice 保存配置", r.status_code == 200)
gc = get("/api/config", token=ta).json()
check("配置回读一致", gc.get("jxb_ids") == "mock_jxb_abc")
sc = get("/api/status", token=ta).json()["config"]
check("status 不回传 cookie", "cookie" not in sc)

# 7. 停止
r = post("/api/stop", {}, ta)
check("alice 停止任务", r.status_code == 200)
time.sleep(1.5)
st = get("/api/status", token=ta).json()
check("已停止", st["running"] is False)
check("成功计数为 0(模拟一直失败)", st["stats"]["success"] == 0)

# 8. me
me = get("/api/me", token=ta).json()
check("me 返回用户数", me["users"] == 2, str(me))

print("\n" + ("部分用例失败 ❌ 请查看上方 FAIL 项" if FAILED else "全部用例通过 ✅"))
sys.exit(1 if FAILED else 0)