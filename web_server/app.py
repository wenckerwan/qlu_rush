# -*- coding: utf-8 -*-
"""
QLU 抢课服务器 —— 多用户版（FastAPI 单文件）
=============================================
把「教务抢课脚本.py」的发包逻辑做成常驻 Web 服务：
  - 多用户：注册/登录，每人独立保存 Cookie、课程参数、任务与日志（互不可见）
  - 网页控制台：一键开始/停止/测试连接，实时日志 + 统计
  - 策略：并发、间隔（带抖动）、成功即停、定时启动、每轮查询课程状态

运行：
  python app.py                     # 默认 0.0.0.0:8000
  python app.py --port 9000 --open  # 指定端口并自动打开浏览器

环境变量：
  QLU_DATA  数据目录（用户、配置），默认 ./data
Docker 部署见 Dockerfile / README.md
"""
import argparse
import json
import os
import random
import re
import secrets
import sys
import threading
import time
import webbrowser
from datetime import datetime, timedelta
from hashlib import pbkdf2_hmac
from pathlib import Path

import requests
import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse

# ---------------------------------------------------------------- 路径与常量
BASE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
STATIC_DIR = BASE_DIR / "static"
DATA_DIR = Path(os.environ.get("QLU_DATA", str(BASE_DIR / "data")))
USERS_FILE = DATA_DIR / "users.json"
CONFIGS_DIR = DATA_DIR / "configs"

MAX_LOGS = 1000          # 每用户内存日志条数上限
MAX_CONCURRENCY = 8      # 并发上限（防止把教务系统打崩）
TOKEN_TTL_DAYS = 30      # 登录令牌有效期
REQUIRED = ["base_url", "cookie", "jxb_ids", "kch_id"]

app = FastAPI(title="QLU 抢课服务器", docs_url=None, redoc_url=None)

# 全局状态（带锁）：令牌表 + 每用户运行状态
_lock = threading.RLock()
_tokens = {}             # token -> {"user": str, "exp": datetime}
_users_state = {}        # username -> UserState


# ---------------------------------------------------------------- 工具函数
def now_str() -> str:
    return datetime.now().strftime("%H:%M:%S")


