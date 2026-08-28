# -*- coding: utf-8 -*-
"""验证 stop_on_success：任务应检测到成功后自动停止。"""
import sys
import time

import requests

B = "http://127.0.0.1:8123"
r = requests.post(B + "/api/login", json={"username": "alice", "password": "123456"}, timeout=10)
assert r.status_code == 200, r.text
ta = r.json()["token"]
H = {"Authorization": "Bearer " + ta}

cfg = {
    "base_url": "http://127.0.0.1:8125", "cookie": "TESTCOOKIE",
    "jxb_ids": "mock_jxb_ok", "kch_id": "K001", "kcmc": "(测试)秒选课",
    "xkkz_id": "X1", "njdm_id": "2022", "zyh_id": "Z1", "kklxdm": "10",
    "xkxnm": "2025", "xkxqm": "3",
    "interval_ms": 60, "concurrency": 1, "timeout": 5,
    "stop_on_success": True, "query_each_round": False, "start_at": "",
}
r = requests.post(B + "/api/start", json=cfg, headers=H, timeout=10)
assert r.status_code == 200, r.text

stopped = False
last = None
for _ in range(30):  # 最多等 15s
    time.sleep(0.5)
    st = requests.get(B + "/api/status", headers=H, timeout=10).json()
    last = st
    if not st["running"] and st["stats"]["success"] >= 1:
        stopped = True
        break
assert stopped, f"未自动停止: {last}"
print(f"[PASS] 成功即停: attempts={last['stats']['attempts']} success={last['stats']['success']}")
sys.exit(0)