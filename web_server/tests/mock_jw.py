# -*- coding: utf-8 -*-
"""模拟正方教务选课接口，用于本地冒烟测试（绝不请求真实教务）。"""
import json
from http.server import BaseHTTPRequestHandler, HTTPServer

HITS = {"select": 0, "query": 0}
PORT = 8124


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        ln = int(self.headers.get("Content-Length", 0))
        self.rfile.read(ln)
        if "cxXkTitleMsg" in self.path:
            HITS["query"] += 1
            out = {"success": True, "titleMsg": "教室(第1-2周) 48/60 人"}
        else:
            HITS["select"] += 1
            out = {"success": False, "message": "模拟教务：暂无剩余名额"}
        body = json.dumps(out, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json;charset=UTF-8")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    print(f"mock jw listening on 127.0.0.1:{PORT}")
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()