def load_users() -> dict:
    try:
        if USERS_FILE.exists():
            return json.loads(USERS_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def save_users(users: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    USERS_FILE.write_text(json.dumps(users, ensure_ascii=False, indent=2), encoding="utf-8")


def hash_pw(pw: str, salt: str) -> str:
    return pbkdf2_hmac("sha256", pw.encode("utf-8"), salt.encode("utf-8"), 200_000).hex()


def user_log(us: "UserState", level: str, msg: str) -> None:
    """写入某用户的日志环（内存），并同时打服务端日志。"""
    with _lock:
        us.log_ids += 1
        us.logs.append({"id": us.log_ids, "ts": now_str(), "level": level, "msg": str(msg)})
        if len(us.logs) > MAX_LOGS:
            us.logs = us.logs[-MAX_LOGS:]
    print(f"[{us.username}] {now_str()} [{level.upper()}] {msg}")


def _to_int(v, default: int, lo: int, hi: int) -> int:
    try:
        n = int(v)
    except (TypeError, ValueError):
        n = default
    return max(lo, min(n, hi))


def _to_bool(v, default: bool) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on")
    return default


def _normalize_config(cfg: dict, raise_on_missing: bool = False) -> dict:
    """清洗前端传来的配置，固定键与取值范围。"""
    base_url = str(cfg.get("base_url") or "https://jw.qlu.edu.cn").strip().rstrip("/")
    # Exact HTTPS origin only: rejects credentials, ports, queries and fragments
    # before any URL concatenation or network request can occur.
    if base_url != "https://jw.qlu.edu.cn":
        raise HTTPException(400, "仅允许连接 https://jw.qlu.edu.cn")
    c = {
        "base_url": base_url,
        "cookie": str(cfg.get("cookie") or "").strip(),
        "jxb_ids": str(cfg.get("jxb_ids") or "").strip(),
        "kch_id": str(cfg.get("kch_id") or "").strip(),
        "kcmc": str(cfg.get("kcmc") or "").strip(),
        "rwlx": str(cfg.get("rwlx") or "2").strip(),
        "rlkz": str(cfg.get("rlkz") or "0").strip(),
        "rlzlkz": str(cfg.get("rlzlkz") or "1").strip(),
        "sxbj": str(cfg.get("sxbj") or "1").strip(),
        "xxkbj": str(cfg.get("xxkbj") or "0").strip(),
        "qz": str(cfg.get("qz") or "0").strip(),
        "cxbj": str(cfg.get("cxbj") or "0").strip(),
        "xkkz_id": str(cfg.get("xkkz_id") or "").strip(),
        "njdm_id": str(cfg.get("njdm_id") or "").strip(),
        "zyh_id": str(cfg.get("zyh_id") or "").strip(),
        "kklxdm": str(cfg.get("kklxdm") or "").strip(),
        "xklc": str(cfg.get("xklc") or "1").strip(),
        "xkxnm": str(cfg.get("xkxnm") or "").strip(),
        "xkxqm": str(cfg.get("xkxqm") or "").strip(),
        "bj": str(cfg.get("bj") or "7").strip(),
        "start_at": str(cfg.get("start_at") or "").strip(),
        "interval_ms": _to_int(cfg.get("interval_ms"), 100, 30, 600000),
        "concurrency": _to_int(cfg.get("concurrency"), 1, 1, MAX_CONCURRENCY),
        "timeout": _to_int(cfg.get("timeout"), 10, 3, 30),
        "stop_on_success": _to_bool(cfg.get("stop_on_success"), True),
        "query_each_round": _to_bool(cfg.get("query_each_round"), False),
    }
    if raise_on_missing:
        missing = [k for k in REQUIRED if not c.get(k)]
        if missing:
            raise HTTPException(400, "缺少必填项: " + ", ".join(missing))
    return c


def _select_payload(cfg: dict) -> dict:
    return {k: cfg[k] for k in (
        "jxb_ids", "kch_id", "kcmc", "rwlx", "rlkz", "rlzlkz", "sxbj",
        "xxkbj", "qz", "cxbj", "xkkz_id", "njdm_id", "zyh_id",
        "kklxdm", "xklc", "xkxnm", "xkxqm")}


def _query_payload(cfg: dict) -> dict:
    return {k: cfg[k] for k in ("jxb_ids", "xkxnm", "xkxqm", "bj", "kch_id", "njdm_id", "zyh_id", "kklxdm")}


def _seconds_until(s: str) -> float:
    """解析 'YYYY-MM-DD HH:MM:SS' 或 'HH:MM:SS'（今天）；已过期返回 0。"""
    s = (s or "").strip()
    if not s:
        return 0.0
    now = datetime.now()
    try:
        dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        try:
            dt = datetime.strptime(s, "%H:%M:%S").replace(year=now.year, month=now.month, day=now.day)
        except ValueError:
            return 0.0
    return max(0.0, (dt - now).total_seconds())


def is_success(obj) -> bool:
    """按正方教务常见返回结构启发式判断是否选课成功。"""
    if not isinstance(obj, dict):
        return False
    if obj.get("success") in (True, "true", 1, "1", "True"):
        return True
    if str(obj.get("flag", "")).strip().lower() in ("1", "true"):
        return True
    msg = "".join(str(obj.get(k, "")) for k in ("message", "msg", "desc", "descZh", "errorMessage"))
    if any(x in msg for x in ("失败", "错误", "已满", "不能", "超限", "无权限", "请先登录", "登录")):
        return False
    if any(x in msg for x in ("成功", "已选", "选上")):
        return True
    return False


class CourseSession(requests.Session):
    def request(self, method, url, **kwargs):
        kwargs["allow_redirects"] = False
        return super().request(method, url, **kwargs)


def _build_session(cfg: dict) -> requests.Session:
    s = CourseSession()
    s.trust_env = False
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
        "Referer": cfg["base_url"] + "/jwglxt/xsxk/zzxkyzb_cxZzxkYzbIndex.html?gnmkdm=N253512",
        "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
        "Cookie": "JSESSIONID=" + cfg["cookie"],
    })
    return s


# ---------------------------------------------------------------- 抢课引擎（每用户一个实例）
class Grabber:
    def __init__(self, username: str):
        self.username = username
        self.running = False
        self._stop = False
        self.workers: list = []
        self.stats = {"attempts": 0, "success": 0, "error": 0,
                      "started_at": None, "stopped_at": None, "last": None}

    # -- 对外 --
    def start(self, cfg: dict, us: "UserState") -> None:
        validated = _normalize_config(cfg, raise_on_missing=True)
        with _lock:
            if self.running:
                raise HTTPException(400, "任务已在运行中，请先停止")
            self._cfg = validated
            self._us = us
            self.running = True
            self._stop = False
            self.stats = {"attempts": 0, "success": 0, "error": 0,
                          "started_at": now_str(), "stopped_at": None, "last": None}
            self.workers = []
        n = max(1, min(validated["concurrency"], MAX_CONCURRENCY))
        for i in range(n):
            t = threading.Thread(target=self._worker, args=(i, validated, us), daemon=True)
            self.workers.append(t)
            t.start()
        user_log(us, "info", f"任务已启动：并发 {n}，间隔 {validated['interval_ms']}ms，成功即停={validated['stop_on_success']}")

    def stop(self) -> None:
        with _lock:
            if not self.running:
                return
            us = getattr(self, "_us", None)
            self._stop = True
            self.running = False
            self.stats["stopped_at"] = now_str()
        if us is not None:
            user_log(us, "warn", "停止指令已发送，等待进行中的请求结束...")

    # -- 内部 --
    def _worker(self, idx: int, cfg: dict, us: "UserState") -> None:
        s = _build_session(cfg)
        base = cfg["base_url"].rstrip("/")
        select_url = base + "/jwglxt/xsxk/zzxkyzbjk_xkBcZyZzxkYzb.html?gnmkdm=N253512"
        query_url = base + "/jwglxt/xsxk/zzxkyzb_cxXkTitleMsg.html?gnmkdm=N253512"
        data = _select_payload(cfg)
        qdata = _query_payload(cfg)

        delay = _seconds_until(cfg.get("start_at", ""))
        if delay > 0:
            user_log(us, "info", f"worker{idx}: 等待定时启动 {cfg.get('start_at')}（{int(delay)} 秒后）")
            waited, step = 0.0, 0.5
            while waited < delay:
                if self._stop:
                    return
                time.sleep(step)
                waited += step
        user_log(us, "info", f"worker{idx}: 开始发送请求")

        while self.running and not self._stop:
            got = False
            try:
                r = s.post(select_url, data=data, timeout=min(cfg["timeout"], 15))
                got = self._handle_select(r, us, idx)
            except Exception as e:
                with _lock:
                    self.stats["error"] += 1
                    self.stats["last"] = {"ts": now_str(), "code": 0, "body": f"异常: {e}"}
                user_log(us, "error", f"worker{idx}: 选课请求异常 {e}")

            if cfg.get("query_each_round"):
                try:
                    r2 = s.post(query_url, data=qdata, timeout=min(cfg["timeout"], 15))
                    self._handle_query(r2, us, idx)
                except Exception as e:
                    user_log(us, "warn", f"worker{idx}: 状态查询异常 {e}")

            if got and cfg.get("stop_on_success"):
                user_log(us, "success", "检测到选课成功 ✅ 自动停止")
                self.stop()
                break
            time.sleep(max(0.03, cfg["interval_ms"] / 1000.0) * random.uniform(0.9, 1.15))
        user_log(us, "info", f"worker{idx}: 已退出")

    def _handle_select(self, r: requests.Response, us: "UserState", idx: int) -> bool:
        try:
            j = r.json()
            body = json.dumps(j, ensure_ascii=False)[:200]
        except Exception:
            j = None
            body = r.text[:200]
        ok = is_success(j)
        with _lock:
            self.stats["attempts"] += 1
            self.stats["last"] = {"ts": now_str(), "code": r.status_code, "body": body}
            if ok:
                self.stats["success"] += 1
        user_log(us, "success" if ok else "info",
                 f"worker{idx}: 选课响应 HTTP {r.status_code} {body}{' ✅' if ok else ''}")
        return ok

    def _handle_query(self, r: requests.Response, us: "UserState", idx: int) -> None:
        try:
            j = r.json()
            title = ""
            for key in ("titleMsg", "title", "msg"):
                if isinstance(j.get(key), str) and j[key]:
                    title = j[key]
                    break
            body = f"{title} {json.dumps(j, ensure_ascii=False)[:150]}".strip()
        except Exception:
            body = r.text[:150]
        user_log(us, "info", f"worker{idx}: 状态查询 HTTP {r.status_code} {body}")


# ---------------------------------------------------------------- 用户状态
class UserState:
    def __init__(self, username: str):
        self.username = username
        self.config: dict = {}
        self.grabber = Grabber(username)
        self.logs: list = []
        self.log_ids = 0


def get_state(username: str) -> UserState:
    with _lock:
        st = _users_state.get(username)
        if st is None:
            st = UserState(username)
            f = CONFIGS_DIR / f"{username}.json"
            if f.exists():
                try:
                    st.config = _normalize_config(json.loads(f.read_text(encoding="utf-8")))
                except Exception:
                    pass
            _users_state[username] = st
        return st


def save_user_config(st: UserState, cfg: dict) -> None:
    with _lock:
        st.config = cfg
        CONFIGS_DIR.mkdir(parents=True, exist_ok=True)
        (CONFIGS_DIR / f"{st.username}.json").write_text(
            json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------- 认证
def get_user(authorization: str = Header(default="")) -> str:
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "未登录")
    token = authorization[7:].strip()
    with _lock:
        rec = _tokens.get(token)
        if not rec or rec["exp"] < datetime.now():
            raise HTTPException(401, "登录已过期，请重新登录")
        return rec["user"]


def _extract_creds(body: dict):
    return (body or {}).get("username", ""), (body or {}).get("password", "")


# ---------------------------------------------------------------- API
@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.post("/api/register")
def api_register(body: dict):
    if os.environ.get("QLU_ALLOW_REGISTRATION") != "1":
        raise HTTPException(403, "注册已关闭，请联系管理员")
    username, password = _extract_creds(body)
    username = (username or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_]{2,20}", username):
        raise HTTPException(400, "用户名需为 2-20 位字母/数字/下划线")
    if len(password or "") < 6:
        raise HTTPException(400, "密码至少 6 位")
    with _lock:
        users = load_users()
        if username in users:
            raise HTTPException(409, "用户名已存在")
        salt = secrets.token_hex(16)
        users[username] = {"salt": salt, "hash": hash_pw(password, salt), "created": now_str()}
        save_users(users)
        _users_state[username] = UserState(username)
    return {"ok": True}


@app.post("/api/login")
def api_login(body: dict):
    username, password = _extract_creds(body)
    name = (username or "").strip()
    users = load_users()
    rec = users.get(name)
    if not rec or not secrets.compare_digest(rec["hash"], hash_pw(password or "", rec["salt"])):
        raise HTTPException(401, "用户名或密码错误")
    token = secrets.token_hex(32)
    with _lock:
        _tokens[token] = {"user": name, "exp": datetime.now() + timedelta(days=TOKEN_TTL_DAYS)}
        get_state(name)
    return {"ok": True, "token": token, "username": name}


@app.post("/api/logout")
def api_logout(authorization: str = Header(default="")):
    if authorization.startswith("Bearer "):
        with _lock:
            _tokens.pop(authorization[7:].strip(), None)
    return {"ok": True}


@app.get("/api/me")
def api_me(user: str = Depends(get_user)):
    with _lock:
        users = list(load_users().keys())
        running = [u for u, st in _users_state.items() if st.grabber.running]
    return {"username": user, "users": len(users), "user_list": users, "running_users": running}


@app.get("/api/config")
def api_get_config(user: str = Depends(get_user)):
    st = get_state(user)
    with _lock:
        return dict(st.config)


@app.post("/api/config")
def api_save_config(body: dict, user: str = Depends(get_user)):
    st = get_state(user)
    cfg = _normalize_config(body)
    save_user_config(st, cfg)
    return {"ok": True}


@app.post("/api/start")
def api_start(body: dict, user: str = Depends(get_user)):
    st = get_state(user)
    _normalize_config(body, raise_on_missing=True)
    save_user_config(st, _normalize_config(body))
    st.grabber.start(_normalize_config(body), st)
    return {"ok": True}


@app.post("/api/stop")
def api_stop(user: str = Depends(get_user)):
    st = get_state(user)
    with _lock:
        g = st.grabber
    if not g.running:
        raise HTTPException(400, "任务未在运行")
    g.stop()
    return {"ok": True}


@app.get("/api/status")
def api_status(user: str = Depends(get_user)):
    st = get_state(user)
    with _lock:
        g = st.grabber
        cfg = {k: v for k, v in st.config.items()}
        cfg.pop("cookie", None)  # 不回传完整 cookie，避免响应里明文
        return {"running": g.running, "stats": dict(g.stats), "config": cfg}


@app.get("/api/logs")
def api_logs(after: int = 0, user: str = Depends(get_user)):
    st = get_state(user)
    with _lock:
        lst = [l for l in st.logs if l["id"] > after]
    return {"logs": lst}


@app.post("/api/test")
def api_test(body: dict, user: str = Depends(get_user)):
    """发一次选课请求，验证 cookie 与参数是否有效（不影响任务状态）。"""
    st = get_state(user)
    cfg = _normalize_config(body, raise_on_missing=True)
    if st.grabber.running:
        raise HTTPException(400, "任务运行中，请先停止再测试")
    user_log(st, "info", "测试连接：发送一次选课请求 ...")
    s = _build_session(cfg)
    url = cfg["base_url"] + "/jwglxt/xsxk/zzxkyzbjk_xkBcZyZzxkYzb.html?gnmkdm=N253512"
    try:
        r = s.post(url, data=_select_payload(cfg), timeout=min(cfg["timeout"], 15))
        try:
            j = r.json()
            body_s = json.dumps(j, ensure_ascii=False)[:400]
        except Exception:
            j = None
            body_s = r.text[:400]
        ok = is_success(j)
        user_log(st, "success" if ok else "warn",
                 f"测试完成：HTTP {r.status_code}，判定「{'成功' if ok else '未成功'}」")
        return {"ok": True, "status_code": r.status_code, "success_guess": ok, "body": body_s}
    except Exception as e:
        user_log(st, "error", f"测试失败：{e}")
        return {"ok": False, "error": str(e)}


# ---------------------------------------------------------------- 入口
def main():
    ap = argparse.ArgumentParser(description="QLU 抢课服务器（多用户版）")
    ap.add_argument("--host", default="0.0.0.0", help="监听地址（默认 0.0.0.0）")
    ap.add_argument("--port", type=int, default=8000, help="监听端口（默认 8000）")
    ap.add_argument("--open", action="store_true", help="启动后自动打开浏览器")
    args = ap.parse_args()

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    existing = load_users()
    if existing:
        print(f"已加载 {len(existing)} 个用户")
    else:
        print("尚无用户，启动后在网页上注册第一个账号")

    if args.open or getattr(sys, "frozen", False):
        threading.Timer(1.0, lambda: webbrowser.open(f"http://127.0.0.1:{args.port}")).start()

    print(f"服务器启动: http://{args.host}:{args.port}   数据目录: {DATA_DIR}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
